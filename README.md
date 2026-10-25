# Oslo Bike Demand

Forecasting station-level city-bike demand in Oslo with spatio-temporal graph networks, using London's
Santander Cycles as a comparison and transfer-learning source.

**Author:** Taylor Xu · **Status:** early build (v0.1 in progress) · **Licence:** MIT (code), source
licences for data (see [data sources](docs/data-sources.md))

## Why this project

A city-bike network is a graph that changes over time. Stations are nodes, trips are flows between them,
and demand at one station depends on its neighbours, the time of day, the weather and what people expect
to find when they arrive. That is the same shape of problem as my MSc research, which used an ST-GCN over
a pedestrian's skeleton graph to predict crossing behaviour, with a Prospect Theory module for how people
weigh risk. Here the graph is a city instead of a body.

Oslo is a good place to do it. Oslo City Bike publishes one of the best-documented open bike-share
records in Europe: about ten years of trips, a stable schema since April 2019, station coordinates on
every trip, and explicit rules on what is excluded. London adds a network several times larger with
weekly trip records back to 2015, which makes it possible to ask whether what a model learns in one city
carries over to another.

The full motivation, and how this builds on the MSc work, is in
**[docs/research-motivation.md](docs/research-motivation.md)**.

## Research questions

| # | Question | Type |
|---|---|---|
| RQ1 | How do the shapes of demand differ between Oslo and London (commuting peaks, weekend leisure, weather and seasonal sensitivity) once scale is normalised? | Descriptive |
| RQ2 | Does an ST-GCN that uses the station graph beat strong non-graph baselines in each city? | Predictive |
| RQ3 | Does a model pre-trained on London's larger network transfer to Oslo, and how much Oslo data does fine-tuning need? | Transfer |
| RQ4 | Do station-choice decisions under uncertainty about empty or full docks show Prospect-Theory-like asymmetries? | Behavioural (later) |

## Data

| Source | Coverage | Licence |
|---|---|---|
| Oslo City Bike trips | April 2016 → present (legacy format before April 2019) | NLOD 2.0 |
| Oslo City Bike real-time availability (GBFS) | Archived by this repo from October 2026 | NLOD 2.0 |
| TfL Santander Cycles journeys | September 2015 → latest published week | TfL open data terms |

Raw data is not committed. Ingestion scripts download it to `data/` (git-ignored). Details, known gaps
and how each is handled are in [docs/data-sources.md](docs/data-sources.md).

## Repository layout

```
bike_demand/
├── contracts/               Data contracts: schema, keys, rules, licence per source
├── src/bikedemand/
│   ├── contracts.py         Loads a contract and validates a DataFrame against it
│   ├── stations.py          Time-versioned station dimension built from trips
│   └── ingest/
│       ├── oslo_trips.py         Monthly trip files → validated Parquet
│       └── oslo_availability.py  GBFS station-status snapshot archiver
├── tests/                   Contract and parsing tests on small fixtures
├── docs/                    Motivation, data sources, roadmap
└── .github/workflows/       CI, a real-data check, and the scheduled availability archiver
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

# Download and validate Oslo trips for a range of months
python -m bikedemand.ingest.oslo_trips --start 2019-04 --end 2026-09

# Build the time-versioned station dimension from the ingested months
python -m bikedemand.stations --start 2019-04 --end 2026-09

# Take one availability snapshot (the scheduled workflow does this every 15 minutes)
python -m bikedemand.ingest.oslo_availability
```

## Roadmap

See [docs/roadmap.md](docs/roadmap.md). In short: Oslo ingestion and contracts → London ingestion and a
unified two-city model → comparison analysis → baselines → v0.1. The ST-GCN, transfer experiments and
the behavioural work follow in 2027.

## Related work by me

- [`data_science_msc/pedestrian_behaviour`](https://github.com/taylorhxu/data_science_msc/tree/main/pedestrian_behaviour):
  MSc research on pedestrian crossing prediction with an ST-GCN and a differentiable Prospect Theory
  module.

## Attribution

Contains data from Oslo City Bike (UIP Bauer Media Outdoor Norge AS), published under the
[Norwegian Licence for Open Government Data (NLOD) 2.0](https://data.norge.no/nlod/en/2.0).
Powered by TfL Open Data.
