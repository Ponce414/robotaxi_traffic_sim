"""Real Los Angeles road network fetched from OpenStreetMap via OSMnx.

Replaces the synthetic 25-zone grid. Produces a simplified nx.DiGraph of
downtown LA + the surrounding freeway ring, enriched with the attributes
the congestion model needs (capacity, free-flow time, road name, zone_type,
direction vs downtown).

Public API:
    load_la_network(force_rebuild=False, cache_path=DEFAULT_CACHE) -> nx.DiGraph
    DOWNTOWN_REF                                                  — (lat, lon)
    LANDMARKS                                                     — attractor seeds
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path
from typing import Iterable

import networkx as nx

# OSMnx and geopandas are heavy deps; import lazily inside load_la_network
# so that importing this module from a notebook that already has a cached
# graph doesn't require them to be installed.

# ============================================================
# Area definition
# ============================================================
BBOX_NORTH = 34.12
BBOX_SOUTH = 33.98
BBOX_EAST = -118.17
BBOX_WEST = -118.32

# Downtown reference point for inbound/outbound classification.
DOWNTOWN_REF = (34.05, -118.25)

# Named landmarks used to tag attractor nodes. Distance threshold below.
LANDMARKS: dict[str, tuple[float, float]] = {
    "Downtown Core":   (34.0522, -118.2437),
    "LA Live":         (34.0430, -118.2673),
    "Union Station":   (34.0560, -118.2365),
    "USC":             (34.0224, -118.2851),
    "Staples/Arena":   (34.0430, -118.2673),
    "Financial Dist":  (34.0488, -118.2570),
    "Koreatown":       (34.0580, -118.3004),
}
ATTRACTOR_RADIUS_M = 1500.0  # node within this distance of a landmark -> attractor


# ============================================================
# Road-type fallbacks
# ============================================================
# When OSM doesn't tag lanes or maxspeed, use these by highway type.
ROAD_TYPE_LANES: dict[str, int] = {
    "motorway": 3, "motorway_link": 2,
    "trunk": 2,    "trunk_link": 1,
    "primary": 2,  "primary_link": 1,
    "secondary": 2, "secondary_link": 1,
}
ROAD_TYPE_SPEED_KPH: dict[str, float] = {
    "motorway": 105, "motorway_link": 70,
    "trunk":    85,  "trunk_link":    55,
    "primary":  65,  "primary_link":  40,
    "secondary":50,  "secondary_link":35,
}
LANE_CAPACITY_VPH = 1800.0  # standard transportation-engineering per-lane capacity


# Road-name alias map (OSM is inconsistent about freeways).
ROAD_NAME_ALIASES: dict[str, str] = {
    "harbor freeway": "I-110",
    "pasadena freeway": "I-110",
    "i 110": "I-110", "i-110": "I-110", "ca-110": "I-110", "110": "I-110",
    "santa monica freeway": "I-10",
    "i 10": "I-10", "i-10": "I-10", "10": "I-10",
    "hollywood freeway": "US-101",
    "us 101": "US-101", "us-101": "US-101", "101": "US-101",
    "golden state freeway": "I-5",
    "i 5": "I-5", "i-5": "I-5", "5": "I-5",
    "san bernardino freeway": "I-10",
    "glendale freeway": "CA-2",
    "arroyo seco parkway": "I-110",
}


# ============================================================
# Caching
# ============================================================
DEFAULT_CACHE = Path(__file__).parent / "cache" / "la_network.pkl"


def _cache_path(path: str | Path | None) -> Path:
    return Path(path) if path is not None else DEFAULT_CACHE


# ============================================================
# Helpers
# ============================================================
def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    R = 6_371_000.0
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _first(v):
    """OSM tags can be str or list-of-str. Return the first usable value."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _normalize_road_name(raw) -> str:
    name = _first(raw)
    if not name:
        return ""
    key = str(name).strip().lower()
    return ROAD_NAME_ALIASES.get(key, str(name).strip())


_HIGHWAY_RANK = {
    "motorway": 0, "trunk": 1, "primary": 2,
    "motorway_link": 3, "trunk_link": 4, "primary_link": 5,
    "secondary": 6, "secondary_link": 7,
}


def _highway_class(hw) -> str:
    """When OSM tags are lists, prefer the highest-class road (motorway wins)."""
    if hw is None:
        return "secondary"
    if isinstance(hw, list):
        if not hw:
            return "secondary"
        return min(hw, key=lambda s: _HIGHWAY_RANK.get(str(s), 99))
    return str(hw)


def _parse_lanes(lanes, highway: str) -> int:
    l = _first(lanes)
    if l is None:
        return ROAD_TYPE_LANES.get(highway, 1)
    try:
        return max(1, int(float(l)))
    except (ValueError, TypeError):
        return ROAD_TYPE_LANES.get(highway, 1)


def _parse_maxspeed_kph(maxspeed, highway: str) -> float:
    ms = _first(maxspeed)
    if ms is None:
        return ROAD_TYPE_SPEED_KPH.get(highway, 50)
    s = str(ms).lower().strip()
    try:
        if "mph" in s:
            return float(s.replace("mph", "").strip()) * 1.60934
        return float(s.replace("km/h", "").replace("kph", "").strip())
    except ValueError:
        return ROAD_TYPE_SPEED_KPH.get(highway, 50)


