# architectural analysis of the graph: community summaries, node roles,
# cross-community cycles. Consumed by `paragraph analyze` and GRAPH_INSIGHTS.md.
from __future__ import annotations

from collections import Counter
from pathlib import Path

import networkx as nx

from paragraph.analyze import _node_community_map


def _top_level_dir(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if len(parts) > 1 else "."


def community_summaries(
    G: nx.Graph,
    communities: dict[int, list[str]],
    cohesion: dict[int, float] | None = None,
    community_labels: dict[int, str] | None = None,
) -> list[dict]:
    """One summary dict per community: what it is, where it lives, how it connects.

    Fields: cid, label, size, cohesion, dominant_dirs, file_types,
    top_members (by within-community degree), external_edge_ratio
    (share of member edges that leave the community — high means entangled,
    low means well-isolated).
    """
    node_community = _node_community_map(communities)
    cohesion = cohesion or {}
    labels = community_labels or {}
    summaries = []
    for cid, members in sorted(communities.items(), key=lambda kv: -len(kv[1])):
        member_set = set(members)
        dirs = Counter()
        ftypes = Counter()
        internal = external = 0
        for n in members:
            if n not in G:
                continue
            data = G.nodes[n]
            src = data.get("source_file")
            if src:
                dirs[_top_level_dir(str(src))] += 1
            ftypes[data.get("file_type") or "unknown"] += 1
            for nb in G.neighbors(n):
                if nb in member_set:
                    internal += 1
                else:
                    external += 1
        internal //= 2  # each intra edge counted from both endpoints
        total = internal + external
        sub = G.subgraph(members)
        ranked = sorted(members, key=lambda n: (-sub.degree(n), str(n)))
        summaries.append({
            "cid": cid,
            "label": labels.get(cid, f"Community {cid}"),
            "size": len(members),
            "cohesion": cohesion.get(cid),
            "dominant_dirs": [d for d, _ in dirs.most_common(3)],
            "file_types": dict(ftypes.most_common()),
            "top_members": [
                str(G.nodes[n].get("label", n)) for n in ranked[:5] if n in G
            ],
            "external_edge_ratio": round(external / total, 2) if total else 0.0,
        })
    return summaries


def classify_nodes(
    G: nx.Graph,
    communities: dict[int, list[str]],
    *,
    bridge_min_communities: int = 3,
    hub_top_n: int = 10,
) -> dict[str, list[dict]]:
    """Classify nodes into architectural roles.

    hubs     - highest-degree nodes (the god-node list, degree-ranked)
    bridges  - nodes with edges into >= bridge_min_communities other communities;
               these hold the architecture together and are risky to change
    orphans  - zero-degree nodes; extraction found them but nothing references them
    """
    node_community = _node_community_map(communities)
    degree = dict(G.degree())

    hubs = [
        {"id": n, "label": str(G.nodes[n].get("label", n)), "degree": d}
        for n, d in sorted(degree.items(), key=lambda kv: (-kv[1], str(kv[0])))[:hub_top_n]
        if d > 0
    ]

    bridges = []
    for n in G.nodes():
        own = node_community.get(n)
        others = {
            node_community.get(nb)
            for nb in G.neighbors(n)
            if node_community.get(nb) not in (None, own)
        }
        if len(others) >= bridge_min_communities:
            bridges.append({
                "id": n,
                "label": str(G.nodes[n].get("label", n)),
                "communities_touched": len(others) + (1 if own is not None else 0),
            })
    bridges.sort(key=lambda b: (-b["communities_touched"], b["id"]))

    orphans = [
        {"id": n, "label": str(G.nodes[n].get("label", n)),
         "source_file": G.nodes[n].get("source_file")}
        for n in sorted(G.nodes(), key=str) if degree.get(n, 0) == 0
    ]
    return {"hubs": hubs, "bridges": bridges, "orphans": orphans}


def community_cycles(
    G: nx.Graph,
    communities: dict[int, list[str]],
    *,
    max_cycles: int = 20,
) -> list[list[int]]:
    """Dependency cycles between communities.

    Uses the preserved edge direction (_src/_tgt attrs) to build a directed
    community-level graph; a cycle here means two or more communities each
    depend on the other — usually a layering violation worth investigating.
    Cycles of length 2 (mutual dependency) are included. Returns at most
    max_cycles cycles, shortest first, as lists of community ids.
    """
    node_community = _node_community_map(communities)
    meta = nx.DiGraph()
    meta.add_nodes_from(communities)
    for u, v, data in G.edges(data=True):
        src = data.get("_src", u)
        tgt = data.get("_tgt", v)
        cu, cv = node_community.get(src), node_community.get(tgt)
        if cu is None or cv is None or cu == cv:
            continue
        meta.add_edge(cu, cv)
    cycles = []
    try:
        for cycle in nx.simple_cycles(meta):
            cycles.append(cycle)
            if len(cycles) >= max_cycles * 5:
                break  # bail early on pathological meta-graphs
    except nx.NetworkXNoCycle:
        pass
    cycles.sort(key=lambda c: (len(c), c))
    return cycles[:max_cycles]


def insights_markdown(
    G: nx.Graph,
    communities: dict[int, list[str]],
    cohesion: dict[int, float] | None = None,
    community_labels: dict[int, str] | None = None,
    *,
    max_communities: int = 25,
) -> str:
    """Render the full analysis as a GRAPH_INSIGHTS.md document."""
    labels = community_labels or {}
    summaries = community_summaries(G, communities, cohesion, labels)
    roles = classify_nodes(G, communities)
    cycles = community_cycles(G, communities)

    lines = ["# Graph Insights", ""]
    lines.append(f"{G.number_of_nodes()} nodes · {G.number_of_edges()} edges · "
                 f"{len(communities)} communities")
    lines.append("")

    lines.append("## Communities")
    lines.append("")
    lines.append("| Community | Size | Cohesion | Isolation | Lives in | Top members |")
    lines.append("|---|---|---|---|---|---|")
    for s in summaries[:max_communities]:
        coh = f"{s['cohesion']:.2f}" if s["cohesion"] is not None else "-"
        isolation = f"{(1 - s['external_edge_ratio']):.0%} internal"
        dirs = ", ".join(s["dominant_dirs"]) or "-"
        tops = ", ".join(s["top_members"][:3])
        lines.append(f"| {s['label']} | {s['size']} | {coh} | {isolation} | {dirs} | {tops} |")
    if len(summaries) > max_communities:
        lines.append("")
        lines.append(f"({len(summaries) - max_communities} smaller communities omitted)")
    lines.append("")

    lines.append("## Structural roles")
    lines.append("")
    if roles["hubs"]:
        lines.append("**Hubs** (highest degree — the graph routes through these):")
        for h in roles["hubs"][:5]:
            lines.append(f"- {h['label']} ({h['degree']} connections)")
        lines.append("")
    if roles["bridges"]:
        lines.append("**Bridges** (touch 3+ communities — changes here ripple widely):")
        for b in roles["bridges"][:5]:
            lines.append(f"- {b['label']} (spans {b['communities_touched']} communities)")
        lines.append("")
    if roles["orphans"]:
        lines.append(f"**Orphans**: {len(roles['orphans'])} node(s) with no connections — "
                     "possibly dead code or extraction gaps.")
        for o in roles["orphans"][:5]:
            lines.append(f"- {o['label']} ({o['source_file'] or 'unknown source'})")
        lines.append("")

    lines.append("## Cross-community dependency cycles")
    lines.append("")
    if not cycles:
        lines.append("None found — community dependencies form a clean hierarchy.")
    else:
        lines.append(f"{len(cycles)} cycle(s) found. Each means the communities depend "
                     "on each other — usually a layering violation:")
        for cycle in cycles[:10]:
            names = " → ".join(labels.get(c, f"Community {c}") for c in cycle)
            first = labels.get(cycle[0], f"Community {cycle[0]}")
            lines.append(f"- {names} → {first}")
    lines.append("")
    return "\n".join(lines)
