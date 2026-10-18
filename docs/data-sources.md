# Data sources

A register of every source this project uses: what it is, its licence, how it is accessed, and the known
issues that affect modelling. Each source also has a machine-readable contract in `contracts/`.

## Register

| ID | Source | Access | Licence | Attribution | Contract |
|---|---|---|---|---|---|
| `oslo_trips` | Oslo City Bike historical trips, April 2019 → present | Monthly CSV/JSON from `data.urbansharing.com` | NLOD 2.0 | Oslo City Bike / UIP Bauer Media Outdoor Norge AS | `contracts/oslo_trips.yaml` |
| `oslo_trips_legacy` | Oslo City Bike trips, April 2016 → December 2018 (older format) | Monthly zipped CSV/JSON from `data-legacy.urbansharing.com`, plus station location and ID mapping files | NLOD 2.0 | As above | planned |
| `oslo_availability` | Oslo City Bike real-time station status (GBFS), archived by this repo | `gbfs.urbansharing.com`, `Client-Identifier` header required | NLOD 2.0 | As above | planned |
| `london_trips` | TfL Santander Cycles journeys, September 2015 → latest week | Weekly CSV files from `cycling.data.tfl.gov.uk` | TfL open data terms | "Powered by TfL Open Data" | planned |

Raw files are never committed. Only code, contracts, small test fixtures and derived summary outputs are.

## Oslo trips: what the data contains

From Oslo City Bike's own documentation:

- Trips of at least one minute, during regular opening hours (05:00–01:00).
- Cancelled trips and bikes moved by the operator's team (rebalancing) are **excluded**.
- Files are cut at midnight **UTC**, so daily totals will not match counts in local time.
- The current month's file is updated daily.

Fields: `started_at`, `ended_at`, `duration` (seconds), and for both start and end station: `id`, `name`,
`description`, `latitude`, `longitude`.

## Known issues and how they are handled

| Issue | Effect on modelling | Handling |
|---|---|---|
| UTC timestamps | Peaks shift by one or two hours depending on daylight saving | Convert to `Europe/Oslo` at ingestion; keep the UTC column too |
| Rebalancing excluded | We see demand, not how many bikes were at a station | Model demand only; use archived availability for supply-side questions |
| Opening hours | Zeros between 01:00 and 05:00 are structural, not low demand | Mask closed hours in training and evaluation |
| Legacy winter gaps | 2016–2018 files have no January–February | Treat 2016–18 as a separate regime; core models use April 2019+ |
| COVID (2020–2021) | Behaviour shift (fewer commutes, more leisure) | Flag as a regime; test whether models trained before, during or after transfer |
| Station network changes | The graph's nodes change over time | Time-versioned station dimension (valid-from / valid-to), built from trip data and GBFS |
| Legacy station IDs | Different identifiers before 2019 | Use the operator's legacy-to-new mapping file |
| Station-hour sparsity | Most station-hours have zero or one trip, so naive models look good | Always report against seasonal-naive baselines, split by station activity level |

## London trips: notes to confirm on ingestion

- Weekly files, with column names and formats that have changed over the years. Expect to write a
  per-era parser and a single normalised output schema.
- Check the date of the latest published file before relying on recent months.
- No coordinates in the trip files; join to the docking station feed.
