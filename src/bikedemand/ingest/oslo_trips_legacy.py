"""Download Oslo City Bike legacy trip files (April 2016 - December 2018), map station IDs, validate.

The legacy files use a different, much thinner format than the April 2019+ files. The publisher
documents four variables per trip, `start_station_id`, `start_time`, `end_station_id` and `end_time`, with
no duration, station names or coordinates. Stations have different IDs from the current system; two
companion files (legacy station locations, and a legacy-to-new ID mapping) link them.

Two things are *not* documented and are handled defensively here:

1. **Exact headers** of the trip, location and mapping files. Columns are resolved through alias lists
   (see `TRIP_ALIASES` etc.), and an unrecognised header fails loudly, listing what the file contains.
2. **Time zone of the timestamps.** If every timestamp carries a UTC offset, it is used. If none do, they
   are read as Europe/Oslo wall-clock time (the operator's local time); the report records which basis
   was used, and `closed_hours_profile` lets the real-data check test that assumption.

Output layout (git-ignored):
    data/raw/oslo_trips_legacy/{year}/{month}.csv.zip
    data/raw/oslo_trips_legacy/legacy_station_locations.csv
    data/raw/oslo_trips_legacy/legacy_new_station_id_mapping.csv
    data/clean/oslo_trips_legacy/year={year}/month={month}/trips.parquet
    data/quarantine/oslo_trips_legacy/{year}-{month}.parquet

Usage:
    python -m bikedemand.ingest.oslo_trips_legacy --start 2016-04 --end 2018-12 [--summary report.md]
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import zipfile
from pathlib import Path

import httpx
import polars as pl

from bikedemand import DATA_DIR
from bikedemand.contracts import Contract

log = logging.getLogger(__name__)

BASE_URL = "https://data-legacy.urbansharing.com"
TRIPS_URL_TEMPLATE = BASE_URL + "/oslobysykkel.no/{year}/{month:02d}.csv.zip"
LOCATIONS_URL = BASE_URL + "/legacy_station_locations.csv"
MAPPING_URL = BASE_URL + "/legacy_new_station_id_mapping.csv"

LOCAL_TZ = "Europe/Oslo"
SHORT_TRIP_SECONDS = 60

# Months listed on https://oslobysykkel.no/en/apne-data/historisk (checked 1 Nov 2026). The scheme was
# closed in winter: no January-March in 2016 and 2017, no January-February in 2018.
PUBLISHED_MONTHS: frozenset[tuple[int, int]] = frozenset(
    [(2016, m) for m in range(4, 13)] + [(2017, m) for m in range(4, 13)] + [(2018, m) for m in range(3, 13)]
)

# Canonical name -> accepted header spellings (compared after lower-casing and replacing spaces with "_").
TRIP_ALIASES = {
    "start_station_legacy_id": ["start_station_id", "start_station"],
    "start_time": ["start_time", "started_at"],
    "end_station_legacy_id": ["end_station_id", "end_station"],
    "end_time": ["end_time", "ended_at"],
}
MAPPING_ALIASES = {
    "legacy_id": ["legacy_id", "legacy_station_id", "old_id", "old_station_id"],
    "new_id": ["new_id", "new_station_id"],
}
LOCATION_ALIASES = {
    # The real file (checked in CI, 1 Nov 2026) has legacy_id, latitude, longitude and no station name.
    "legacy_id": ["legacy_id", "legacy_station_id", "station_id", "id"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "lng"],
}

_ISO_RE = r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"
_OFFSET_RE = r"(Z|[+-]\d{2}:?\d{2})$"
# Above this share of unparseable timestamps, fail instead of quarantining: the format is not understood.
MAX_UNPARSEABLE_SHARE = 0.01
MIN_UNPARSEABLE_ROWS = 10


def months(start: str, end: str) -> list[tuple[int, int]]:
    """Published legacy months between two inclusive 'YYYY-MM' strings (winter gaps skipped)."""
    s, e = (tuple(map(int, x.split("-"))) for x in (start, end))
    if s < min(PUBLISHED_MONTHS) or e > max(PUBLISHED_MONTHS):
        raise ValueError("Legacy data covers 2016-04 to 2018-12; use oslo_trips for 2019-04 onwards")
    return sorted(ym for ym in PUBLISHED_MONTHS if s <= ym <= e)


def _normalise(header: str) -> str:
    return re.sub(r"\s+", "_", header.strip().lstrip("\ufeff").lower())


def resolve_columns(df: pl.DataFrame, aliases: dict[str, list[str]], what: str) -> pl.DataFrame:
    """Rename columns to canonical names via `aliases`; keep only those. Fail listing actual headers."""
    by_norm = {_normalise(c): c for c in df.columns}
    rename, missing = {}, []
    for canonical, options in aliases.items():
        found = next((by_norm[o] for o in options if o in by_norm), None)
        if found is None:
            missing.append(canonical)
        else:
            rename[found] = canonical
    if missing:
        raise ValueError(f"{what}: cannot find {missing}; file has columns {df.columns}")
    return df.select(list(rename)).rename(rename)


def read_zipped_csv(zip_bytes: bytes) -> bytes:
    """Return the single CSV inside a monthly legacy zip."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv") and not n.startswith("__MACOSX")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in zip, found {zf.namelist()}")
        return zf.read(members[0])


