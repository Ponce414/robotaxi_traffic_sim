"""Visual-calibration data from Google Maps' typical-traffic layer.

Fill in observed flow/capacity ratios by eyeballing Google Maps at 8 AM,
12 PM, and 5 PM on a typical weekday. Placeholders below are rough
sketches — replace with real observations before presenting results.

Resolution order in apply_calibration():
    1. SEGMENT_OVERRIDES  (road + direction + nearest-cross-street key)
    2. ROAD_CALIBRATION   (road + direction)
    3. DEFAULT_RATIOS     (time of day fallback)

Then: edge["baseline_flow_vph"] = ratio * edge["capacity_vph"]
"""
from __future__ import annotations

import math
from typing import Literal

import networkx as nx

TimeOfDay = Literal["am_peak", "midday", "pm_peak"]


# ============================================================
# Road-level ratios (observed flow / capacity)
# ============================================================
# Inbound = edge bearing points toward downtown reference (34.05, -118.25).
# Outbound = away from downtown.
# Format: {road_name: {tod_key: {"inbound": ratio, "outbound": ratio}}}
#
# tod_key: "am" | "midday" | "pm"   (NOT the full SimParams.time_of_day value)
# Ratios: 0.0 (empty) -> 1.0+ (stop-and-go). 0.85+ shows up yellow/red.

ROAD_CALIBRATION: dict[str, dict[str, dict[str, float]]] = {
    "I-110": {  # Harbor / Pasadena Freeway — classic AM rush into DTLA
        "am":     {"inbound": 0.92, "outbound": 0.55},
        "midday": {"inbound": 0.55, "outbound": 0.55},
        "pm":     {"inbound": 0.60, "outbound": 0.90},
    },
    "I-10": {   # Santa Monica Freeway — heavy both directions, worse westbound PM
        "am":     {"inbound": 0.80, "outbound": 0.60},
        "midday": {"inbound": 0.60, "outbound": 0.60},
        "pm":     {"inbound": 0.65, "outbound": 0.88},
    },
    "US-101": { # Hollywood Freeway — perpetual slog, slightly worse inbound AM
        "am":     {"inbound": 0.85, "outbound": 0.70},
        "midday": {"inbound": 0.65, "outbound": 0.65},
        "pm":     {"inbound": 0.70, "outbound": 0.85},
    },
    "I-5": {
        "am":     {"inbound": 0.85, "outbound": 0.60},
        "midday": {"inbound": 0.55, "outbound": 0.55},
        "pm":     {"inbound": 0.60, "outbound": 0.85},
    },
    # Major arterials (primary roads) — less peaked, more mid-day flow
    "Wilshire Blvd": {
        "am":     {"inbound": 0.70, "outbound": 0.50},
        "midday": {"inbound": 0.60, "outbound": 0.60},
        "pm":     {"inbound": 0.55, "outbound": 0.75},
    },
    "Sunset Blvd": {
        "am":     {"inbound": 0.65, "outbound": 0.50},
        "midday": {"inbound": 0.60, "outbound": 0.60},
        "pm":     {"inbound": 0.55, "outbound": 0.70},
    },
    "Olympic Blvd": {
        "am":     {"inbound": 0.65, "outbound": 0.50},
        "midday": {"inbound": 0.55, "outbound": 0.55},
        "pm":     {"inbound": 0.50, "outbound": 0.70},
    },
    "Figueroa St": {
        "am":     {"inbound": 0.70, "outbound": 0.50},
        "midday": {"inbound": 0.55, "outbound": 0.55},
        "pm":     {"inbound": 0.55, "outbound": 0.72},
    },
}


# ============================================================
# Segment-level overrides (worse-than-road-average pain points)
# ============================================================
# Key format: "<road>_<direction>_of_<cross>"
# Resolved against a midpoint lookup: edge is considered near a cross street
# if its midpoint is within SEGMENT_MATCH_RADIUS_M of the landmark.
#
# Fill in as you observe specific pinch points on Google Maps.

SEGMENT_MATCH_RADIUS_M = 800.0

SEGMENT_LANDMARKS: dict[str, tuple[float, float]] = {
    # Named junctions used as anchors for SEGMENT_OVERRIDES keys.
    "I-10": (34.0325, -118.2585),    # Santa Monica Fwy at 110 stack
    "I-110": (34.0487, -118.2641),   # Harbor Fwy at 10 stack
    "US-101": (34.0625, -118.2395),  # 101 at downtown
    "Union Station": (34.0560, -118.2365),
    "LA Live": (34.0430, -118.2673),
    "USC": (34.0224, -118.2851),
}

