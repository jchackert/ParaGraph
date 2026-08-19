import json
from pathlib import Path

import networkx as nx

from paragraph.build import build_from_json
from paragraph.cluster import cluster, score_all
from paragraph.insights import (
    community_summaries, classify_nodes, community_cycles, insights_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))


def test_community_summaries_cover_all_communities():
    G = make_graph()
    communities = cluster(G)
    summaries = community_summaries(G, communities, score_all(G, communities))
    assert len(summaries) == len(communities)
    assert summaries == sorted(summaries, key=lambda s: -s["size"])
    for s in summaries:
        assert 0.0 <= s["external_edge_ratio"] <= 1.0
        assert s["top_members"]


def test_classify_nodes_roles():
    G = make_graph()
    G.add_node("loner", label="loner")
    communities = cluster(G)
    roles = classify_nodes(G, communities)
    assert roles["hubs"], "fixture graph has connected nodes"
    assert all(h["degree"] > 0 for h in roles["hubs"])
    assert any(o["id"] == "loner" for o in roles["orphans"])


def test_bridges_require_multiple_communities():
    G = nx.Graph()
    for n in "abcdefg":
        G.add_node(n, label=n)
    # 'a' touches three other communities
    G.add_edge("a", "c"); G.add_edge("a", "e"); G.add_edge("a", "g")
    G.add_edge("b", "a")
    communities = {0: ["a", "b"], 1: ["c", "d"], 2: ["e", "f"], 3: ["g"]}
    roles = classify_nodes(G, communities)
    assert [b["id"] for b in roles["bridges"]] == ["a"]


def test_community_cycles_detects_mutual_dependency():
    G = nx.Graph()
    for n in ("a1", "a2", "b1", "b2"):
        G.add_node(n, label=n)
    # a1 -> b1 and b2 -> a2: communities 0 and 1 depend on each other
    G.add_edge("a1", "b1", _src="a1", _tgt="b1")
    G.add_edge("b2", "a2", _src="b2", _tgt="a2")
    communities = {0: ["a1", "a2"], 1: ["b1", "b2"]}
    cycles = community_cycles(G, communities)
    assert cycles and sorted(cycles[0]) == [0, 1]


def test_community_cycles_clean_hierarchy():
    G = nx.Graph()
    for n in ("a1", "b1"):
        G.add_node(n, label=n)
    G.add_edge("a1", "b1", _src="a1", _tgt="b1")  # one-way only
    communities = {0: ["a1"], 1: ["b1"]}
    assert community_cycles(G, communities) == []


def test_insights_markdown_renders():
    G = make_graph()
    communities = cluster(G)
    labels = {cid: f"Area {cid}" for cid in communities}
    md = insights_markdown(G, communities, score_all(G, communities), labels)
    assert md.startswith("# Graph Insights")
    assert "## Communities" in md
    assert "## Structural roles" in md
    assert "## Cross-community dependency cycles" in md
    assert "Area 0" in md