def _parse_timestamps(df: pl.DataFrame, cols: list[str]) -> tuple[pl.DataFrame, str]:
    """Parse timestamp strings to UTC. Returns the frame and the basis used ('offset' or 'assumed_local')."""
    values = pl.concat([df[c].drop_nulls() for c in cols])
    has_offset = values.str.contains(_OFFSET_RE)
    if has_offset.all() and values.len():
        exprs = {c: pl.col(c).str.to_datetime(time_zone="UTC", strict=False) for c in cols}
        return df.with_columns(**exprs), "offset"
    if has_offset.any():
        raise ValueError(f"mixed timestamps: {int(has_offset.sum())} of {values.len()} carry a UTC offset")

    def local(c: str, ambiguous: str) -> pl.Expr:
        # Spring-forward times do not exist locally; they become null and are quarantined.
        return (
            pl.col(c)
            .str.to_datetime(strict=False)
            .dt.replace_time_zone(LOCAL_TZ, ambiguous=ambiguous, non_existent="null")
        )

    # Autumn fall-back hour is ambiguous: take the earlier (summer-time) reading, and flag it.
    flags = {
        f"flag_ambiguous_{c}": local(c, "earliest").is_not_null() & local(c, "null").is_null() for c in cols
    }
    exprs = {c: local(c, "earliest").dt.convert_time_zone("UTC") for c in cols}
    return df.with_columns(**flags).with_columns(**exprs), "assumed_local"


def _check_parse_rate(raw: pl.DataFrame, parsed: pl.DataFrame) -> None:
    """Raise if many timestamps failed to parse, showing examples (a few null rows are just quarantined)."""
    bad = parsed["start_time"].is_null() | parsed["end_time"].is_null()
    n_bad = int(bad.sum())
    if n_bad > MIN_UNPARSEABLE_ROWS and n_bad / parsed.height > MAX_UNPARSEABLE_SHARE:
        examples = raw.filter(bad).head(3).select("start_time", "end_time").rows()
        raise ValueError(f"{n_bad} of {parsed.height} rows have unparseable timestamps, e.g. {examples}")


def parse(csv_bytes: bytes) -> tuple[pl.DataFrame, str]:
    """Parse a legacy monthly CSV. Returns (frame, timestamp basis)."""
    raw = pl.read_csv(io.BytesIO(csv_bytes), infer_schema=False)
    df = resolve_columns(raw, TRIP_ALIASES, "legacy trips")
    df = df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)
    # "2018-06-01 07:00:00 +0200" -> "2018-06-01 07:00:00+0200" (Polars will not parse the spaced form).
    df = df.with_columns(pl.col("start_time", "end_time").str.replace(r"\s+([+-]\d{2}:?\d{2})$", "$1"))
    # Only ISO-style dates are accepted, so a day/month order is never guessed.
    iso = df.with_columns(
        pl.when(pl.col(c).str.contains(_ISO_RE)).then(pl.col(c)).otherwise(None).alias(c)
        for c in ("start_time", "end_time")
    )
    parsed, basis = _parse_timestamps(iso, ["start_time", "end_time"])
    _check_parse_rate(df, parsed)
    df = parsed.rename({"start_time": "started_at", "end_time": "ended_at"})
    for c in ("start_time", "end_time"):
        flag = f"flag_ambiguous_{c}"
        if flag not in df.columns:
            df = df.with_columns(pl.lit(False).alias(flag))
    df = df.with_columns(
        flag_ambiguous_local_time=pl.col("flag_ambiguous_start_time") | pl.col("flag_ambiguous_end_time")
    ).drop("flag_ambiguous_start_time", "flag_ambiguous_end_time")
    return (
        df.with_columns(
            duration=(pl.col("ended_at") - pl.col("started_at")).dt.total_seconds().cast(pl.Int64),
            started_at_local=pl.col("started_at").dt.convert_time_zone(LOCAL_TZ),
            ended_at_local=pl.col("ended_at").dt.convert_time_zone(LOCAL_TZ),
        ),
        basis,
    )


