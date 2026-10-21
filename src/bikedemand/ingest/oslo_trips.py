"""Download Oslo City Bike monthly trip files, validate them, and write Parquet.

Output layout (git-ignored):
    data/raw/oslo_trips/{year}/{month}.csv
    data/clean/oslo_trips/year={year}/month={month}/trips.parquet
    data/quarantine/oslo_trips/{year}-{month}.parquet   (rows failing the contract)

Usage:
    python -m bikedemand.ingest.oslo_trips --start 2019-04 --end 2026-09
"""

from __future__ import annotations

import argparse
import io
import logging
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from bikedemand import DATA_DIR
from bikedemand.contracts import Contract

log = logging.getLogger(__name__)

URL_TEMPLATE = "https://data.urbansharing.com/oslobysykkel.no/trips/v1/{year}/{month:02d}.csv"
FIRST_MONTH = (2019, 4)
LOCAL_TZ = "Europe/Oslo"
SHORT_TRIP_SECONDS = 60

STRING_COLS = [
    "start_station_id",
    "start_station_name",
    "start_station_description",
    "end_station_id",
    "end_station_name",
    "end_station_description",
]


def months(start: str, end: str) -> list[tuple[int, int]]:
    """Inclusive list of (year, month) between two 'YYYY-MM' strings."""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    if (y, m) < FIRST_MONTH:
        raise ValueError(f"Current-format data starts {FIRST_MONTH[0]}-{FIRST_MONTH[1]:02d}")
    out = []
    while (y, m) <= (ey, em):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def download(year: int, month: int, raw_dir: Path, client: httpx.Client, force: bool = False) -> Path:
    path = raw_dir / str(year) / f"{month:02d}.csv"
    is_current_month = (year, month) == (date.today().year, date.today().month)
    if path.exists() and not force and not is_current_month:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = client.get(URL_TEMPLATE.format(year=year, month=month), timeout=120)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def parse(csv_bytes: bytes) -> pl.DataFrame:
    """Parse a raw monthly CSV into typed columns, plus local-time columns."""
    df = pl.read_csv(
        io.BytesIO(csv_bytes),
        schema_overrides={c: pl.String for c in STRING_COLS},
        try_parse_dates=False,
    )
    ts = {c: pl.col(c).str.to_datetime(time_zone="UTC", strict=False) for c in ("started_at", "ended_at")}
    return df.with_columns(
        **ts,
        duration=pl.col("duration").cast(pl.Int64),
    ).with_columns(
        started_at_local=pl.col("started_at").dt.convert_time_zone(LOCAL_TZ),
        ended_at_local=pl.col("ended_at").dt.convert_time_zone(LOCAL_TZ),
    )


def apply_rules(df: pl.DataFrame) -> pl.DataFrame:
    """Flag rule violations that don't make a row unusable, for reporting."""
    return df.with_columns(
        flag_ends_before_start=pl.col("ended_at") < pl.col("started_at"),
        flag_duration_mismatch=(
            (pl.col("ended_at") - pl.col("started_at")).dt.total_seconds() - pl.col("duration")
        ).abs()
        > 2,
        # Oslo's docs say trips under a minute are excluded, but the files contain some genuine
        # 25-59 s hops between neighbouring stations. Keep them, but flag them.
        flag_short_trip=pl.col("duration") < SHORT_TRIP_SECONDS,
    )


def ingest_month(year: int, month: int, client: httpx.Client, data_dir: Path = DATA_DIR) -> dict:
    contract = Contract.load("oslo_trips")
    raw = download(year, month, data_dir / "raw" / "oslo_trips", client)
    df = apply_rules(parse(raw.read_bytes()))

    valid, quarantined = contract.split(df)
    contract.enforce(valid)

    out = data_dir / "clean" / "oslo_trips" / f"year={year}" / f"month={month:02d}" / "trips.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    valid.write_parquet(out)

    if quarantined.height:
        q = data_dir / "quarantine" / "oslo_trips" / f"{year}-{month:02d}.parquet"
        q.parent.mkdir(parents=True, exist_ok=True)
        quarantined.write_parquet(q)

    report = {
        "month": f"{year}-{month:02d}",
        "rows": df.height,
        "valid": valid.height,
        "quarantined": quarantined.height,
        "ends_before_start": int(valid["flag_ends_before_start"].sum()),
        "duration_mismatch": int(valid["flag_duration_mismatch"].sum()),
        "short_trips": int(valid["flag_short_trip"].sum()),
    }
    log.info(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="First month, YYYY-MM (2019-04 or later)")
    parser.add_argument("--end", required=True, help="Last month, YYYY-MM")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with httpx.Client(headers={"User-Agent": "taylorhxu-oslobikedemand"}) as client:
        for year, month in months(args.start, args.end):
            ingest_month(year, month, client)


if __name__ == "__main__":
    main()
