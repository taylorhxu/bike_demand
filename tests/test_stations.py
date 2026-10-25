"""Station dimension tests on a small synthetic history in the oslo_trips CSV format.

The history, over 1-8 September 2026 (UTC days):
- Station 100 "Alpha" moves about 200 m on day 4, then is renamed "Alpha Nord" on day 6.
- Station 200 "Beta" never changes, but has 5 m of coordinate jitter on day 2 and one stray row with a
  wrong name on day 3 (outvoted by that day's other trips).
- Station 300 "Gamma" changes only its description (on day 5).
- Station 400 "Delta" first appears on day 5.
"""

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from bikedemand.contracts import Contract
from bikedemand.ingest.oslo_trips import apply_rules, parse
from bikedemand.stations import as_of, build, haversine_m, summarise, summary_markdown

ALPHA = ("100", "Alpha", "ved torget", 59.910000, 10.750000)
ALPHA_MOVED = ("100", "Alpha", "ved torget", 59.911800, 10.750000)  # ~200 m north
ALPHA_NORD = ("100", "Alpha Nord", "ved torget", 59.911800, 10.750000)
BETA = ("200", "Beta", "", 59.920000, 10.760000)
BETA_JITTER = ("200", "Beta", "", 59.920045, 10.760000)  # ~5 m
BETA_TYPO = ("200", "Beta X", "", 59.920000, 10.760000)
GAMMA_OLD = ("300", "Gamma", "gammel tekst", 59.930000, 10.770000)
GAMMA_NEW = ("300", "Gamma", "ny tekst", 59.930000, 10.770000)
DELTA = ("400", "Delta", "", 59.940000, 10.780000)


def _alpha(day: int):
    return ALPHA if day < 4 else ALPHA_MOVED if day < 6 else ALPHA_NORD


def _trips_for_day(day: int) -> list[tuple]:
    beta = BETA_JITTER if day == 2 else BETA
    gamma = GAMMA_OLD if day < 5 else GAMMA_NEW
    pairs = [(_alpha(day), beta), (beta, gamma), (gamma, _alpha(day))]
    if day == 3:
        pairs.append((BETA_TYPO, gamma))
        pairs.append((gamma, beta))
    if day >= 5:
        pairs.append((DELTA, beta))
    return pairs


def _row(start: tuple, end: tuple, started: datetime, seconds: int = 600) -> dict:
    ended = started + timedelta(seconds=seconds)
    row = {
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S+00:00"),
        "ended_at": ended.strftime("%Y-%m-%d %H:%M:%S+00:00"),
        "duration": seconds,
    }
    for side, (sid, name, desc, lat, lon) in (("start", start), ("end", end)):
        row |= {
            f"{side}_station_id": sid,
            f"{side}_station_name": name,
            f"{side}_station_description": desc,
            f"{side}_station_latitude": lat,
            f"{side}_station_longitude": lon,
        }
    return row


@pytest.fixture(scope="module")
def trips() -> pl.DataFrame:
    rows = []
    for day in range(1, 9):
        for i, (start, end) in enumerate(_trips_for_day(day)):
            rows.append(_row(start, end, datetime(2026, 9, day, 8 + i)))
    csv = pl.DataFrame(rows).write_csv().encode()
    return apply_rules(parse(csv))


@pytest.fixture(scope="module")
def dim(trips) -> pl.DataFrame:
    return build(trips)


def _versions(dim: pl.DataFrame, station_id: str) -> pl.DataFrame:
    return dim.filter(pl.col("station_id") == station_id).sort("version")


def test_dimension_satisfies_its_contract(dim):
    assert Contract.load("oslo_stations").validate(dim) == []


def test_move_and_rename_each_start_a_version(dim):
    alpha = _versions(dim, "100")
    assert alpha["change_reason"].to_list() == ["first_seen", "moved", "renamed"]
    assert alpha["name"].to_list() == ["Alpha", "Alpha", "Alpha Nord"]
    assert alpha["valid_from"].to_list() == [date(2026, 9, 1), date(2026, 9, 4), date(2026, 9, 6)]
    assert alpha["valid_to"].to_list() == [date(2026, 9, 4), date(2026, 9, 6), None]


def test_jitter_and_stray_rows_do_not_start_versions(dim):
    beta = _versions(dim, "200")
    assert beta.height == 1
    assert beta["name"][0] == "Beta"
    # The version's coordinates come from its busiest days, not the jittered one.
    assert beta["latitude"][0] == pytest.approx(59.92)


def test_description_change_alone_keeps_one_version(dim):
    gamma = _versions(dim, "300")
    assert gamma.height == 1
    # The latest text wins, even though the old one was seen on more trips.
    assert gamma["description"][0] == "ny tekst"


def test_new_station_starts_on_its_first_day(dim):
    delta = _versions(dim, "400")
    assert delta["valid_from"].to_list() == [date(2026, 9, 5)]
    assert delta["valid_to"].to_list() == [None]


def test_validity_windows_chain_without_gaps_or_overlaps(dim):
    chained = dim.sort("station_id", "version").with_columns(
        next_from=pl.col("valid_from").shift(-1).over("station_id")
    )
    assert (chained["valid_to"].eq_missing(chained["next_from"])).all()


def test_as_of_returns_exactly_one_version_per_live_station(dim):
    on_day_5 = as_of(dim, date(2026, 9, 5)).sort("station_id")
    assert on_day_5["station_id"].to_list() == ["100", "200", "300", "400"]
    assert on_day_5.filter(pl.col("station_id") == "100")["version"][0] == 2
    assert as_of(dim, date(2026, 9, 3))["station_id"].to_list().count("400") == 0


def test_threshold_is_configurable(trips):
    # With a 500 m threshold the 200 m move is ignored; only the rename remains.
    alpha = _versions(build(trips, move_threshold_m=500), "100")
    assert alpha["change_reason"].to_list() == ["first_seen", "renamed"]


def test_haversine_is_about_right():
    # One thousandth of a degree of latitude is about 111 m.
    d = pl.select(haversine_m(pl.lit(59.91), pl.lit(10.75), pl.lit(59.911), pl.lit(10.75))).item()
    assert d == pytest.approx(111.2, abs=0.5)


def test_summary_counts_changes(dim):
    s = summarise(dim)
    assert s["stations"] == 4
    assert s["versions"] == 6
    assert s["stations_with_changes"] == 1
    assert s["changes_by_reason"] == {"moved": 1, "renamed": 1}
    assert "Alpha Nord" in summary_markdown(dim, s)


def test_contract_rejects_duplicate_keys(dim):
    errors = Contract.load("oslo_stations").validate(pl.concat([dim, dim.head(1)]))
    assert any("primary key" in e for e in errors)
