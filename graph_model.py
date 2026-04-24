"""Robo-taxi congestion model on the real Los Angeles road network.

Pure logic, no UI imports. Safe to import from notebooks or other analyses
(centrality, game theory, adoption dynamics).

SimParams has the same fields as the original synthetic-grid version — all
downstream code that built on it continues to work.

Public API:
    SimParams                                      — frozen dataclass of all knobs
    build_graph(seed=42) -> nx.DiGraph             — loads LA network from cache
    get_demand_matrix(tod, level, G) -> {(o,d): trips}
    compute_flows(G, params) -> {edge: flow}       — total flow per edge
    compute_metrics(flows, G) -> dict              — VMT + derived scalars
    compute_vmt(flows, G) -> float                 — back-compat
    get_baseline_vmt(G, params) -> float           — robotaxi_share=0 variant
    run_scenario(G, params) -> dict                — full result dict
    simulate(G, params) -> dict                    — back-compat alias
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping

import networkx as nx

from la_network import load_la_network, DOWNTOWN_REF
from calibration import apply_calibration


# ============================================================
# Parameters — same schema as the synthetic-grid version
# ============================================================
TimeOfDay = Literal["am_peak", "midday", "pm_peak"]
Level = Literal["low", "medium", "high"]

BG_SCALE: Mapping[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.5}
DEMAND_SCALE: Mapping[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.6}


@dataclass(frozen=True)
class SimParams:
    time_of_day: TimeOfDay = "am_peak"
    background_level: Level = "medium"
    demand_level: Level = "medium"

    robotaxi_share: float = 0.30
    deadhead_ratio: float = 0.15
    fleet_cap: int = 2000

    personal_car_share_of_remainder: float = 0.7

    trips_per_vehicle_per_period: float = 4.0
    vehicle_occupancy: float = 1.2
    free_flow_speed_kmh: float = 40.0   # unused on LA graph; per-edge time is authoritative
    bpr_alpha: float = 0.15
    bpr_beta: float = 4.0

    seed: int = 42


# ============================================================
# Graph loader
# ============================================================
def build_graph(seed: int = 42) -> nx.DiGraph:
    """Load the cached LA road network. Falls through to OSMnx on first call."""
    return load_la_network()


# ============================================================
# Demand
# ============================================================
BASE_TRIP_RATE = 0.10   # commuter trips per unit of origin weight per period


def _origin_weights(G: nx.DiGraph) -> dict:
    """Population proxy: throughput nodes weighted by distance from downtown
    (farther -> higher residential density proxy)."""
    out: dict[int, float] = {}
    for n, d in G.nodes(data=True):
        if d["zone_type"] == "attractor":
            continue
        dist_m = float(d.get("landmark_dist_m", 5000))
        out[n] = min(3000.0, 500.0 + 0.4 * dist_m)
    return out


def get_demand_matrix(time_of_day: str, demand_level: str,
                      G: nx.DiGraph) -> dict[tuple, float]:
    scale = DEMAND_SCALE[demand_level]
    demand: dict[tuple, float] = {}

    origin_w = _origin_weights(G)
    throughput = list(origin_w.keys())
    attractors = [n for n, d in G.nodes(data=True) if d["zone_type"] == "attractor"]
    if not attractors or not throughput:
        return demand

    n_attr = len(attractors)

    if time_of_day == "am_peak":
        for o in throughput:
            total_from_o = origin_w[o] * BASE_TRIP_RATE * scale
            per_attr = total_from_o / n_attr
            for a in attractors:
                demand[(o, a)] = per_attr

    elif time_of_day == "pm_peak":
        total_pop = sum(origin_w.values())
        total_morning = sum(origin_w[o] * BASE_TRIP_RATE * scale for o in throughput)
        per_attr_outflow = total_morning / n_attr
        for a in attractors:
            for o in throughput:
                share = origin_w[o] / total_pop
                demand[(a, o)] = per_attr_outflow * share

    else:  # midday — diffuse exchange, attractor ↔ attractor plus light background
        for o in attractors:
            for d in attractors:
                if o == d:
                    continue
                demand[(o, d)] = 200.0 * scale
        # Light throughput activity (shopping/errands)
        for o in throughput[::2]:
            for d in attractors:
                demand[(o, d)] = origin_w[o] * 0.02 * scale

    return demand


# ============================================================
# Routing
# ============================================================
def _shortest_paths(G: nx.DiGraph) -> dict:
    return dict(nx.all_pairs_dijkstra_path(G, weight="length_km"))


def _assign_aon(G: nx.DiGraph, od_flow: dict[tuple, float],
                paths: dict) -> dict[tuple, float]:
    flow: dict[tuple, float] = {e: 0.0 for e in G.edges()}
    for (o, d), f in od_flow.items():
        if f <= 0 or o == d:
            continue
        if o not in paths or d not in paths[o]:
            continue
        p = paths[o][d]
        for u, v in zip(p[:-1], p[1:]):
            if (u, v) in flow:
                flow[(u, v)] += f
    return flow


# ============================================================
# Core simulation
# ============================================================
def _simulate_internal(G: nx.DiGraph, params: SimParams) -> dict:
    # 1. Apply calibration to set baseline_flow_vph per edge for this TOD.
    apply_calibration(G, params.time_of_day)

    # 2. Demand + routing.
    demand = get_demand_matrix(params.time_of_day, params.demand_level, G)
    paths = _shortest_paths(G)

    rt_share = params.robotaxi_share
    pc_share = (1 - rt_share) * params.personal_car_share_of_remainder

    rt_trips = {od: f * rt_share for od, f in demand.items()}
    pc_trips = {od: f * pc_share for od, f in demand.items()}

    # 3. Fleet cap (proportional rejection).
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

    # 4. Convert trips to vehicle counts.
    pc_vehicle = {od: f / params.vehicle_occupancy for od, f in pc_trips.items()}
    rt_vehicle = dict(rt_trips)

    # 5. Assign loaded flows.
    pc_flow = _assign_aon(G, pc_vehicle, paths)
    rt_flow = _assign_aon(G, rt_vehicle, paths)

    # 6. Deadheading: PageRank-weighted repositioning from dropoff -> next-pickup.
    dropoffs: dict = {}
    for (_, d), f in rt_vehicle.items():
        dropoffs[d] = dropoffs.get(d, 0.0) + f

    origin_demand: dict = {}
    for (o, _), f in demand.items():
        origin_demand[o] = origin_demand.get(o, 0.0) + f
    if sum(origin_demand.values()) > 0:
        pr = nx.pagerank(G, alpha=0.85, personalization=origin_demand)
    else:
        pr = {n: 1.0 / G.number_of_nodes() for n in G.nodes()}

    total_rt = sum(rt_vehicle.values())
    empty_total = params.deadhead_ratio * total_rt
    dh_od: dict = {}
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

    # 7. Total edge flow = calibrated background + loaded + deadhead.
    bg_mult = BG_SCALE[params.background_level]
    edge_flow: dict = {}
    bg_flow: dict = {}
    for u, v, data in G.edges(data=True):
        bg = data["baseline_flow_vph"] * bg_mult
        bg_flow[(u, v)] = bg
        edge_flow[(u, v)] = bg + pc_flow[(u, v)] + rt_flow[(u, v)] + dh_flow[(u, v)]

    # 8. BPR travel time per edge (minutes).
    travel_time: dict = {}
    utilization: dict = {}
    for (u, v), data in ((e, G.edges[e]) for e in G.edges()):
        cap = data["capacity_vph"]
        util = edge_flow[(u, v)] / cap if cap > 0 else 0.0
        utilization[(u, v)] = util
        fft = data["free_flow_time_min"]
        travel_time[(u, v)] = fft * (1 + params.bpr_alpha * util ** params.bpr_beta)

    # 9. Trip-weighted average travel time.
    total_trip_time = 0.0
    total_trips = 0.0
    for (o, d), f in demand.items():
        if f <= 0 or o == d:
            continue
        if o not in paths or d not in paths[o]:
            continue
        p = paths[o][d]
        t = sum(travel_time[(u, v)] for u, v in zip(p[:-1], p[1:]))
        total_trip_time += t * f
        total_trips += f
    avg_trip_time_min = total_trip_time / total_trips if total_trips > 0 else 0.0

    # 10. VMT breakdown (flow × length_km).
    total_vmt = sum(edge_flow[e] * G.edges[e]["length_km"] for e in G.edges())
    deadhead_vmt = sum(dh_flow[e] * G.edges[e]["length_km"] for e in G.edges())
    commuter_vmt = sum(
        (pc_flow[e] + rt_flow[e]) * G.edges[e]["length_km"] for e in G.edges()
    )
    congested = sum(1 for u in utilization.values() if u > 0.9)

    # 11. Most-congested edges (top 5) for the "Road inspection" panel.
    congested_edges = sorted(
        G.edges(),
        key=lambda e: utilization[e],
        reverse=True,
    )[:5]
    top_congested = []
    for e in congested_edges:
        u, v = e
        delay_min = travel_time[e] - G.edges[e]["free_flow_time_min"]
        top_congested.append({
            "edge": (u, v),
            "road_name": G.edges[e]["road_name"],
            "direction": G.edges[e].get("direction", ""),
            "utilization": round(utilization[e], 3),
            "flow_vph": round(edge_flow[e], 0),
            "capacity_vph": G.edges[e]["capacity_vph"],
            "delay_min": round(delay_min, 2),
        })

    return {
        "edge_flow": edge_flow,
        "bg_flow": bg_flow,
        "pc_flow": pc_flow,
        "rt_flow": rt_flow,
        "dh_flow": dh_flow,
        "utilization": utilization,
        "travel_time_min": travel_time,
        "total_vmt": total_vmt,
        "commuter_vmt": commuter_vmt,
        "deadhead_vmt": deadhead_vmt,
        "avg_trip_time_min": avg_trip_time_min,
        "congested_edge_count": congested,
        "unmet_demand": unmet_demand,
        "surge_flag": surge,
        "total_trips": total_trips,
        "pagerank": pr,
        "top_congested": top_congested,
    }


# ============================================================
# Public entry points
# ============================================================
def run_scenario(G: nx.DiGraph, params: SimParams) -> dict:
    """Full scenario result: flow dicts + scalar readouts."""
    return _simulate_internal(G, params)


# Back-compat alias — keep existing notebooks from the synthetic-grid phase working.
simulate = run_scenario


def compute_flows(G: nx.DiGraph, params: SimParams) -> dict[tuple, float]:
    """Total edge flow (background + personal + loaded RT + deadhead)."""
    return _simulate_internal(G, params)["edge_flow"]


def compute_metrics(flows: dict[tuple, float], G: nx.DiGraph) -> dict:
    """Derive VMT + per-edge utilization from a flow dict."""
    utilization = {
        e: flows[e] / G.edges[e]["capacity_vph"] if G.edges[e]["capacity_vph"] > 0 else 0.0
        for e in G.edges() if e in flows
    }
    total_vmt = sum(flows[e] * G.edges[e]["length_km"] for e in flows)
    return {
        "total_vmt": total_vmt,
        "utilization": utilization,
        "congested_edge_count": sum(1 for u in utilization.values() if u > 0.9),
    }


def compute_vmt(flows: dict[tuple, float], G: nx.DiGraph) -> float:
    """Back-compat: VMT from a flow dict."""
    return float(sum(f * G.edges[e]["length_km"] for e, f in flows.items()))


def get_baseline_vmt(G: nx.DiGraph, params: SimParams) -> float:
    """VMT with robotaxi_share=0 at the same demand/TOD/background."""
    baseline = replace(params, robotaxi_share=0.0, deadhead_ratio=0.0)
    return _simulate_internal(G, baseline)["total_vmt"]
