"""Time-versioned station dimension, built from Oslo trip records.

Stations are the nodes of the demand graph, and they change: new stations open, some close, some are
renamed and some are moved. Every trip carries its start and end station's id, name, description and
coordinates as they were at the time, so the trip files themselves are a history of the network.

How versions are found:

1. Every trip gives two observations (start station at `started_at`, end station at `ended_at`).
2. Per station and UTC day, the most common (name, description, latitude, longitude) is taken as that
   day's state. This absorbs the odd stray row on a day when a station changes.
3. Walking each station's days in order, a new version starts when the name changes or the location
   moves more than `move_threshold_m` metres from the previous day's location. A description change
   alone does not start a version; the version keeps its latest description, and the coordinate pair
   seen on the most trips.

Each version is valid from its first observed day up to (not including) the next version's first day.
The latest version of each station is open-ended (`valid_to` is null); `last_seen` says when the
station last had a trip, so a closed station can be recognised by an old `last_seen`.

Usage:
    python -m bikedemand.stations --start 2019-04 --end 2026-09 [--summary summary.md]

Reads data/clean/oslo_trips (written by bikedemand.ingest.oslo_trips) and writes
data/clean/oslo_stations/stations.parquet.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

import polars as pl

from bikedemand import DATA_DIR
from bikedemand.contracts import Contract
from bikedemand.ingest.oslo_trips import months

log = logging.getLogger(__name__)

MOVE_THRESHOLD_M = 50.0
EARTH_RADIUS_M = 6_371_000.0
ATTRS = ["name", "description", "latitude", "longitude"]


def observations(trips: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    """One row per station appearance: each trip's start and end station, with the time seen."""
    lf = trips.lazy()
    sides = [
        lf.select(
            station_id=pl.col(f"{side}_station_id"),
            name=pl.col(f"{side}_station_name"),
            description=pl.col(f"{side}_station_description"),
            latitude=pl.col(f"{side}_station_latitude"),
            longitude=pl.col(f"{side}_station_longitude"),
            seen_at=pl.col(at),
        )
        for side, at in (("start", "started_at"), ("end", "ended_at"))
    ]
    return pl.concat(sides)


def daily_states(obs: pl.LazyFrame) -> pl.LazyFrame:
    """Most common attributes per station and UTC day, with that day's number of appearances."""
    counted = (
        obs.with_columns(day=pl.col("seen_at").dt.date())
        .group_by("station_id", "day", *ATTRS)
        .agg(n=pl.len())
    )
    return (
        counted.sort(
            ["station_id", "day", "n", "name", "latitude", "longitude"],
            descending=[False, False, True, False, False, False],
            nulls_last=True,
        )
        .group_by("station_id", "day", maintain_order=True)
        .agg(pl.col(ATTRS).first(), appearances=pl.col("n").sum())
    )