SEGMENT_OVERRIDES: dict[str, dict[str, float]] = {
    # I-110 through the 10 stack — consistently worse than the 110 average.
    "I-110_inbound_near_I-10": {"am": 0.98, "midday": 0.65, "pm": 0.75},
    "I-110_outbound_near_I-10": {"am": 0.60, "midday": 0.60, "pm": 0.95},
    # I-10 East through the 110 stack.
    "I-10_inbound_near_I-110": {"am": 0.90, "midday": 0.65, "pm": 0.80},
    "I-10_outbound_near_I-110": {"am": 0.65, "midday": 0.60, "pm": 0.92},
    # 101 through downtown — perpetually red.
    "US-101_inbound_near_US-101": {"am": 0.92, "midday": 0.78, "pm": 0.80},
    "US-101_outbound_near_US-101": {"am": 0.75, "midday": 0.72, "pm": 0.92},
}


# ============================================================
# Defaults (when neither override nor road match applies)
# ============================================================
DEFAULT_RATIOS: dict[str, float] = {
    "am": 0.55, "midday": 0.45, "pm": 0.55,
}


# ============================================================
# Application
# ============================================================
def _tod_key(time_of_day: str) -> str:
    """Map SimParams.time_of_day -> calibration tod key."""
    return {"am_peak": "am", "midday": "midday", "pm_peak": "pm"}.get(
        time_of_day, "midday"
    )


def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    R = 6_371_000.0
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _edge_midpoint(G: nx.DiGraph, u: int, v: int) -> tuple[float, float]:
    coords = G.edges[u, v].get("geometry_lonlat")
    if coords and len(coords) >= 2:
        mid = coords[len(coords) // 2]
        return (mid[1], mid[0])  # lat, lon
    return (
        (G.nodes[u]["lat"] + G.nodes[v]["lat"]) / 2,
        (G.nodes[u]["lon"] + G.nodes[v]["lon"]) / 2,
    )


def _match_override(G: nx.DiGraph, u: int, v: int, tod: str) -> float | None:
    edge = G.edges[u, v]
    road = edge.get("road_name", "")
    direction = edge.get("direction", "inbound")
    mid = _edge_midpoint(G, u, v)
    for name, landmark in SEGMENT_LANDMARKS.items():
        if _haversine_m(mid, landmark) > SEGMENT_MATCH_RADIUS_M:
            continue
        key = f"{road}_{direction}_near_{name}"
        if key in SEGMENT_OVERRIDES:
            return SEGMENT_OVERRIDES[key].get(tod)
    return None


def _match_road(G: nx.DiGraph, u: int, v: int, tod: str) -> float | None:
    edge = G.edges[u, v]
    road = edge.get("road_name", "")
    direction = edge.get("direction", "inbound")
    if road in ROAD_CALIBRATION:
        return ROAD_CALIBRATION[road].get(tod, {}).get(direction)
    return None


def resolve_ratio(G: nx.DiGraph, u: int, v: int, time_of_day: str) -> float:
    """Pick the best-matching ratio for one edge at the given time."""
    tod = _tod_key(time_of_day)
    r = _match_override(G, u, v, tod)
    if r is not None:
        return r
    r = _match_road(G, u, v, tod)
    if r is not None:
        return r
    return DEFAULT_RATIOS[tod]


def apply_calibration(G: nx.DiGraph, time_of_day: str) -> nx.DiGraph:
    """Update each edge's baseline_flow_vph to calibrated ratio × capacity."""
    for u, v in G.edges():
        ratio = resolve_ratio(G, u, v, time_of_day)
        cap = G.edges[u, v].get("capacity_vph", G.edges[u, v].get("capacity", 0))
        base = ratio * cap
        G.edges[u, v]["baseline_flow_vph"] = base
        G.edges[u, v]["baseline_flow"] = base  # alias
        G.edges[u, v]["calibrated_ratio"] = ratio
    return G


def calibration_coverage(G: nx.DiGraph, time_of_day: str) -> dict:
    """Report how many edges hit overrides / road match / default — useful
    for the presenter: 'X% of edges are calibrated, the rest use defaults.'"""
    tod = _tod_key(time_of_day)
    override = road = fallback = 0
    for u, v in G.edges():
        if _match_override(G, u, v, tod) is not None:
            override += 1
        elif _match_road(G, u, v, tod) is not None:
            road += 1
        else:
            fallback += 1
    total = G.number_of_edges()
    return {
        "override": override,
        "road": road,
        "fallback": fallback,
        "calibrated_fraction": round((override + road) / total, 3) if total else 0,
    }
