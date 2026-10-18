"""Archive Oslo City Bike real-time station availability (GBFS).

Historical availability isn't published, so this script takes snapshots of station_status and
writes each as a small Parquet file. Run it on a schedule (see
.github/workflows/archive-availability.yml) to build a record of empty and full docks over time.

Output layout (git-ignored):
    data/availability/oslo/date={YYYY-MM-DD}/status_{HHMMSS}.parquet
    data/availability/oslo/date={YYYY-MM-DD}/stations.parquet   (station_information, once a day)
"""

from __future__ import annotations

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    snapshot()
