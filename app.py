"""Streamlit UI for the robo-taxi congestion model.

Run with:
    streamlit run app.py

All model logic lives in graph_model.py. This file only handles UI + caching.
"""
from __future__ import annotations

import networkx as nx
import plotly.graph_objects as go
import streamlit as st

from graph_model import (
    ATTRACTORS,
    SimParams,
    build_graph,
    get_baseline_vmt,
    simulate,
)

st.set_page_config(page_title="Robo-Taxi Congestion Study", layout="wide")


# ============================================================
# Cached computation
# ============================================================
@st.cache_resource
def _cached_graph(seed: int) -> nx.DiGraph:
    return build_graph(seed=seed)


@st.cache_data(show_spinner=False)
def _cached_run(time_of_day: str, background_level: str, demand_level: str,
                robotaxi_share: float, deadhead_ratio: float, fleet_cap: int,
                seed: int) -> tuple[dict, float]:
    G = _cached_graph(seed)
    params = SimParams(
        time_of_day=time_of_day,
        background_level=background_level,
        demand_level=demand_level,
        robotaxi_share=robotaxi_share,
        deadhead_ratio=deadhead_ratio,
        fleet_cap=fleet_cap,
        seed=seed,
    )
    result = simulate(G, params)
    baseline_vmt = get_baseline_vmt(G, params)
    return result, baseline_vmt


# ============================================================
# Plotly figure
# ============================================================
ZONE_COLORS = {
    "residential": "#3498db",
    "commercial":  "#9b59b6",
    "attractor":   "#e67e22",
}
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


def build_figure(G: nx.DiGraph, result: dict) -> go.Figure:
    pos = nx.get_node_attributes(G, "pos")
    util = result["utilization"]
    edge_flow = result["edge_flow"]

    fig = go.Figure()

    # Edges grouped by utilization bucket so each bucket can have its own color.
    for name, color in UTIL_COLORS.items():
        xs: list[float | None] = []
        ys: list[float | None] = []
        for (u, v), ut in util.items():
            if _bucket(ut) != name:
                continue
            x0, y0 = pos[u]; x1, y1 = pos[v]
            xs += [x0, x1, None]
            ys += [y0, y1, None]
        if not xs:
            continue
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(color=color, width=2.5),
            hoverinfo="none",
            name=f"util {name}",
            showlegend=False,
        ))

    # Per-edge midpoint markers carrying hover text (flow / cap / util).
    mid_x, mid_y, mid_text, mid_color = [], [], [], []
    for (u, v) in G.edges():
        x0, y0 = pos[u]; x1, y1 = pos[v]
        mid_x.append((x0 + x1) / 2 + 0.05 * (y1 - y0))
        mid_y.append((y0 + y1) / 2 - 0.05 * (x1 - x0))
        mid_text.append(
            f"{u} → {v}<br>"
            f"flow: {edge_flow[(u, v)]:.0f} / cap {G.edges[(u, v)]['capacity']}"
            f"<br>util: {util[(u, v)]:.2f}"
            f"<br>distance: {G.edges[(u, v)]['distance_km']:.2f} km"
        )
        mid_color.append(UTIL_COLORS[_bucket(util[(u, v)])])
    fig.add_trace(go.Scatter(
        x=mid_x, y=mid_y, mode="markers",
        marker=dict(size=6, color=mid_color, opacity=0.6),
        hovertext=mid_text, hoverinfo="text",
        showlegend=False,
    ))

    # Nodes: size by outbound loaded flow, color by zone type.
    out_flow: dict[int, float] = {
        n: sum(result["pc_flow"][(n, v)] + result["rt_flow"][(n, v)]
               for v in G.successors(n))
        for n in G.nodes()
    }
    max_out = max(out_flow.values()) or 1.0

    node_x, node_y, node_text, node_size, node_color, node_label = \
        [], [], [], [], [], []
    for n in G.nodes():
        x, y = pos[n]
        node_x.append(x); node_y.append(y)
        zt = G.nodes[n]["zone_type"]
        label = ATTRACTORS.get(n, str(n))
        node_label.append(label)
        node_text.append(
            f"<b>{label}</b> (zone {n})<br>"
            f"type: {zt}<br>"
            f"population: {G.nodes[n]['population']:,}<br>"
            f"parking: {G.nodes[n]['parking_capacity']}<br>"
            f"pickup infra: {G.nodes[n]['has_pickup_dropoff_infra']}<br>"
            f"outbound veh flow: {out_flow[n]:.0f}"
        )
        node_size.append(16 + 36 * (out_flow[n] / max_out))
        node_color.append(ZONE_COLORS[zt])

    fig.add_trace(go.Scatter(
        x=node_x, y=node_y, mode="markers+text",
        text=node_label, textposition="top center",
        textfont=dict(color="white", size=11),
        marker=dict(size=node_size, color=node_color,
                    line=dict(color="white", width=1.2)),
        hovertext=node_text, hoverinfo="text",
        showlegend=False,
    ))

    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x"),
        margin=dict(l=10, r=10, t=10, b=10),
        height=620,
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
    )
    return fig


