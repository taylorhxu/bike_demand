"""Archive Oslo City Bike real-time station availability (GBFS).

Historical availability isn't published, so this script takes snapshots of station_status and
writes each as a small Parquet file. The workflow in .github/workflows/archive-availability.yml
runs it in a loop and uploads a bundled file per run, building a record of empty and full docks.

Usage:
    python -m bikedemand.ingest.oslo_availability                 # one snapshot
    python -m bikedemand.ingest.oslo_availability bundle --out x.parquet

Output layout (git-ignored):
    data/availability/oslo/date={YYYY-MM-DD}/status_{HHMMSS}.parquet
    data/availability/oslo/date={YYYY-MM-DD}/stations.parquet   (station_information, once a day)
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from bikedemand import DATA_DIR

log = logging.getLogger(__name__)

BASE = "https://gbfs.urbansharing.com/oslobysykkel.no"
# Oslo City Bike asks every client to identify itself as "<name>-<application>".
CLIENT_ID = os.environ.get("OSLO_CLIENT_IDENTIFIER", "taylorhxu-oslobikedemand")


def fetch(feed: str, client: httpx.Client) -> dict:
    resp = client.get(f"{BASE}/{feed}.json", timeout=30)
    resp.raise_for_status()
    return resp.json()


def status_frame(payload: dict, snapshot_at: datetime) -> pl.DataFrame:
    df = pl.DataFrame(payload["data"]["stations"])
    return df.with_columns(
        station_id=pl.col("station_id").cast(pl.String),
        last_reported=pl.from_epoch(pl.col("last_reported"), time_unit="s").dt.replace_time_zone("UTC"),
        feed_last_updated=pl.lit(payload["last_updated"]),
        snapshot_at=pl.lit(snapshot_at),
    )


def stations_frame(payload: dict, snapshot_at: datetime) -> pl.DataFrame:
    df = pl.DataFrame(payload["data"]["stations"])
    return df.with_columns(
        station_id=pl.col("station_id").cast(pl.String),
        snapshot_at=pl.lit(snapshot_at),
    )


def snapshot(data_dir: Path = DATA_DIR, now: datetime | None = None) -> Path:
    now = now or datetime.now(UTC)
    day_dir = data_dir / "availability" / "oslo" / f"date={now:%Y-%m-%d}"
    day_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"Client-Identifier": CLIENT_ID}) as client:
        status = status_frame(fetch("station_status", client), now)
        out = day_dir / f"status_{now:%H%M%S}.parquet"
        status.write_parquet(out)

        stations_path = day_dir / "stations.parquet"
        if not stations_path.exists():
            stations_frame(fetch("station_information", client), now).write_parquet(stations_path)

    log.info("wrote %s stations to %s", status.height, out)
    return out


def bundle(out: Path, data_dir: Path = DATA_DIR, pattern: str = "status_*.parquet") -> int:
    """Concatenate all status snapshots under data_dir into one Parquet file; return row count.

    The archiver workflow takes many snapshots in one run and uploads a single bundled file per
    run, which keeps the release asset count far below GitHub's 1,000-per-release limit.
    """
    files = sorted((data_dir / "availability" / "oslo").glob(f"date=*/{pattern}"))
    if not files:
        return 0
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed").sort(
        "snapshot_at", "station_id"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    return df.height


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Oslo City Bike availability archiver")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("snapshot", help="Take one snapshot (default)")
    b = sub.add_parser("bundle", help="Combine local snapshots into one Parquet file")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--pattern", default="status_*.parquet")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.cmd == "bundle":
        rows = bundle(args.out, pattern=args.pattern)
        log.info("bundled %s rows into %s", rows, args.out)
    else:
        snapshot()


if __name__ == "__main__":
    main()
