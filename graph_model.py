"""Robo-taxi urban congestion model.

Pure Python / NetworkX. No UI imports — safe to import from notebooks
or other analyses (centrality, game theory, adoption dynamics).

Public API:
    SimParams                                       — frozen dataclass of all knobs
    build_graph(seed=42) -> nx.DiGraph              — synthetic 25-zone city
    get_demand_matrix(tod, level, G) -> {(o,d): trips}
    compute_flows(G, params) -> {edge: flow}        — total edge flow (bg + loaded)
    compute_vmt(flows, G) -> float                  — sum(flow * distance_km)
    get_baseline_vmt(G, params) -> float            — same params, robotaxi_share=0
    simulate(G, params) -> dict                     — full result with readouts
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping

import networkx as nx
import numpy as np


# ============================================================
# Zone layout (5x5 grid, 25 zones)
# ============================================================
GRID_SIZE = 5

# zone_id -> attractor name
ATTRACTORS: dict[int, str] = {
    12: "CBD",    # (2,2) — center
    7:  "COM",    # (2,1) — commercial district
    22: "STAD",   # (2,4) — stadium / event zone
    24: "AIR",    # (4,4) — airport hub (peripheral)
}
COMMERCIAL_NODES: set[int] = {6, 8, 11, 13, 16, 17, 18}
# All other nodes are residential.

# Attractor pull weights (sum to 1.0) — used to allocate AM/PM commuter trips.
ATTRACTOR_WEIGHTS: dict[int, float] = {12: 0.50, 7: 0.20, 24: 0.15, 22: 0.15}


def _xy(zone_id: int) -> tuple[int, int]:
    return (zone_id % GRID_SIZE, zone_id // GRID_SIZE)


def _zid(x: int, y: int) -> int:
    return y * GRID_SIZE + x


def _zone_type(zid: int) -> str:
    if zid in ATTRACTORS:
        return "attractor"
    if zid in COMMERCIAL_NODES:
        return "commercial"
    return "residential"


# ============================================================
# Parameters
# ============================================================
TimeOfDay = Literal["am_peak", "midday", "pm_peak"]
Level = Literal["low", "medium", "high"]

BG_SCALE: Mapping[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.5}
DEMAND_SCALE: Mapping[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.6}


@dataclass(frozen=True)
class SimParams:
    """All simulation knobs in one place. Frozen so it is hashable for caching."""
    # Categorical controls
    time_of_day: TimeOfDay = "am_peak"
    background_level: Level = "medium"
    demand_level: Level = "medium"

    # Continuous controls
    robotaxi_share: float = 0.30          # 0.0–1.0 of commuter trips
    deadhead_ratio: float = 0.15          # 0.0–0.5 empty RT trips / loaded RT trips
    fleet_cap: int = 2000                 # binding -> unmet demand + surge

    # Mode split for the (1 - robotaxi_share) remainder; rest is transit (off-road).
    personal_car_share_of_remainder: float = 0.7

    # Calibration scalars (exposed for sensitivity tests).
    trips_per_vehicle_per_period: float = 4.0
    vehicle_occupancy: float = 1.2
    free_flow_speed_kmh: float = 40.0
    bpr_alpha: float = 0.15
    bpr_beta: float = 4.0

    seed: int = 42


# ============================================================
# Graph construction
# ============================================================
def build_graph(seed: int = 42) -> nx.DiGraph:
    rng = np.random.default_rng(seed)
    G = nx.DiGraph()

    for zid in range(GRID_SIZE * GRID_SIZE):
        x, y = _xy(zid)
        zt = _zone_type(zid)
        if zt == "residential":
            pop = int(rng.integers(2500, 3500))
            base_trip = 0.18
            parking = int(rng.integers(200, 400))
            infra = False
        elif zt == "commercial":
            pop = int(rng.integers(600, 1000))
            base_trip = 0.07
            parking = int(rng.integers(400, 700))
            infra = True
        else:  # attractor
            pop = int(rng.integers(200, 500))
            base_trip = 0.05
            parking = int(rng.integers(300, 600))
            infra = True
        G.add_node(
            zid,
            zone_id=zid,
            pos=(x, y),
            zone_type=zt,
            population=pop,
            base_trip_demand=base_trip,
            parking_capacity=parking,
            has_pickup_dropoff_infra=infra,
        )

    def _add_edge(u: int, v: int, capacity: int, distance: float) -> None:
        G.add_edge(u, v, capacity=capacity, distance_km=distance,
                   baseline_flow=0.4 * capacity)

    # Grid edges (both directions). Inbound to attractors gets higher capacity.
    for y in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            u = _zid(x, y)
            for dx, dy in ((1, 0), (0, 1)):
                if x + dx >= GRID_SIZE or y + dy >= GRID_SIZE:
                    continue
                v = _zid(x + dx, y + dy)
                dist = float(np.hypot(dx, dy))
                cap_uv = 1500 if v in ATTRACTORS else (900 if u in ATTRACTORS else 1000)
                cap_vu = 1500 if u in ATTRACTORS else (900 if v in ATTRACTORS else 1000)
                _add_edge(u, v, cap_uv, dist)
                _add_edge(v, u, cap_vu, dist)

    # Diagonal arterials — give the network non-Manhattan shortest paths.
    arterials = [(12, 0), (12, 4), (12, 20), (12, 24), (7, 2), (22, 20)]
    for a, b in arterials:
        ax, ay = _xy(a); bx, by = _xy(b)
        dist = float(np.hypot(bx - ax, by - ay))
        _add_edge(a, b, 2000, dist)
        _add_edge(b, a, 2000, dist)

    return G


# ============================================================
# Demand matrices
# ============================================================
def get_demand_matrix(time_of_day: str, demand_level: str,
                      G: nx.DiGraph) -> dict[tuple[int, int], float]:
    """Return {(origin, dest): trips} for the given time-of-day and demand scale."""
    scale = DEMAND_SCALE[demand_level]
    demand: dict[tuple[int, int], float] = {}

    residential = [n for n, d in G.nodes(data=True) if d["zone_type"] == "residential"]
    commercial = [n for n, d in G.nodes(data=True) if d["zone_type"] == "commercial"]
    attractors = list(ATTRACTORS.keys())

    if time_of_day == "am_peak":
        for r in residential:
            base = G.nodes[r]["population"] * G.nodes[r]["base_trip_demand"] * scale
            for a, w in ATTRACTOR_WEIGHTS.items():
                demand[(r, a)] = demand.get((r, a), 0.0) + base * w

    elif time_of_day == "pm_peak":
        total_res_pop = sum(G.nodes[r]["population"] for r in residential)
        total_morning = sum(
            G.nodes[r]["population"] * G.nodes[r]["base_trip_demand"] * scale
            for r in residential
        )
        for a, w in ATTRACTOR_WEIGHTS.items():
            outflow = total_morning * w
            for r in residential:
                share = G.nodes[r]["population"] / total_res_pop
                demand[(a, r)] = demand.get((a, r), 0.0) + outflow * share

    else:  # midday — diffuse exchange among commercial + attractor zones
        pool = commercial + attractors
        for o in pool:
            for d in pool:
                if o == d:
                    continue
                v = 0.015 * (G.nodes[o]["population"] + G.nodes[d]["population"]) * scale
                demand[(o, d)] = v

    return demand


# ============================================================
# Routing
# ============================================================
def _all_pairs_paths(G: nx.DiGraph) -> dict:
    return dict(nx.all_pairs_dijkstra_path(G, weight="distance_km"))


def _assign_aon(G: nx.DiGraph, od_flow: dict[tuple[int, int], float],
                paths: dict) -> dict[tuple[int, int], float]:
    flow: dict[tuple[int, int], float] = {e: 0.0 for e in G.edges()}
    for (o, d), f in od_flow.items():
        if f <= 0 or o == d:
            continue
        p = paths[o][d]
        for u, v in zip(p[:-1], p[1:]):
            flow[(u, v)] += f
    return flow


# ============================================================
# Core simulation
# ============================================================
def _simulate_internal(G: nx.DiGraph, params: SimParams) -> dict:
    demand = get_demand_matrix(params.time_of_day, params.demand_level, G)
    paths = _all_pairs_paths(G)

    # ---- Mode split ----
    rt_share = params.robotaxi_share
    pc_share = (1 - rt_share) * params.personal_car_share_of_remainder
    rt_trips = {od: f * rt_share for od, f in demand.items()}
    pc_trips = {od: f * pc_share for od, f in demand.items()}

    # ---- Fleet cap (proportional rejection across all RT OD pairs) ----
    rt_requested = sum(rt_trips.values())
    rt_capacity = params.fleet_cap * params.trips_per_vehicle_per_period
    if rt_requested > rt_capacity > 0:
        served = rt_capacity / rt_requested
        rt_trips = {od: f * served for od, f in rt_trips.items()}
        unmet_demand = rt_requested - rt_capacity
        surge = True
    else:
        unmet_demand = 0.0
        surge = False

    # ---- Trips -> on-road vehicles ----
    pc_vehicle = {od: f / params.vehicle_occupancy for od, f in pc_trips.items()}
    rt_vehicle = dict(rt_trips)  # one rider per robo-taxi trip

    # ---- AON assignment of loaded flows ----
    pc_flow = _assign_aon(G, pc_vehicle, paths)
    rt_flow = _assign_aon(G, rt_vehicle, paths)

    # ---- Deadheading: PageRank-weighted repositioning ----
    dropoffs: dict[int, float] = {}
    for (_, d), f in rt_vehicle.items():
        dropoffs[d] = dropoffs.get(d, 0.0) + f

    origin_demand: dict[int, float] = {}
    for (o, _), f in demand.items():
        origin_demand[o] = origin_demand.get(o, 0.0) + f
    if sum(origin_demand.values()) > 0:
        pr = nx.pagerank(G, alpha=0.85, personalization=origin_demand)
    else:
        pr = {n: 1.0 / G.number_of_nodes() for n in G.nodes()}

    total_rt = sum(rt_vehicle.values())
    empty_total = params.deadhead_ratio * total_rt
    dh_od: dict[tuple[int, int], float] = {}
    if empty_total > 0 and total_rt > 0:
        drop_total = sum(dropoffs.values())
        pr_sum = sum(pr.values())
        for j, dj in dropoffs.items():
            if dj <= 0:
                continue
            remain = pr_sum - pr[j]
            if remain <= 0:
                continue
            for k, pk in pr.items():
                if k == j:
                    continue
                dh_od[(j, k)] = dh_od.get((j, k), 0.0) + \
                    empty_total * (dj / drop_total) * (pk / remain)
    dh_flow = _assign_aon(G, dh_od, paths)

    # ---- Background + total edge flow ----
    bg_mult = BG_SCALE[params.background_level]
    edge_flow: dict[tuple[int, int], float] = {}
    bg_flow: dict[tuple[int, int], float] = {}
    for u, v, data in G.edges(data=True):
        bg = data["baseline_flow"] * bg_mult
        bg_flow[(u, v)] = bg
        edge_flow[(u, v)] = bg + pc_flow[(u, v)] + rt_flow[(u, v)] + dh_flow[(u, v)]

    # ---- BPR travel times ----
    travel_time: dict[tuple[int, int], float] = {}
    utilization: dict[tuple[int, int], float] = {}
    for e in G.edges():
        cap = G.edges[e]["capacity"]
        util = edge_flow[e] / cap if cap > 0 else 0.0
        utilization[e] = util
        fft = G.edges[e]["distance_km"] / params.free_flow_speed_kmh
        travel_time[e] = fft * (1 + params.bpr_alpha * util ** params.bpr_beta)

    # ---- Trip-weighted average travel time over all served commuters ----
    total_trip_time = 0.0
    total_trips = 0.0
    for (o, d), f in demand.items():
        if f <= 0 or o == d:
            continue
        p = paths[o][d]
        t = sum(travel_time[(u, v)] for u, v in zip(p[:-1], p[1:]))
        total_trip_time += t * f
        total_trips += f
    avg_trip_time_min = (total_trip_time / total_trips * 60.0) if total_trips > 0 else 0.0

    # ---- VMT ----
    total_vmt = sum(edge_flow[e] * G.edges[e]["distance_km"] for e in G.edges())
    deadhead_vmt = sum(dh_flow[e] * G.edges[e]["distance_km"] for e in G.edges())
    commuter_vmt = sum(
        (pc_flow[e] + rt_flow[e]) * G.edges[e]["distance_km"] for e in G.edges()
    )

    congested = sum(1 for u in utilization.values() if u > 0.9)

    return {
        "edge_flow": edge_flow,
        "bg_flow": bg_flow,
        "pc_flow": pc_flow,
        "rt_flow": rt_flow,
        "dh_flow": dh_flow,
        "utilization": utilization,
        "travel_time_hr": travel_time,
        "total_vmt": total_vmt,
        "commuter_vmt": commuter_vmt,
        "deadhead_vmt": deadhead_vmt,
        "avg_trip_time_min": avg_trip_time_min,
        "congested_edge_count": congested,
        "unmet_demand": unmet_demand,
        "surge_flag": surge,
        "total_trips": total_trips,
        "pagerank": pr,
    }


def simulate(G: nx.DiGraph, params: SimParams) -> dict:
    """Full simulation result (flow dicts + scalar readouts)."""
    return _simulate_internal(G, params)


def compute_flows(G: nx.DiGraph, params: SimParams) -> dict[tuple[int, int], float]:
    """Total edge flow (background + personal car + loaded RT + deadhead RT)."""
    return _simulate_internal(G, params)["edge_flow"]


def compute_vmt(flows: dict[tuple[int, int], float], G: nx.DiGraph) -> float:
    """VMT for an arbitrary flow dict: sum(flow * distance_km)."""
    return float(sum(f * G.edges[e]["distance_km"] for e, f in flows.items()))


def get_baseline_vmt(G: nx.DiGraph, params: SimParams) -> float:
    """VMT with robo-taxi share forced to 0 at the same demand/TOD/background."""
    baseline = replace(params, robotaxi_share=0.0, deadhead_ratio=0.0)
    return _simulate_internal(G, baseline)["total_vmt"]
