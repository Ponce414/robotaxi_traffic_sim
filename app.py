"""Streamlit UI for the LA robo-taxi congestion model.

Run with:
    streamlit run app.py

Model lives in graph_model.py. la_network.py fetches the OSM graph.
calibration.py holds Google-Maps-observed congestion ratios.
"""
from __future__ import annotations

import networkx as nx
import plotly.graph_objects as go
import streamlit as st

from graph_model import SimParams, build_graph, get_baseline_vmt, run_scenario
from la_network import DOWNTOWN_REF
from calibration import calibration_coverage

st.set_page_config(page_title="LA Robo-Taxi Congestion Study", layout="wide")


# ============================================================
# Caching
# ============================================================
@st.cache_resource
def _cached_graph() -> nx.DiGraph:
    return build_graph()


@st.cache_data(show_spinner=False)
def _cached_run(time_of_day: str, background_level: str, demand_level: str,
                robotaxi_share: float, deadhead_ratio: float,
                fleet_cap: int, seed: int) -> tuple[dict, float]:
    G = _cached_graph()
    params = SimParams(
        time_of_day=time_of_day,
        background_level=background_level,
        demand_level=demand_level,
        robotaxi_share=robotaxi_share,
        deadhead_ratio=deadhead_ratio,
        fleet_cap=fleet_cap,
        seed=seed,
    )
    result = run_scenario(G, params)
    baseline_vmt = get_baseline_vmt(G, params)
    return result, baseline_vmt


# ============================================================
# Map figure
# ============================================================
UTIL_COLORS = {
    "green":  "#2ecc71",
    "yellow": "#f1c40f",
    "red":    "#e74c3c",
}


def _bucket(util: float) -> str:
    if util < 0.6:
        return "green"
    if util < 0.9:
        return "yellow"
    return "red"


def build_map(G: nx.DiGraph, result: dict) -> go.Figure:
    util = result["utilization"]
    edge_flow = result["edge_flow"]
    tt = result["travel_time_min"]

    fig = go.Figure()

    # One trace per bucket so Plotly assigns a single line color.
    for bucket_name, color in UTIL_COLORS.items():
        lats: list[float | None] = []
        lons: list[float | None] = []
        for (u, v), ut in util.items():
            if _bucket(ut) != bucket_name:
                continue
            coords = G.edges[u, v].get("geometry_lonlat", [
                (G.nodes[u]["lon"], G.nodes[u]["lat"]),
                (G.nodes[v]["lon"], G.nodes[v]["lat"]),
            ])
            for lon, lat in coords:
                lats.append(lat); lons.append(lon)
            lats.append(None); lons.append(None)
        if not lats:
            continue
        width = 4 if bucket_name == "red" else (3 if bucket_name == "yellow" else 2)
        fig.add_trace(go.Scattermapbox(
            lat=lats, lon=lons, mode="lines",
            line=dict(color=color, width=width),
            hoverinfo="none",
            name=f"util {bucket_name}",
        ))

    # Edge midpoint hover markers (carry flow/cap/util text).
    mid_lat, mid_lon, mid_text, mid_color = [], [], [], []
    for (u, v) in G.edges():
        coords = G.edges[u, v].get("geometry_lonlat")
        if coords and len(coords) > 1:
            mid = coords[len(coords) // 2]
            lon, lat = mid[0], mid[1]
        else:
            lat = (G.nodes[u]["lat"] + G.nodes[v]["lat"]) / 2
            lon = (G.nodes[u]["lon"] + G.nodes[v]["lon"]) / 2
        mid_lat.append(lat); mid_lon.append(lon)
        ed = G.edges[u, v]
        mid_text.append(
            f"<b>{ed.get('road_name', '[road]')}</b> ({ed.get('direction', '')})"
            f"<br>flow: {edge_flow[(u, v)]:,.0f} / cap {ed['capacity_vph']:,.0f}"
            f"<br>util: {util[(u, v)]:.2f}"
            f"<br>travel: {tt[(u, v)]:.2f} min"
            f" (+{tt[(u, v)] - ed['free_flow_time_min']:.2f} delay)"
            f"<br>length: {ed['length_km']:.2f} km"
        )
        mid_color.append(UTIL_COLORS[_bucket(util[(u, v)])])

    fig.add_trace(go.Scattermapbox(
        lat=mid_lat, lon=mid_lon, mode="markers",
        marker=dict(size=6, color=mid_color, opacity=0.0),
        hovertext=mid_text, hoverinfo="text", name="segments",
        showlegend=False,
    ))

    # Nodes.
    node_lat, node_lon, node_text, node_size, node_color, node_label = \
        [], [], [], [], [], []
    for n, d in G.nodes(data=True):
        node_lat.append(d["lat"]); node_lon.append(d["lon"])
        is_attr = d["zone_type"] == "attractor"
        label = d["district"] if is_attr else ""
        node_label.append(label)
        node_text.append(
            f"<b>node {n}</b>"
            f"<br>district: {d['district']}"
            f"<br>type: {d['zone_type']}"
            f"<br>nearest landmark: {d.get('nearest_landmark', '—')}"
            f" ({d.get('landmark_dist_m', 0):.0f} m)"
        )
        node_size.append(14 if is_attr else 5)
        node_color.append("#f39c12" if is_attr else "#34495e")

    fig.add_trace(go.Scattermapbox(
        lat=node_lat, lon=node_lon, mode="markers",
        marker=dict(size=node_size, color=node_color),
        hovertext=node_text, hoverinfo="text",
        name="zones", showlegend=False,
    ))

    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            center=dict(lat=DOWNTOWN_REF[0], lon=DOWNTOWN_REF[1]),
            zoom=11,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=640,
        showlegend=False,
    )
    return fig