def load_station_mapping(mapping_csv: bytes) -> tuple[pl.DataFrame, list[str]]:
    """Legacy -> new station ID table, one row per legacy ID.

    Legacy IDs that map to more than one new ID are dropped (treated as unmapped) and returned separately,
    so an ambiguous mapping can never silently pick one.
    """
    raw = pl.read_csv(io.BytesIO(mapping_csv), infer_schema=False)
    m = resolve_columns(raw, MAPPING_ALIASES, "station ID mapping")
    m = m.with_columns(pl.all().str.strip_chars()).filter(
        pl.col("legacy_id").is_not_null() & (pl.col("legacy_id") != "")
    )
    m = m.with_columns(pl.when(pl.col("new_id") == "").then(None).otherwise(pl.col("new_id")).alias("new_id"))
    m = m.unique()
    counts = m.group_by("legacy_id").agg(n=pl.col("new_id").n_unique())
    ambiguous = sorted(counts.filter(pl.col("n") > 1)["legacy_id"].to_list())
    return m.filter(~pl.col("legacy_id").is_in(ambiguous)), ambiguous


def load_station_locations(locations_csv: bytes) -> pl.DataFrame:
    """Legacy station table: legacy_id, latitude, longitude."""
    raw = pl.read_csv(io.BytesIO(locations_csv), infer_schema=False)
    loc = resolve_columns(raw, LOCATION_ALIASES, "legacy station locations")
    return loc.with_columns(
        pl.col("legacy_id").str.strip_chars(),
        pl.col("latitude", "longitude").str.strip_chars().cast(pl.Float64, strict=False),
    ).unique("legacy_id", keep="first")


def map_stations(trips: pl.DataFrame, mapping: pl.DataFrame) -> pl.DataFrame:
    """Add current-system `start_station_id` / `end_station_id` (null where the mapping has no entry)."""
    lookup = mapping.select("legacy_id", "new_id")
    for side in ("start", "end"):
        trips = trips.join(
            lookup.rename({"legacy_id": f"{side}_station_legacy_id", "new_id": f"{side}_station_id"}),
            on=f"{side}_station_legacy_id",
            how="left",
        )
    return trips


def apply_rules(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        flag_ends_before_start=pl.col("ended_at") < pl.col("started_at"),
        flag_short_trip=pl.col("duration").is_between(0, SHORT_TRIP_SECONDS - 1),
        flag_unmapped_station=pl.col("start_station_id").is_null() | pl.col("end_station_id").is_null(),
    )


def closed_hours_profile(df: pl.DataFrame, closed: tuple[int, int] = (1, 5)) -> dict[str, float]:
    """Share of trip starts in the local night-closure window under two readings of the raw timestamps.

    `as_parsed` is the interpretation used for ingestion; `if_raw_were_utc` re-reads the same wall-clock
    values as if they were UTC. If the scheme was closed overnight, the correct reading should put
    (almost) no starts in the closed window. Only meaningful when the basis was 'assumed_local'.
    """
    lo, hi = closed
    as_parsed = df["started_at_local"].dt.hour()
    raw_as_utc = df["started_at_local"].dt.replace_time_zone("UTC").dt.convert_time_zone(LOCAL_TZ).dt.hour()
    n = max(df.height, 1)
    return {
        "as_parsed": float(as_parsed.is_between(lo, hi - 1).sum()) / n,
        "if_raw_were_utc": float(raw_as_utc.is_between(lo, hi - 1).sum()) / n,
    }


def _download(url: str, path: Path, client: httpx.Client, force: bool = False) -> bytes:
    if path.exists() and not force:
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = client.get(url, timeout=120)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return resp.content