def _bearing_deg(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _direction_vs_ref(u_latlon: tuple[float, float],
                      v_latlon: tuple[float, float],
                      ref: tuple[float, float]) -> str:
    """Edge direction relative to the downtown reference.

    Inbound: midpoint-to-ref bearing lines up with edge bearing (moving closer).
    Outbound: opposite.
    """
    du = _haversine_m(u_latlon, ref)
    dv = _haversine_m(v_latlon, ref)
    return "inbound" if dv < du else "outbound"


def _nearest_landmark(lat: float, lon: float) -> tuple[str | None, float]:
    best_name, best_d = None, float("inf")
    for name, ll in LANDMARKS.items():
        d = _haversine_m((lat, lon), ll)
        if d < best_d:
            best_d, best_name = d, name
    return best_name, best_d


# ============================================================
# Main build
# ============================================================
def build_la_network() -> nx.DiGraph:
    """Fetch and simplify the LA network. Slow (hits OSM). Prefer load_la_network()."""
    import osmnx as ox
    ox.settings.log_console = False
    ox.settings.use_cache = True

    cf = '["highway"~"motorway|trunk|primary|motorway_link|trunk_link"]'
    G_raw = ox.graph_from_bbox(
        bbox=(BBOX_WEST, BBOX_SOUTH, BBOX_EAST, BBOX_NORTH),
        network_type="drive",
        custom_filter=cf,
        simplify=True,
        retain_all=False,
    )

    # Project to meters for geometric consolidation.
    G_proj = ox.project_graph(G_raw)
    G_cons = ox.simplification.consolidate_intersections(
        G_proj, tolerance=200, rebuild_graph=True, dead_ends=False, reconnect_edges=True,
    )
    # Back to lat/lon for our purposes.
    G_ll = ox.project_graph(G_cons, to_crs="EPSG:4326")

    # Convert MultiDiGraph -> DiGraph, keeping the richest edge per (u,v).
    G = nx.DiGraph()
    for n, data in G_ll.nodes(data=True):
        lat = float(data.get("y") or data.get("lat"))
        lon = float(data.get("x") or data.get("lon"))
        landmark, dist_m = _nearest_landmark(lat, lon)
        zone_type = "attractor" if dist_m <= ATTRACTOR_RADIUS_M else "throughput"
        G.add_node(
            n,
            node_id=n,
            lat=lat, lon=lon,
            pos=(lon, lat),  # Plotly (x, y) convention
            district=landmark or "unknown",
            nearest_landmark=landmark,
            landmark_dist_m=dist_m,
            zone_type=zone_type,
        )

    for u, v, data in G_ll.edges(data=True):
        if u == v or not G.has_node(u) or not G.has_node(v):
            continue
        highway = _highway_class(data.get("highway"))
        lanes = _parse_lanes(data.get("lanes"), highway)
        speed_kph = _parse_maxspeed_kph(data.get("maxspeed"), highway)
        length_m = float(data.get("length", 0.0) or 0.0)
        length_km = length_m / 1000.0
        capacity = lanes * LANE_CAPACITY_VPH
        fft_min = (length_km / speed_kph) * 60.0 if speed_kph > 0 else 1.0

        road_name = _normalize_road_name(data.get("name")) or f"[{highway}]"
        direction = _direction_vs_ref(
            (G.nodes[u]["lat"], G.nodes[u]["lon"]),
            (G.nodes[v]["lat"], G.nodes[v]["lon"]),
            DOWNTOWN_REF,
        )

        # OSMnx may give a geometry LineString; fall back to straight line.
        geom = data.get("geometry")
        if geom is not None and hasattr(geom, "coords"):
            coords = [(float(x), float(y)) for x, y in geom.coords]  # (lon, lat)
        else:
            coords = [
                (G.nodes[u]["lon"], G.nodes[u]["lat"]),
                (G.nodes[v]["lon"], G.nodes[v]["lat"]),
            ]

        # If (u,v) already exists (multi-parallel OSM edges), keep the higher-capacity one.
        if G.has_edge(u, v) and G.edges[u, v]["capacity_vph"] >= capacity:
            continue

        G.add_edge(
            u, v,
            road_name=road_name,
            highway=highway,
            lanes=lanes,
            length_km=length_km,
            distance_km=length_km,  # alias so graph_model.py stays symmetric
            speed_kph=speed_kph,
            free_flow_time_min=fft_min,
            capacity_vph=capacity,
            capacity=capacity,      # alias
            baseline_flow_vph=0.5 * capacity,  # overwritten by calibration
            baseline_flow=0.5 * capacity,      # alias
            direction=direction,
            geometry_lonlat=coords,
        )

    return G


def _filter_largest_scc(G: nx.DiGraph) -> nx.DiGraph:
    """Keep only the largest strongly connected component so all pairs are routable."""
    if nx.is_strongly_connected(G):
        return G
    comps = list(nx.strongly_connected_components(G))
    comps.sort(key=len, reverse=True)
    largest = comps[0]
    return G.subgraph(largest).copy()


def load_la_network(force_rebuild: bool = False,
                    cache_path: str | Path | None = None) -> nx.DiGraph:
    """Load the LA network from the pickle cache. Rebuild on demand."""
    path = _cache_path(cache_path)
    if path.exists() and not force_rebuild:
        with path.open("rb") as f:
            return pickle.load(f)

    G = build_la_network()
    G = _filter_largest_scc(G)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(G, f)
    return G


def coverage_stats(G: nx.DiGraph) -> dict:
    lats = [G.nodes[n]["lat"] for n in G.nodes()]
    lons = [G.nodes[n]["lon"] for n in G.nodes()]
    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "attractors": sum(1 for n, d in G.nodes(data=True) if d["zone_type"] == "attractor"),
        "lat_range": (min(lats), max(lats)),
        "lon_range": (min(lons), max(lons)),
        "total_lane_km": round(sum(
            d["length_km"] * d["lanes"] for _, _, d in G.edges(data=True)
        ), 1),
        "road_types": _count(d["highway"] for _, _, d in G.edges(data=True)),
    }


def _count(items: Iterable) -> dict:
    out: dict = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