# ============================================================
# Sidebar
# ============================================================
st.sidebar.title("Controls")

tod_label = st.sidebar.selectbox("Time of day", ["AM peak", "Midday", "PM peak"], index=0)
tod_map = {"AM peak": "am_peak", "Midday": "midday", "PM peak": "pm_peak"}
tod = tod_map[tod_label]

bg = st.sidebar.selectbox("Background traffic", ["low", "medium", "high"], index=1)
demand = st.sidebar.selectbox("Customer demand", ["low", "medium", "high"], index=1)

rt_share = st.sidebar.slider("Robo-taxi share (% of commuters)", 0, 100, 30, step=5) / 100
deadhead = st.sidebar.slider("Deadheading ratio (% of RT trips)", 0, 50, 15, step=5) / 100
fleet = st.sidebar.slider("Fleet size cap (vehicles)", 200, 8000, 2000, step=100)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Edge color = flow / capacity. Green < 0.6, yellow 0.6–0.9, red > 0.9. "
    "Orange markers are attractor zones (downtown, USC, LA Live, Koreatown)."
)


# ============================================================
# Run + render
# ============================================================
result, baseline_vmt = _cached_run(tod, bg, demand, rt_share, deadhead, fleet, 42)
G = _cached_graph()

st.title("LA Robo-Taxi Urban Congestion Study")
st.caption(
    "Downtown LA + freeway ring. Traffic calibrated to Google Maps "
    "typical-traffic observations (where filled in); fallback 0.5 elsewhere."
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total VMT", f"{result['total_vmt']:,.0f}")
delta = result["total_vmt"] - baseline_vmt
c2.metric("Δ VMT vs. zero-RT baseline", f"{delta:+,.0f}",
          delta=f"{(delta / baseline_vmt * 100):+.1f}%" if baseline_vmt else None)
c3.metric("Avg trip time (min)", f"{result['avg_trip_time_min']:.1f}")
c4.metric("Congested edges (util > 0.9)", f"{result['congested_edge_count']}")
unmet_label = f"{result['unmet_demand']:,.0f}"
if result["surge_flag"]:
    unmet_label += "  ⚠ surge"
c5.metric("Unmet demand", unmet_label)

st.plotly_chart(build_map(G, result), use_container_width=True)

# Road inspection — the presentation hook.
with st.expander("Road inspection — top 5 most congested segments", expanded=True):
    rows = result["top_congested"]
    if not rows:
        st.write("No edges loaded.")
    else:
        for r in rows:
            bar_color = (
                "#e74c3c" if r["utilization"] > 0.9
                else "#f1c40f" if r["utilization"] > 0.6
                else "#2ecc71"
            )
            st.markdown(
                f"**{r['road_name']}** ({r['direction']}) — "
                f"<span style='color:{bar_color};font-weight:600'>"
                f"{r['utilization'] * 100:.0f}% utilization</span>, "
                f"+{r['delay_min']:.1f} min delay  "
                f"<span style='color:#7f8c8d'>"
                f"({r['flow_vph']:,.0f} / {r['capacity_vph']:,.0f} vph)"
                f"</span>",
                unsafe_allow_html=True,
            )

with st.expander("Breakdown"):
    b1, b2, b3 = st.columns(3)
    b1.metric("Commuter VMT", f"{result['commuter_vmt']:,.0f}")
    b2.metric("Deadhead VMT", f"{result['deadhead_vmt']:,.0f}")
    b3.metric("Trips served", f"{result['total_trips']:,.0f}")
    cov = calibration_coverage(G, tod)
    st.caption(
        f"Calibration coverage @ {tod}: "
        f"{cov['override']} override / {cov['road']} road / {cov['fallback']} fallback — "
        f"{cov['calibrated_fraction'] * 100:.0f}% named."
    )

with st.expander("How controls map to model parameters"):
    st.markdown("""
- **Time of day** → `SimParams.time_of_day` — selects OD matrix AND calibration ratios.
- **Background traffic** → `SimParams.background_level` — multiplies the
  calibrated `baseline_flow_vph` per edge (low ×0.5, med ×1.0, high ×1.5).
- **Customer demand** → `SimParams.demand_level` — scales the OD trip table.
- **Robo-taxi share** → `SimParams.robotaxi_share` — fraction of commuters on
  robo-taxi. Remainder splits 70/30 personal-car / transit (transit is off-road).
- **Deadheading ratio** → `SimParams.deadhead_ratio` — empty RT trips as a
  fraction of loaded RT trips, routed from dropoff zones to next-pickup zones
  weighted by demand-personalized PageRank.
- **Fleet cap** → `SimParams.fleet_cap` — when `cap × 4 trips/veh < RT demand`,
  all RT OD pairs are scaled down proportionally and the surge flag flips.
""")
