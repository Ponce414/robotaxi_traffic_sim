"""Standalone validation for the LA road network + model pipeline.

Run:
    python validate.py

(a) Prints node/edge counts + coverage.
(b) Saves a static matplotlib map of the graph to cache/la_network_preview.png.
(c) Runs run_scenario with default SimParams and prints the metrics dict.
"""
from __future__ import annotations

import json
import pprint
from pathlib import Path


def main() -> None:
    from la_network import load_la_network, coverage_stats

    print("=" * 60)
    print("(a) Loading LA network (may take a minute on first run)...")
    G = load_la_network()
    stats = coverage_stats(G)
    print(json.dumps(stats, indent=2, default=str))

    # Assertion-level sanity checks
    assert 40 <= stats["nodes"] <= 250, f"node count off target: {stats['nodes']}"
    assert stats["edges"] >= stats["nodes"], "edge count suspicious"
    assert stats["attractors"] >= 1, "no attractor nodes tagged"
    print("-> basic counts OK")

    print()
    print("=" * 60)
    print("(b) Rendering static preview...")
    _render_preview(G, Path(__file__).parent / "cache" / "la_network_preview.png")

    print()
    print("=" * 60)
    print("(c) Running default scenario...")
    try:
        from graph_model import SimParams, run_scenario, build_graph
    except ImportError:
        print("graph_model.py not yet ported — skipping scenario run.")
        return

    G2 = build_graph()
    params = SimParams()
    result = run_scenario(G2, params)

    scalars = {k: v for k, v in result.items() if isinstance(v, (int, float, bool))}
    pprint.pp(scalars, width=88)


def _render_preview(G, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 9), dpi=120)

    for u, v, data in G.edges(data=True):
        coords = data.get("geometry_lonlat") or [
            (G.nodes[u]["lon"], G.nodes[u]["lat"]),
            (G.nodes[v]["lon"], G.nodes[v]["lat"]),
        ]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        color = {
            "motorway": "#c0392b", "motorway_link": "#e74c3c",
            "trunk": "#d35400", "trunk_link": "#e67e22",
            "primary": "#7f8c8d", "primary_link": "#95a5a6",
        }.get(data.get("highway", ""), "#bdc3c7")
        width = 2.0 if data.get("highway", "").startswith("motorway") else 0.9
        ax.plot(xs, ys, color=color, linewidth=width, alpha=0.8, solid_capstyle="round")

    for n, d in G.nodes(data=True):
        is_attr = d["zone_type"] == "attractor"
        ax.scatter(d["lon"], d["lat"],
                   s=40 if is_attr else 8,
                   color="#f39c12" if is_attr else "#2c3e50",
                   zorder=5, edgecolors="white", linewidths=0.5)

    ax.set_aspect("equal")
    ax.set_title(f"LA network preview  ·  {G.number_of_nodes()} nodes / {G.number_of_edges()} edges")
    ax.set_xlabel("lon"); ax.set_ylabel("lat")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    print(f"saved -> {out_path}")


if __name__ == "__main__":
    main()
