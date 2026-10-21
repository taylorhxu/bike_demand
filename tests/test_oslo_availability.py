from datetime import UTC, datetime

import polars as pl

from bikedemand.ingest.oslo_availability import status_frame

# Shape taken from Oslo City Bike's published station_status example.
PAYLOAD = {
    "last_updated": 1540219230,
    "data": {
        "stations": [
            {
                "is_installed": 1,
                "is_renting": 1,
                "num_bikes_available": 7,
                "num_docks_available": 5,
                "last_reported": 1540219230,
                "is_returning": 1,
                "station_id": "175",
            },
            {
                "is_installed": 1,
                "is_renting": 1,
                "num_bikes_available": 0,
                "num_docks_available": 12,
                "last_reported": 1540219230,
                "is_returning": 1,
                "station_id": "47",
            },
        ]
    },
}


def test_status_frame_types_and_snapshot_time():
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    df = status_frame(PAYLOAD, now)
    assert df.height == 2
    assert df["station_id"].dtype == pl.String
    assert df["last_reported"].dtype == pl.Datetime("us", "UTC")
    assert df["snapshot_at"][0] == now


def test_empty_station_is_visible():
    df = status_frame(PAYLOAD, datetime.now(UTC))
    assert df.filter(pl.col("num_bikes_available") == 0).height == 1


def test_bundle_combines_snapshots_across_days(tmp_path):
    from bikedemand.ingest.oslo_availability import bundle

    base = tmp_path / "availability" / "oslo"
    times = [datetime(2026, 10, 20, 23, 55, tzinfo=UTC), datetime(2026, 10, 21, 0, 0, tzinfo=UTC)]
    for t in times:
        day = base / f"date={t:%Y-%m-%d}"
        day.mkdir(parents=True, exist_ok=True)
        status_frame(PAYLOAD, t).write_parquet(day / f"status_{t:%H%M%S}.parquet")

    out = tmp_path / "upload" / "bundle.parquet"
    assert bundle(out, data_dir=tmp_path) == 4
    df = pl.read_parquet(out)
    assert df["snapshot_at"].n_unique() == 2
    assert df["snapshot_at"].is_sorted()


def test_bundle_with_no_snapshots_writes_nothing(tmp_path):
    from bikedemand.ingest.oslo_availability import bundle

    out = tmp_path / "bundle.parquet"
    assert bundle(out, data_dir=tmp_path) == 0
    assert not out.exists()
