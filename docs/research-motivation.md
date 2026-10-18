# Research motivation

## 1. Where this comes from

My MSc dissertation ([`pedestrian_behaviour`](https://github.com/taylorhxu/data_science_msc/tree/main/pedestrian_behaviour))
asked whether a model of individual risk perception improves pedestrian crossing prediction. It combined
two ideas:

- An **ST-GCN backbone** that treats a pedestrian's skeleton as a graph (17 joints as nodes, anatomical
  links as edges) and learns from how that graph moves over time.
- A differentiable **Cumulative Prospect Theory** module, so the model could represent that two people
  facing the same gap in traffic can choose differently because they weigh risk differently.

The population-level Prospect Theory effect was modest but significant, and the individual-level effect
appeared only once there was roughly 3.3 times more training data. The main limitation I recorded was
that both evaluation sets came from the same footage (JAAD), so the work could not show that anything
generalised beyond one dataset.

This project picks up both threads in a new domain.

## 2. The same problem, at city scale

A city-bike network has the structure an ST-GCN is designed for:

| MSc project | This project |
|---|---|
| Nodes: 17 skeleton joints | Nodes: bike stations (hundreds in Oslo, ~800 in London) |
| Edges: fixed anatomical links | Edges: distance, and observed trip flows between stations |
| Signal: joint positions over time | Signal: departures and arrivals per station per time step |
| Target: will this person cross? | Target: how many bikes will leave or arrive at each station? |
| Behavioural layer: risk tolerance when crossing | Behavioural layer: choices when a dock may be empty or full |

Two things change, and they make the problem harder in useful ways. The graph is much larger, and it is
not fixed: stations open, close and move over ten years. And the behaviour is collective. A single
rider's choice is invisible, but thousands of them add up to demand patterns that respond to weather,
season, events and the network itself.

## 3. What this project adds

**Generalisation, directly.** The MSc work could not test generalisation beyond one dataset. Here it is
built into the design: London and Oslo are separate systems, run by different operators, in cities with
different cycling cultures, terrain and climate. RQ3 asks whether a model trained on London's larger
network transfers to Oslo, and how much Oslo data fine-tuning needs. Oslo's sister schemes in Bergen and
Trondheim publish in the same format, which gives a second, cleaner transfer test later.

**Data volume.** The MSc result showed that individual-level effects only appeared with enough data.
Oslo offers about ten years of trips and London more than ten, so data volume should not be the limit
this time.

**A sharper behavioural question.** Prospect Theory is about decisions under risk measured against a
reference point, where losses weigh more than equal gains. Bike share has a clean version of this: a
rider heading to a station does not know whether there will be a bike (or a free dock on return). Do
riders avoid stations that are often empty more than the raw probability would justify? Does demand
shift asymmetrically after a station is unexpectedly empty? Historical availability data is not
published, so this repo archives Oslo's real-time feed from October 2026 to build that record (RQ4).

## 4. Lessons carried over from the MSc

Two problems from the dissertation shape how this project is set up:

1. **Majority-class collapse.** An early MSc result looked like a win until a confusion matrix showed
   the model predicting one class for 99% of samples. The demand-forecasting equivalent is a model that
   looks accurate because most station-hours have zero or near-zero trips. So every model here is
   compared against strong naive baselines (seasonal naive, historical average by station and hour)
   before any graph model is trusted, and metrics are reported separately for busy and quiet stations.
2. **Dataset artifacts.** In the MSc data, a trivial feature almost solved the task on unannotated
   bystanders. The bike-share equivalents are known in advance: rebalancing moves are excluded from the
   Oslo data, legacy years have winter closures, COVID changed behaviour, and timestamps are in UTC
   while behaviour follows local time. Each is documented in [data-sources.md](data-sources.md) and
   handled explicitly rather than discovered late.

## 5. Why Oslo

I came across Oslo's data while looking for open bike-share records in Europe, and it stood out. Few
European systems publish trip-level data at all; many large schemes publish only live availability or
none. Oslo publishes every trip with station coordinates, a stable schema since 2019, records back to
2016, a daily-updated current month, an explicit list of what is excluded, and an open licence. Bergen
and Trondheim publish in the same format, which makes cross-city tests straightforward.

## 6. What success looks like

| Version | Outcome |
|---|---|
| v0.1 | Both cities ingested under data contracts; descriptive comparison (RQ1); baseline forecasts |
| v0.2 | ST-GCN adapted from the MSc backbone to the station graph; RQ2 answered honestly, including if the graph does not help |
| v0.3 | London → Oslo transfer experiments (RQ3), with a data-volume curve like the MSc one |
| v0.4 | First look at availability-driven behaviour (RQ4), once enough archived data exists |

A negative result at any stage is still a result, and will be written up as one.
