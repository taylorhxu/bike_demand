# Roadmap

Working rhythm: two 45-minute weekday sessions and one longer weekend session. Every session ends with a
commit and a GitHub issue describing the next small step.

## v0.1: data foundation and baselines (October – early December 2026)

| Week | Dates | Work | Done when |
|---|---|---|---|
| 1 | 18–25 Oct | Repo, CI, Oslo trip ingestion and contract; start availability archiver | Oslo April 2019 → present loaded; tests pass in CI; archiver running |
| 2 | 26 Oct – 1 Nov | Legacy 2016–18 ingestion with station ID mapping; time-versioned station dimension | One station table covering 2016 → present |
| 3–4 | 2–15 Nov | London ingestion with per-era parsers; unified two-city trip schema | Both cities queryable through one model in DuckDB |
| 5 | 16–22 Nov | RQ1 comparison: hourly and weekly demand profiles, normalised peaks, weather join | First charts and findings in `docs/` |
| 6–7 | 23 Nov – 6 Dec | Baselines per city (seasonal naive, station-hour average, gradient boosting); write-up | Baseline results table; **v0.1 tagged** |

## Pause (mid-December 2026 onwards)

Archiver keeps running. Weekly automated PRs continue; Taylor reviews them when time allows.

## Later (2027)

| Version | Work |
|---|---|
| v0.2 | Adapt the MSc ST-GCN backbone to a station graph (distance and flow adjacency); RQ2 |
| v0.3 | London → Oslo transfer and fine-tuning data-volume curve; Bergen and Trondheim as extra targets; RQ3 |
| v0.4 | Availability-driven behaviour from the archived GBFS data; RQ4 |
| v0.5 | Multi-city extension: test transfer and compare demand across the systems below |

## Other cities with open trip data (v0.5 candidates)

Candidates for extending RQ1 (comparison) and RQ3 (transfer) beyond Oslo and London. Coverage, current
publication status and terms must be checked before any ingestion work starts, since several have changed
operator or format over the years. **Weekly sessions should not start these without Taylor's go-ahead.**

### Europe

| City | System | Data | Why it's interesting | Status |
|---|---|---|---|---|
| Bergen, Norway | Bergen Bysykkel | Trip records, same format as Oslo | Same operator and schema: the cleanest transfer test | Planned in v0.3 |
| Trondheim, Norway | Trondheim Bysykkel | Trip records, same format as Oslo | As Bergen; smaller, hillier city | Planned in v0.3 |
| Helsinki and Espoo, Finland | HSL City Bikes | Trips from May 2016 with distance (m) and duration; CC BY 4.0 | Nordic comparison; only source here with trip distance | To verify; seasonal (spring to October) |
| Barcelona, Spain | Bicing | Station-status history only, no trips | Comparison for RQ4 (availability), not for demand flows | To verify |

### North America

| City | System | Why it's interesting | Status |
|---|---|---|---|
| New York, USA | Citi Bike | Largest open record; coordinates, member vs casual, e-bike vs classic | To verify |
| Chicago, USA | Divvy | Large, long history | To verify |
| Washington DC, USA | Capital Bikeshare | One of the longest records in North America | To verify |
| San Francisco Bay Area, USA | Bay Wheels (formerly Ford GoBike) | Hilly terrain, mixed e-bike fleet | To verify |
| Boston, USA | Bluebikes (formerly Hubway) | Mid-size; seasonal climate closer to Oslo | To verify |
| Philadelphia, USA | Indego | Mid-size | To verify |
| Los Angeles, USA | Metro Bike Share | Car-oriented city; contrast case | To verify |
| Minneapolis–St Paul, USA | Nice Ride | Seasonal winter closure, like legacy Oslo | To verify |
| Montreal, Canada | BIXI | Seasonal; harsh winters; large network | To verify |

### Latin America

| City | System | Why it's interesting | Status |
|---|---|---|---|
| Guadalajara, Mexico | MiBici | Different climate and urban form; tests how far transfer stretches | To verify |

Source for the North and Latin American list: the rOpenSci `bikedata` package, which collects systems
that publish individual trip data.