def haversine_m(lat1: pl.Expr, lon1: pl.Expr, lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    """Great-circle distance in metres between two coordinate expressions."""
    phi1, phi2 = lat1.radians(), lat2.radians()
    dphi = (lat2 - lat1).radians()
    dlmb = (lon2 - lon1).radians()
    a = (dphi / 2).sin() ** 2 + phi1.cos() * phi2.cos() * (dlmb / 2).sin() ** 2
    return 2 * EARTH_RADIUS_M * a.sqrt().arcsin()


def build(trips: pl.LazyFrame | pl.DataFrame, move_threshold_m: float = MOVE_THRESHOLD_M) -> pl.DataFrame:
    """Build the station dimension: one row per (station_id, version)."""
    days = daily_states(observations(trips)).sort("station_id", "day")

    prev = {c: pl.col(c).shift(1).over("station_id") for c in ("name", "latitude", "longitude")}
    renamed = prev["name"].is_not_null() & (pl.col("name") != prev["name"])
    moved = prev["latitude"].is_not_null() & (
        haversine_m(prev["latitude"], prev["longitude"], pl.col("latitude"), pl.col("longitude"))
        > move_threshold_m
    )
    first_day = pl.col("day").rank("ordinal").over("station_id") == 1

    days = days.with_columns(renamed=renamed, moved=moved, is_first=first_day).with_columns(
        version=(pl.col("is_first") | pl.col("renamed") | pl.col("moved"))
        .cast(pl.Int64)
        .cum_sum()
        .over("station_id"),
        change_reason=pl.when(pl.col("is_first"))
        .then(pl.lit("first_seen"))
        .when(pl.col("renamed") & pl.col("moved"))
        .then(pl.lit("renamed_and_moved"))
        .when(pl.col("renamed"))
        .then(pl.lit("renamed"))
        .when(pl.col("moved"))
        .then(pl.lit("moved"))
        .otherwise(None),
    )

    # Coordinates: the (latitude, longitude) pair with the most appearances in the version, so one
    # jittered day doesn't decide where the node sits. Kept as a pair so lat and lon never mix.
    coords = (
        days.group_by("station_id", "version", "latitude", "longitude")
        .agg(n=pl.col("appearances").sum())
        .sort(
            ["station_id", "version", "n", "latitude", "longitude"],
            descending=[False, False, True, False, False],
        )
        .group_by("station_id", "version", maintain_order=True)
        .agg(pl.col("latitude", "longitude").first())
    )
    versions = (
        days.sort("station_id", "day")
        .group_by("station_id", "version")
        .agg(
            name=pl.col("name").first(),
            # Descriptions are free text that the operator edits; keep the latest one.
            description=pl.col("description").last(),
            change_reason=pl.col("change_reason").drop_nulls().first(),
            first_seen=pl.col("day").min(),
            last_seen=pl.col("day").max(),
            days_seen=pl.len().cast(pl.Int64),
            appearances=pl.col("appearances").sum().cast(pl.Int64),
        )
        .join(coords, on=["station_id", "version"])
    )

    return (
        versions.sort("station_id", "version")
        .with_columns(
            valid_from=pl.col("first_seen"),
            valid_to=pl.col("first_seen").shift(-1).over("station_id"),
        )
        .select(
            "station_id",
            "version",
            "name",
            "description",
            "latitude",
            "longitude",
            "valid_from",
            "valid_to",
            "first_seen",
            "last_seen",
            "days_seen",
            "appearances",
            "change_reason",
        )
        .collect()
    )


def as_of(dim: pl.DataFrame, day: date) -> pl.DataFrame:
    """Stations as they were on `day`: the version whose validity window contains it."""
    return dim.filter(
        (pl.col("valid_from") <= day) & (pl.col("valid_to").is_null() | (pl.col("valid_to") > day))
    )


def summarise(dim: pl.DataFrame) -> dict:
    per_station = dim.group_by("station_id").agg(pl.len().alias("versions"))
    reasons = dim.filter(pl.col("version") > 1)["change_reason"].value_counts(sort=True)
    return {
        "stations": per_station.height,
        "versions": dim.height,
        "stations_with_changes": int((per_station["versions"] > 1).sum()),
        "stations_with_3plus_versions": int((per_station["versions"] >= 3).sum()),
        "changes_by_reason": dict(zip(reasons["change_reason"], reasons["count"], strict=True)),
        "first_day": str(dim["first_seen"].min()),
        "last_day": str(dim["last_seen"].max()),
    }


def summary_markdown(dim: pl.DataFrame, summary: dict, max_rows: int = 40) -> str:
    """Readable report: headline numbers plus every station that has more than one version."""
    lines = ["## Oslo station dimension", "", "| Measure | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    changed_ids = dim.filter(pl.col("version") > 1)["station_id"].unique()
    changed = dim.filter(pl.col("station_id").is_in(changed_ids.implode())).sort("station_id", "version")
    lines += ["", f"### Stations with more than one version (first {max_rows} rows)", ""]
    lines += [
        "| id | v | name | lat | lon | valid_from | valid_to | reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in changed.head(max_rows).iter_rows(named=True):
        lines.append(
            f"| {r['station_id']} | {r['version']} | {r['name']} | {r['latitude']:.6f} | "
            f"{r['longitude']:.6f} | {r['valid_from']} | {r['valid_to'] or ''} | {r['change_reason']} |"
        )
    return "\n".join(lines) + "\n"


def trip_files(start: str, end: str, data_dir: Path = DATA_DIR) -> list[Path]:
    root = data_dir / "clean" / "oslo_trips"
    paths = [root / f"year={y}" / f"month={m:02d}" / "trips.parquet" for y, m in months(start, end)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"run bikedemand.ingest.oslo_trips first; missing {missing[0]}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="First month, YYYY-MM")
    parser.add_argument("--end", required=True, help="Last month, YYYY-MM")
    parser.add_argument("--move-threshold-m", type=float, default=MOVE_THRESHOLD_M)
    parser.add_argument("--summary", type=Path, help="Also append a Markdown report to this file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    trips = pl.scan_parquet(trip_files(args.start, args.end))
    dim = Contract.load("oslo_stations").enforce(build(trips, args.move_threshold_m))

    out = DATA_DIR / "clean" / "oslo_stations" / "stations.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    dim.write_parquet(out)

    summary = summarise(dim)
    log.info(json.dumps(summary))
    if args.summary:
        with args.summary.open("a") as f:
            f.write(summary_markdown(dim, summary))


if __name__ == "__main__":
    main()
