from pathlib import Path

import polars as pl
import pytest

from bikedemand.contracts import Contract
from bikedemand.ingest.oslo_trips import apply_rules, months, parse

FIXTURE = Path(__file__).parent / "fixtures" / "oslo_trips_sample.csv"


@pytest.fixture
def trips() -> pl.DataFrame:
    return apply_rules(parse(FIXTURE.read_bytes()))


def test_parses_timestamps_with_and_without_fractional_seconds(trips):
    assert trips["started_at"].null_count() == 0
    assert trips["started_at"].dtype == pl.Datetime("us", "UTC")


def test_local_time_follows_oslo_daylight_saving(trips):
    # 05:01 UTC on 1 September is 07:01 in Oslo (CEST, UTC+2)
    assert trips["started_at_local"][0].hour == 7


def test_station_ids_stay_strings(trips):
    assert trips["start_station_id"].dtype == pl.String


def test_contract_quarantines_bad_rows_instead_of_failing(trips):
    contract = Contract.load("oslo_trips")
    valid, quarantined = contract.split(trips)
    # Row 4 has 0,0 coordinates.
    assert valid.height == 3
    assert quarantined.height == 1
    assert contract.validate(valid) == []


def test_short_trips_are_kept_and_flagged(trips):
    valid, _ = Contract.load("oslo_trips").split(trips)
    short = valid.filter(pl.col("flag_short_trip"))
    assert short.height == 1
    assert short["duration"][0] == 30


def test_contract_reports_missing_columns(trips):
    errors = Contract.load("oslo_trips").validate(trips.drop("duration"))
    assert any("missing columns" in e for e in errors)


def test_months_is_inclusive_and_crosses_years():
    assert months("2019-11", "2020-02") == [(2019, 11), (2019, 12), (2020, 1), (2020, 2)]


def test_months_rejects_legacy_range():
    with pytest.raises(ValueError):
        months("2018-12", "2019-05")