def ingest_month(
    year: int,
    month: int,
    client: httpx.Client,
    mapping: pl.DataFrame,
    data_dir: Path = DATA_DIR,
) -> tuple[dict, pl.DataFrame]:
    """Ingest one month. Returns the report and that month's unmapped legacy stations."""
    contract = Contract.load("oslo_trips_legacy")
    raw_dir = data_dir / "raw" / "oslo_trips_legacy"
    zipped = _download(
        TRIPS_URL_TEMPLATE.format(year=year, month=month),
        raw_dir / str(year) / f"{month:02d}.csv.zip",
        client,
    )
    df, basis = parse(read_zipped_csv(zipped))
    df = apply_rules(map_stations(df, mapping))

    valid, quarantined = contract.split(df)
    contract.enforce(valid)

    out = data_dir / "clean" / "oslo_trips_legacy" / f"year={year}" / f"month={month:02d}" / "trips.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    valid.write_parquet(out)
    if quarantined.height:
        q = data_dir / "quarantine" / "oslo_trips_legacy" / f"{year}-{month:02d}.parquet"
        q.parent.mkdir(parents=True, exist_ok=True)
        quarantined.write_parquet(q)

    closed = closed_hours_profile(valid)
    report = {
        "month": f"{year}-{month:02d}",
        "timestamp_basis": basis,
        "rows": df.height,
        "valid": valid.height,
        "quarantined": quarantined.height,
        "short_trips": int(valid["flag_short_trip"].sum()),
        "ambiguous_local_time": int(valid["flag_ambiguous_local_time"].sum()),
        "unmapped_trips": int(valid["flag_unmapped_station"].sum()),
        "legacy_stations": int(
            pl.concat([valid["start_station_legacy_id"], valid["end_station_legacy_id"]]).n_unique()
        ),
        "starts_01_05_local": round(closed["as_parsed"], 5),
        "starts_01_05_if_utc": round(closed["if_raw_were_utc"], 5),
    }
    log.info(report)
    return report, unmapped_stations(valid)


def unmapped_stations(trips: pl.DataFrame) -> pl.DataFrame:
    """Legacy station IDs with no new ID, by number of trip ends (start or end), most frequent first."""
    ends = pl.concat(
        [
            trips.filter(pl.col(f"{side}_station_id").is_null()).select(
                legacy_id=pl.col(f"{side}_station_legacy_id")
            )
            for side in ("start", "end")
        ]
    )
    return (
        ends.group_by("legacy_id").len("trip_ends").sort(["trip_ends", "legacy_id"], descending=[True, False])
    )


def summary_markdown(
    reports: list[dict],
    ambiguous: list[str],
    mapping: pl.DataFrame,
    locations: pl.DataFrame | None,
    unmapped: pl.DataFrame | None = None,
) -> str:
    lines = ["## Oslo legacy trips: real-data check", ""]
    if reports:
        keys = list(reports[0])
        lines += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
        lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in reports]
    lines += [
        "",
        f"- Mapping rows (unambiguous legacy IDs): {mapping.height}",
        f"- Legacy IDs mapped to more than one new ID (dropped): {len(ambiguous)} {ambiguous[:20]}",
        f"- Legacy IDs mapped to no new ID: {mapping['new_id'].null_count()}",
        f"- Sample mapping rows: {mapping.sort('legacy_id').head(5).rows()}",
    ]
    if unmapped is not None and unmapped.height:
        lines.append(
            f"- Unmapped legacy stations seen in trips: {unmapped.height}; most frequent "
            f"(legacy_id, trip ends): {unmapped.head(15).rows()}"
        )
    if locations is not None:
        unlocated = mapping.join(locations, on="legacy_id", how="anti").height
        lines.append(
            f"- Legacy stations in locations file: {locations.height}; mapped IDs without a "
            f"location: {unlocated}"
        )
    lines += [
        "",
        "`starts_01_05_local` is the share of trips starting 01:00-05:00 Oslo time under the reading used;",
        "`starts_01_05_if_utc` is the same share if the raw timestamps were UTC instead. If the scheme was",
        "closed overnight, the correct reading is the one close to zero.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="First month, YYYY-MM (2016-04 or later)")
    parser.add_argument("--end", required=True, help="Last month, YYYY-MM (2018-12 or earlier)")
    parser.add_argument("--summary", type=Path, help="Write a Markdown report here")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    raw_dir = DATA_DIR / "raw" / "oslo_trips_legacy"
    with httpx.Client(headers={"User-Agent": "taylorhxu-oslobikedemand"}) as client:
        mapping, ambiguous = load_station_mapping(
            _download(MAPPING_URL, raw_dir / "legacy_new_station_id_mapping.csv", client)
        )
        try:
            locations = load_station_locations(
                _download(LOCATIONS_URL, raw_dir / "legacy_station_locations.csv", client)
            )
        except ValueError as exc:  # report, don't block trip ingestion on an unexpected locations header
            log.warning("locations file not used: %s", exc)
            locations = None
        results = [ingest_month(y, m, client, mapping) for y, m in months(args.start, args.end)]

    reports = [r for r, _ in results]
    unmapped = (
        pl.concat([u for _, u in results])
        .group_by("legacy_id")
        .agg(pl.col("trip_ends").sum())
        .sort(["trip_ends", "legacy_id"], descending=[True, False])
    )
    summary = summary_markdown(reports, ambiguous, mapping, locations, unmapped)
    log.info(summary)
    if args.summary:
        args.summary.write_text(summary)


if __name__ == "__main__":
    main()