# ============================================================
# Sidebar controls
# ============================================================
st.sidebar.title("Controls")

tod_label = st.sidebar.selectbox(
    "Time of day", ["AM peak", "Midday", "PM peak"], index=0,
)
tod_map = {"AM peak": "am_peak", "Midday": "midday", "PM peak": "pm_peak"}
tod = tod_map[tod_label]

bg = st.sidebar.selectbox("Background traffic", ["low", "medium", "high"], index=1)
demand = st.sidebar.selectbox("Customer demand", ["low", "medium", "high"], index=1)

rt_share = st.sidebar.slider("Robo-taxi share (% of commuters)", 0, 100, 30, step=5) / 100
deadhead = st.sidebar.slider("Deadheading ratio (% of RT trips)", 0, 50, 15, step=5) / 100
fleet = st.sidebar.slider("Fleet size cap (vehicles)", 200, 5000, 2000, step=100)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Edges colored by flow/capacity: green < 0.6, yellow 0.6–0.9, red > 0.9. "
    "Node size = outbound loaded flow."
)

# ============================================================
# Run + render
# ============================================================
result, baseline_vmt = _cached_run(tod, bg, demand, rt_share, deadhead, fleet, 42)
G = _cached_graph(42)

st.title("Robo-Taxi Urban Congestion Study")
st.caption("Interactive simulation — shared foundation for centrality, "
           "game-theory, and adoption-dynamics analyses.")

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

st.plotly_chart(build_figure(G, result), use_container_width=True)

with st.expander("Breakdown"):
    b1, b2, b3 = st.columns(3)
    b1.metric("Commuter VMT", f"{result['commuter_vmt']:,.0f}")
    b2.metric("Deadhead VMT", f"{result['deadhead_vmt']:,.0f}")
    b3.metric("Total commuter trips served",
              f"{result['total_trips']:,.0f}")

with st.expander("How controls map to model parameters"):
    st.markdown("""
- **Time of day** → `SimParams.time_of_day` — picks AM peak (residential→attractor),
  midday (diffuse commercial↔attractor), or PM peak (attractor→residential).
- **Background traffic** → `SimParams.background_level` — multiplies each edge's
  `baseline_flow` (low ×0.5, medium ×1.0, high ×1.5).
- **Customer demand** → `SimParams.demand_level` — scales the OD trip table
  (low ×0.5, medium ×1.0, high ×1.6).
- **Robo-taxi share** → `SimParams.robotaxi_share` — fraction of commuter trips
  routed as robo-taxi. Remainder splits 70/30 personal-car / transit (transit is
  off-road and contributes no edge flow).
- **Deadheading ratio** → `SimParams.deadhead_ratio` — empty repositioning trips as
  a fraction of loaded RT trips. Empty trips originate at dropoff zones and head to
  next-pickup zones, weighted by demand-personalized PageRank.
- **Fleet cap** → `SimParams.fleet_cap` — if `cap × trips_per_vehicle_per_period`
  is less than RT demand, all RT OD pairs are scaled down proportionally and the
  surge flag flips.
""")
