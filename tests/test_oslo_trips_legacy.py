import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from bikedemand.contracts import Contract
from bikedemand.ingest.oslo_trips_legacy import (
    apply_rules,
    closed_hours_profile,
    load_station_locations,
    load_station_mapping,
    map_stations,
    months,
    parse,
    read_zipped_csv,
    summary_markdown,
    unmapped_stations,
)

FIXTURES = Path(__file__).parent / "fixtures"
TRIPS = FIXTURES / "oslo_trips_legacy_sample.csv"
MAPPING = FIXTURES / "oslo_legacy_station_mapping_sample.csv"
LOCATIONS = FIXTURES / "oslo_legacy_station_locations_sample.csv"
HEADER = b"start_station_id,start_time,end_station_id,end_time\n"


@pytest.fixture
def mapping() -> pl.DataFrame:
    return load_station_mapping(MAPPING.read_bytes())[0]


@pytest.fixture
def trips(mapping) -> pl.DataFrame:
    df, basis = parse(TRIPS.read_bytes())
    assert basis == "assumed_local"
    return apply_rules(map_stations(df, mapping))


@pytest.fixture
def split(trips):
    return Contract.load("oslo_trips_legacy").split(trips)


def test_naive_timestamps_are_read_as_oslo_local_time(trips):
    # 07:01:12 on 27 October 2018 in Oslo is CEST (UTC+2), so 05:01:12 UTC.
    assert trips["started_at"][0] == datetime(2018, 10, 27, 5, 1, 12, tzinfo=UTC)
    assert trips["started_at_local"][0].hour == 7


def test_timestamps_with_offset_use_the_offset():
    csv = HEADER + b"1,2018-06-01 07:00:00+02:00,2,2018-06-01 07:10:00+02:00\n"
    df, basis = parse(csv)
    assert basis == "offset"
    assert df["started_at"][0] == datetime(2018, 6, 1, 5, 0, tzinfo=UTC)
    assert not df["flag_ambiguous_local_time"][0]


def test_space_before_offset_is_accepted():
    df, basis = parse(HEADER + b"1,2018-06-01 07:00:00 +0200,2,2018-06-01 07:10:00 +0200\n")
    assert basis == "offset"
    assert df["started_at"][0] == datetime(2018, 6, 1, 5, 0, tzinfo=UTC)


def test_unrecognised_timestamp_format_fails_with_examples():
    rows = b"".join(b"1,01/06/2018 07:%02d,2,01/06/2018 08:00\n" % i for i in range(20))
    with pytest.raises(ValueError, match=r"20 of 20 rows have unparseable timestamps, e\.g\. \[\('01/06"):
        parse(HEADER + rows)


def test_mixed_timestamp_formats_fail_loudly():
    csv = HEADER + b"1,2018-06-01 07:00:00+02:00,2,2018-06-01 07:10:00\n"
    with pytest.raises(ValueError, match="mixed timestamps"):
        parse(csv)


def test_fall_back_hour_is_flagged_and_read_as_summer_time(split):
    valid, _ = split
    amb = valid.filter(pl.col("flag_ambiguous_local_time"))
    assert amb.height == 1
    # 02:30 local, earliest reading = CEST = 00:30 UTC; duration still 10 minutes.
    assert amb["started_at"][0] == datetime(2018, 10, 28, 0, 30, tzinfo=UTC)
    assert amb["duration"][0] == 600


def test_spring_forward_gap_is_quarantined():
    csv = (
        b"start_station_id,start_time,end_station_id,end_time\n1,2018-03-25 02:30:00,2,2018-03-25 03:10:00\n"
    )
    df, _ = parse(csv)
    valid, quarantined = Contract.load("oslo_trips_legacy").split(df)
    assert (valid.height, quarantined.height) == (0, 1)


def test_duration_is_derived_and_bad_rows_are_quarantined(split):
    valid, quarantined = split
    # Quarantined: one trip ending before it starts, one unparseable timestamp.
    assert (valid.height, quarantined.height) == (5, 2)
    assert valid["duration"].to_list()[:2] == [600, 930]
    assert Contract.load("oslo_trips_legacy").validate(valid) == []


def test_short_trips_are_kept_and_flagged(split):
    valid, _ = split
    assert valid.filter(pl.col("flag_short_trip"))["duration"].to_list() == [40]


def test_legacy_ids_are_mapped_and_unmapped_ones_kept(split):
    valid, _ = split
    first = valid.row(0, named=True)
    assert (first["start_station_legacy_id"], first["start_station_id"]) == ("157", "627")
    unmapped = valid.filter(pl.col("flag_unmapped_station"))
    assert unmapped["start_station_legacy_id"].to_list() == ["999"]
    assert unmapped["start_station_id"].to_list() == [None]


def test_ambiguous_mapping_is_dropped_not_guessed():
    mapping, ambiguous = load_station_mapping(MAPPING.read_bytes())
    assert ambiguous == ["512"]
    assert "512" not in mapping["legacy_id"].to_list()
    # A legacy station with an empty new ID stays, with a null new ID.
    assert mapping.filter(pl.col("legacy_id") == "444")["new_id"].to_list() == [None]


def test_header_aliases_and_bom_are_accepted():
    csv = "\ufeffStart station,Start time,End station,End time\n1,2018-06-01 07:00:00,2,2018-06-01 07:10:00\n"
    df, _ = parse(csv.encode())
    assert df["start_station_legacy_id"].to_list() == ["1"]


def test_unknown_header_error_lists_actual_columns():
    with pytest.raises(ValueError, match=r"file has columns \['from', 'to'\]"):
        parse(b"from,to\n1,2\n")


def test_reads_single_csv_from_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("10.csv", TRIPS.read_bytes())
        zf.writestr("__MACOSX/._10.csv", b"junk")
    assert read_zipped_csv(buf.getvalue()) == TRIPS.read_bytes()


def test_locations_are_typed():
    loc = load_station_locations(LOCATIONS.read_bytes())
    # Matches the real file's header (legacy_id, latitude, longitude), seen in CI on 1 Nov 2026.
    assert loc.columns == ["legacy_id", "latitude", "longitude"]
    assert loc["latitude"].dtype == pl.Float64


def test_closed_hours_profile_distinguishes_readings():
    # 00:30 Oslo time is outside the 01-05 closure; the same wall-clock read as UTC is 02:30 Oslo (CEST).
    df, _ = parse(HEADER + b"1,2018-06-01 00:30:00,2,2018-06-01 00:40:00\n")
    assert closed_hours_profile(df) == {"as_parsed": 0.0, "if_raw_were_utc": 1.0}


def test_months_skip_winter_closures_and_reject_current_format():
    assert months("2016-11", "2017-05") == [(2016, 11), (2016, 12), (2017, 4), (2017, 5)]
    assert (2018, 3) in months("2018-01", "2018-04")
    with pytest.raises(ValueError):
        months("2018-12", "2019-04")


def test_summary_renders(split):
    mapping, ambiguous = load_station_mapping(MAPPING.read_bytes())
    md = summary_markdown(
        [{"month": "2018-10", "rows": 7}], ambiguous, mapping, load_station_locations(LOCATIONS.read_bytes())
    )
    assert "| 2018-10 | 7 |" in md
    assert "mapped IDs without a location: 1" in md


def test_unmapped_stations_counts_both_trip_ends(split):
    valid, _ = split
    assert unmapped_stations(valid).rows() == [("999", 1)]
