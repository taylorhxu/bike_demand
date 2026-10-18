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
