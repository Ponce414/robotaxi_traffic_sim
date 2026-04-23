# Robo-Taxi Urban Congestion Simulation

Shared graph + traffic-flow foundation for the 4-person CECS-427 robo-taxi
project. `graph_model.py` is the importable core; `app.py` is a Streamlit demo.
Teammates plug their own analyses (centrality, game theory, adoption dynamics)
on top of `graph_model.py` — no UI dependencies.

## Setup

```bash
pip install networkx plotly streamlit numpy pandas
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501).

## Files

- `graph_model.py` — pure logic. Builds the city graph, generates demand, runs
  the assignment, returns flows and readouts. Safe to import from any notebook.
- `app.py` — Streamlit wrapper. All caching and Plotly rendering lives here.
- `README.md` — this file.

## City model

- **25 zones on a 5×5 grid.** Four attractors: CBD (center), commercial
  district, stadium, airport (peripheral). Inner ring is mixed commercial,
  outer ring is residential.
- **Directed edges** between 4-neighbors plus six diagonal arterials between
  the CBD and the corners. Inbound-to-attractor capacity is higher than
  outbound (asymmetric arterials).
- **Three demand matrices** keyed by time-of-day:
  - AM peak: residential → attractors (weights CBD 50%, COM 20%, AIR 15%, STAD 15%).
  - PM peak: attractors → residential.
  - Midday: diffuse exchange among commercial + attractor zones.
- **Deadheading**: a fraction of RT VMT is empty repositioning. Empty trips
  originate at dropoff zones and go to next-pickup zones distributed by
  PageRank with demand-weighted personalization. AM dropoffs cluster at the
  CBD, so empty flow naturally heads back outbound.

## Control → parameter mapping

| UI control          | `SimParams` field        | Effect                                                 |
|---------------------|--------------------------|--------------------------------------------------------|
| Time of day         | `time_of_day`            | `am_peak` / `midday` / `pm_peak` — selects OD matrix   |
| Background traffic  | `background_level`       | `low`/`medium`/`high` — scales edge `baseline_flow`    |
| Customer demand     | `demand_level`           | scales the OD trip table                               |
| Robo-taxi share     | `robotaxi_share`         | 0.0–1.0 of commuter trips                              |
| Deadheading ratio   | `deadhead_ratio`         | 0.0–0.5 empty RT trips per loaded RT trip              |
| Fleet cap           | `fleet_cap`              | binding → unmet demand + surge flag                    |

## API for teammates

```python
from graph_model import (
    SimParams,
    build_graph,
    get_demand_matrix,
    compute_flows,
    compute_vmt,
    get_baseline_vmt,
    simulate,
)

G = build_graph(seed=42)                          # nx.DiGraph
params = SimParams(time_of_day="am_peak", robotaxi_share=0.4)

demand = get_demand_matrix("am_peak", "medium", G)   # {(o, d): trips}
flows  = compute_flows(G, params)                    # {edge: total flow}
vmt    = compute_vmt(flows, G)                       # float
base   = get_baseline_vmt(G, params)                 # robotaxi_share=0
full   = simulate(G, params)                         # full result dict
```

`SimParams` is a frozen dataclass — modify with
`dataclasses.replace(params, robotaxi_share=0.5)`.

### Node attributes

`zone_id`, `pos` (x, y), `zone_type` (`residential` / `commercial` / `attractor`),
`population`, `base_trip_demand`, `parking_capacity`, `has_pickup_dropoff_infra`.

### Edge attributes

`capacity`, `distance_km`, `baseline_flow`.

### `simulate()` result keys

| Key                     | Type                       | Notes                                       |
|-------------------------|----------------------------|---------------------------------------------|
| `edge_flow`             | `{edge: float}`            | bg + personal + loaded RT + deadhead RT     |
| `bg_flow`               | `{edge: float}`            | background only                             |
| `pc_flow`               | `{edge: float}`            | personal-car flow                           |
| `rt_flow`               | `{edge: float}`            | loaded robo-taxi flow                       |
| `dh_flow`               | `{edge: float}`            | empty robo-taxi (deadhead) flow             |
| `utilization`           | `{edge: float}`            | flow / capacity                             |
| `travel_time_hr`        | `{edge: float}`            | BPR travel time                             |
| `total_vmt`             | `float`                    | sum over all edges, all flow                |
| `commuter_vmt`          | `float`                    | personal + loaded RT only                   |
| `deadhead_vmt`          | `float`                    | empty RT only                               |
| `avg_trip_time_min`     | `float`                    | trip-weighted average                       |
| `congested_edge_count`  | `int`                      | edges with util > 0.9                       |
| `unmet_demand`          | `float`                    | trips rejected when fleet cap binds         |
| `surge_flag`            | `bool`                     | True when fleet cap binds                   |
| `total_trips`           | `float`                    | served commuter trips                       |
| `pagerank`              | `{node: float}`            | demand-personalized PageRank vector         |

## Modeling assumptions

- **Routing**: all-or-nothing on shortest path by `distance_km` (paths cached).
- **Travel time**: BPR with α = 0.15, β = 4, free-flow 40 km/h.
- **Personal-car occupancy**: 1.2 riders/vehicle. Robo-taxi: 1 rider/vehicle.
- **Transit**: off-road; contributes no edge flow.
- **Fleet cap**: when `fleet_cap × trips_per_vehicle_per_period` is less than
  RT demand, *all* RT OD pairs are scaled down proportionally — rejected trips
  are recorded as `unmet_demand`, not re-allocated to other modes.
- **Random seed**: fixed via `SimParams.seed` (default 42).
