# LA Robo-Taxi Urban Congestion Simulation

Real Los Angeles road network + interactive robo-taxi adoption scenarios.
Shared graph + traffic-flow foundation for the 4-person CECS-427 project.

`graph_model.py` is the importable core. Teammates plug their own analyses
(centrality, game theory, adoption dynamics) on top without touching the UI.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

The first run downloads the LA OSM network (~30 s), simplifies it, and
caches the result to `cache/la_network.pkl`. Subsequent runs are instant.

### Windows / OSMnx caveats

OSMnx pulls in `geopandas`, `shapely`, `pyogrio`, and `pyproj`. These
have historically been painful to install on Windows because they wrap
GDAL / PROJ C libraries. As of pip ≥ 24 + Python 3.11+, wheels are
published for Windows x64 and `pip install osmnx` just works. If you
hit a GDAL error on an older setup:

1. Use a 64-bit Python 3.11 or 3.12 (tested on 3.12).
2. If pip still can't find a wheel, install `gdal` first via conda:
   `conda install -c conda-forge gdal geopandas shapely`, then
   `pip install osmnx`.
3. As a last resort, the `cache/la_network.pkl` is committed — teammates
   who only want to run the model can skip OSMnx entirely and rely on
   the cache. `la_network.load_la_network()` will read the pickle without
   ever importing `osmnx` if the file exists.

## Files

| File                | Purpose                                                       |
|---------------------|---------------------------------------------------------------|
| `la_network.py`     | OSMnx fetch → simplify → enrich; pickle cache                 |
| `graph_model.py`    | Pure model: `SimParams`, demand, flows, BPR, deadheading      |
| `calibration.py`    | Google-Maps-observed ratios → per-edge `baseline_flow_vph`    |
| `app.py`            | Streamlit UI with `scattermapbox` edge rendering              |
| `validate.py`       | Standalone sanity check (counts, preview PNG, default run)    |
| `cache/*.pkl,*.png` | Cached graph + preview image                                  |

## City model

- **Bounding box**: lat 33.98–34.12, lon −118.32 to −118.17 (downtown LA
  + surrounding freeway ring).
- **Road-type filter**: motorway, trunk, primary, and their `_link` variants.
  Residential streets would balloon the edge count without adding analysis
  value for a congestion study.
- **Simplification**: `osmnx.simplification.consolidate_intersections` with
  200 m tolerance. Result: **71 nodes / 199 edges** (down from ~1600 raw).
- **Attractor tagging**: any node within 1.5 km of a named landmark (Downtown
  Core, LA Live, Union Station, USC, Financial District, Koreatown) is
  flagged `zone_type="attractor"`; everything else is `"throughput"`.
- **Edge direction**: each edge gets a `direction ∈ {inbound, outbound}`
  at build time based on whether the bearing points toward downtown
  (34.05, −118.25). This feeds calibration's direction-aware ratios.

## Control → parameter mapping

| UI control          | `SimParams` field    | Effect                                                 |
|---------------------|----------------------|--------------------------------------------------------|
| Time of day         | `time_of_day`        | Selects OD matrix AND calibration ratios               |
| Background traffic  | `background_level`   | Multiplies calibrated `baseline_flow_vph` (×0.5/1/1.5) |
| Customer demand     | `demand_level`       | Scales OD trip table (×0.5/1/1.6)                      |
| Robo-taxi share     | `robotaxi_share`     | 0.0–1.0 of commuter trips                              |
| Deadheading ratio   | `deadhead_ratio`     | 0.0–0.5 empty RT trips per loaded RT trip              |
| Fleet cap           | `fleet_cap`          | binding → unmet demand + surge flag                    |

`SimParams` is unchanged from the original synthetic-grid version — any
notebook built before the LA port still runs. Two fields shifted in
meaning only:

- `background_level` now multiplies the **calibrated** baseline (from
  `calibration.py`) rather than a synthetic `0.4 × capacity`.
- `free_flow_speed_kmh` is **ignored** — per-edge free-flow times come
  from OSM `maxspeed` tags (with road-type defaults for missing tags).
  Kept in the dataclass for API stability.

## API for teammates

```python
from graph_model import (
    SimParams,
    build_graph,
    get_demand_matrix,
    compute_flows,
    compute_metrics,
    compute_vmt,        # back-compat alias
    get_baseline_vmt,
    run_scenario,
    simulate,           # back-compat alias of run_scenario
)

G = build_graph()                                      # nx.DiGraph of LA
params = SimParams(time_of_day="am_peak", robotaxi_share=0.4)

demand = get_demand_matrix("am_peak", "medium", G)     # {(o, d): trips}
flows  = compute_flows(G, params)                      # {edge: total flow}
m      = compute_metrics(flows, G)                     # {total_vmt, util, ...}
base   = get_baseline_vmt(G, params)                   # robotaxi_share=0 VMT
full   = run_scenario(G, params)                       # dict with all readouts
```

### Node attributes (set by `la_network.py`)

`node_id`, `lat`, `lon`, `pos` (lon, lat), `district`, `nearest_landmark`,
`landmark_dist_m`, `zone_type` (`attractor` / `throughput`).

### Edge attributes

`road_name` (normalized: "I-110", "Wilshire Blvd", etc.), `highway`
(OSM class), `lanes`, `length_km` (alias `distance_km`), `speed_kph`,
`free_flow_time_min`, `capacity_vph` (alias `capacity`),
`baseline_flow_vph` (alias `baseline_flow`, set by calibration),
`calibrated_ratio`, `direction` (`inbound` / `outbound`), `geometry_lonlat`
(polyline for map rendering).

### `run_scenario()` result keys

Unchanged from synthetic-grid version, plus `top_congested` (top 5 edges
by utilization, ready-formatted for the "Road inspection" panel).

## Calibration

`calibration.py` holds flow/capacity ratios observed on Google Maps'
typical-traffic layer. Three resolution levels, checked in order:

1. **`SEGMENT_OVERRIDES`** — pain points keyed by
   `<road>_<direction>_near_<landmark>` (e.g. `"I-110_inbound_near_I-10"`
   for the 10/110 stack northbound at AM peak).
2. **`ROAD_CALIBRATION`** — road-level ratios per
   `{tod: {inbound, outbound}}`. Covers the major freeways and
   arterials; fill in more roads by eyeballing Maps.
3. **`DEFAULT_RATIOS`** — TOD fallback (`am: 0.55`, `midday: 0.45`,
   `pm: 0.55`) for any edge not covered above.

`apply_calibration(G, time_of_day)` writes `baseline_flow_vph = ratio ×
capacity_vph` on every edge. `compute_flows()` calls this at the start of
each scenario so background traffic reflects real observations.

Check coverage with:

```python
from calibration import calibration_coverage
calibration_coverage(G, "am_peak")
# -> {'override': 0, 'road': 10, 'fallback': 189, 'calibrated_fraction': 0.05}
```

## Modeling assumptions

- **Routing**: all-or-nothing on shortest path by `length_km`.
- **Travel time**: BPR with α = 0.15, β = 4, per-edge `free_flow_time_min`.
- **Personal-car occupancy**: 1.2 riders/vehicle. Robo-taxi: 1.
- **Transit**: off-road; no edge flow contribution.
- **Fleet cap**: `fleet_cap × trips_per_vehicle_per_period < RT demand`
  → proportional rejection across RT OD pairs; rejected trips are
  reported as `unmet_demand`, not reassigned to other modes.
- **Random seed**: fixed via `SimParams.seed` (default 42).
