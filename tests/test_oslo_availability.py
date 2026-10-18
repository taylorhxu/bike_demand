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
