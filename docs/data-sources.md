# Data sources

A register of every source this project uses: what it is, its licence, how it is accessed, and the known
issues that affect modelling. Each source also has a machine-readable contract in `contracts/`.

## Register

| ID | Source | Access | Licence | Attribution | Contract |
|---|---|---|---|---|---|
| `oslo_trips` | Oslo City Bike historical trips, April 2019 → present | Monthly CSV/JSON from `data.urbansharing.com` | NLOD 2.0 | Oslo City Bike / UIP Bauer Media Outdoor Norge AS | `contracts/oslo_trips.yaml` |
| `oslo_trips_legacy` | Oslo City Bike trips, April 2016 → December 2018 (older format) | Monthly zipped CSV/JSON from `data-legacy.urbansharing.com`, plus station location and ID mapping files | NLOD 2.0 | As above | `contracts/oslo_trips_legacy.yaml` |
| `oslo_availability` | Oslo City Bike real-time station status (GBFS), archived by this repo | `gbfs.urbansharing.com`, `Client-Identifier` header required; snapshots every 5 min, bundled per run in weekly GitHub pre-releases | NLOD 2.0 | As above | planned |
| `london_trips` | TfL Santander Cycles journeys, September 2015 → latest week | Weekly CSV files from `cycling.data.tfl.gov.uk` | TfL open data terms | "Powered by TfL Open Data" | planned |

Raw files are never committed. Only code, contracts, small test fixtures and derived summary outputs are.

## Oslo trips: what the data contains

From Oslo City Bike's own documentation:

- Trips of at least one minute, during regular opening hours (05:00–01:00). In practice a few shorter
  trips remain: see the first real-data check below.
- Cancelled trips and bikes moved by the operator's team (rebalancing) are **excluded**.
- Files are cut at midnight **UTC**, so daily totals will not match counts in local time.
- The current month's file is updated daily.

Fields: `started_at`, `ended_at`, `duration` (seconds), and for both start and end station: `id`, `name`,
`description`, `latitude`, `longitude`.

## First real-data check (September 2026)

Run on 18 October 2026 against the published September file:

| Measure | Value |
|---|---|
| Rows | 157,634 |
| Passed contract v1 | 157,542 |
| Quarantined | 92 (0.06%), all for duration under 60 s |
| Trips ending before they start | 0 |
| Duration not matching timestamps | 0 |

The 92 short trips (25–59 s) are genuine hops between neighbouring stations, such as Aker Brygge 3 →
Aker Brygge 2 and Vippetangen vest → øst, despite the documented one-minute minimum. Contract v2 keeps
them and marks them with `flag_short_trip`, so analyses can include or exclude them deliberately.

## Known issues and how they are handled

| Issue | Effect on modelling | Handling |
|---|---|---|
| UTC timestamps | Peaks shift by one or two hours depending on daylight saving | Convert to `Europe/Oslo` at ingestion; keep the UTC column too |
| Rebalancing excluded | We see demand, not how many bikes were at a station | Model demand only; use archived availability for supply-side questions |
| Opening hours | Zeros between 01:00 and 05:00 are structural, not low demand | Mask closed hours in training and evaluation |
| Legacy winter gaps | No January–March files in 2016 and 2017, no January–February in 2018 | Treat 2016–18 as a separate regime; core models use April 2019+ |
| COVID (2020–2021) | Behaviour shift (fewer commutes, more leisure) | Flag as a regime; test whether models trained before, during or after transfer |
| Station network changes | The graph's nodes change over time | Time-versioned station dimension (valid-from / valid-to), built from trip data and GBFS |
| Legacy station IDs | Different identifiers before 2019 | Use the operator's legacy-to-new mapping file; unmapped stations kept with a null new ID and flagged; legacy IDs mapping to several new IDs treated as unmapped |
| Trips under 60 s | A handful per month despite the documented minimum | Kept and flagged (`flag_short_trip`), contract v2 |
| Archived availability gaps | 18–21 Oct 2026: GitHub ran the 15-minute schedule only ~6 times a day, leaving gaps of several hours | Archiver changed on 21 Oct 2026 to long-running 5-minute loops; treat earlier data as sparse |
| Station-hour sparsity | Most station-hours have zero or one trip, so naive models look good | Always report against seasonal-naive baselines, split by station activity level |

## Oslo legacy trips (2016–2018): what is and isn't documented

The publisher documents four variables per legacy trip: `start_station_id`, `start_time`, `end_station_id`,
`end_time`. There is no duration (it is derived) and no station name or coordinates on the trip; those
come from `legacy_station_locations.csv`, and current-system IDs from `legacy_new_station_id_mapping.csv`.

### First real-data check (September–October 2018)

Run in CI on 1 November 2026 (`Real-data check (Oslo legacy trips)` on PR #2). What the real files contain:

- **Trip headers** are `Start station,Start time,End station,End time`, not the snake_case names on the
  publisher's page. Each monthly zip holds one CSV, e.g. `trips-2018.10.1-2018.10.31.csv`.
- **Timestamps carry an explicit offset**, e.g. `2018-10-01 05:59:42 +0200`, in 2016 as in 2018. So
  the time zone is not a guess: offsets are used as given. Check: 0.002–0.004% of trips start
  01:00–05:00 Oslo time under this reading, against 2.3–2.5% if the wall-clock values were UTC. The
  first trips of the day start at about 06:00, consistent with the scheme opening at 06:00 then.
- **Mapping file** header is `new_id,legacy_id`: 308 legacy IDs, all one-to-one, none empty.
- **Locations file** has `legacy_id,latitude,longitude` (no names), 320 stations; at least one (292) has
  coordinates 0,0.

| Measure | 2018-09 | 2018-10 |
|---|---|---|
| Rows | 340,910 | 263,003 |
| Valid | 340,851 | 262,533 |
| Quarantined | 59 | 470 |
| Trips under 60 s (kept, flagged) | 12,451 (3.7%) | 8,698 (3.3%) |
| Trips touching a station with no mapping | 206,936 (61%) | 162,611 (62%) |
| Legacy stations seen | 238 | 245 |

Two findings to follow up, both open:

1. **The mapping covers far fewer stations than the trips use.** 117 legacy station IDs in these two
   months have no entry in the mapping, and they include the busiest ones (465, 292, 374, 405, ...). So
   the operator's mapping alone cannot link most 2018 trips to today's stations. Possible reasons, not
   yet tested: stations that closed before 2019, or late-legacy files already using another ID scheme.
   Next check: whether the unmapped IDs appear in the locations file, so they can be matched to current
   stations by coordinates.
2. **Short trips are far more common than after 2019** (3–4% against 0.06%). Early 2016 rows show
   same-station trips of 19–33 s, which look like re-docking rather than journeys. Not yet investigated;
   they stay flagged. The reason for the 470 quarantined October rows has not been looked at either.

## London trips: notes to confirm on ingestion

- Weekly files, with column names and formats that have changed over the years. Expect to write a
  per-era parser and a single normalised output schema.
- Check the date of the latest published file before relying on recent months.
- No coordinates in the trip files; join to the docking station feed.
