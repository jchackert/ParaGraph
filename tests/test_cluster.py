import json
import sys
import networkx as nx
from pathlib import Path
from paragraph.build import build_from_json
from paragraph.cluster import cluster, cohesion_score, score_all, community_label, label_communities

FIXTURES = Path(__file__).parent / "fixtures"

def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))

def test_cluster_returns_dict():
    G = make_graph()
    communities = cluster(G)
    assert isinstance(communities, dict)

def test_cluster_covers_all_nodes():
    G = make_graph()
    communities = cluster(G)
    all_nodes = {n for nodes in communities.values() for n in nodes}
    assert all_nodes == set(G.nodes)

def test_cohesion_score_complete_graph():
    G = nx.complete_graph(4)
    G = nx.relabel_nodes(G, {i: str(i) for i in G.nodes})
    score = cohesion_score(G, list(G.nodes))
    assert score == 1.0

def test_cohesion_score_single_node():
    G = nx.Graph()
    G.add_node("a")
    score = cohesion_score(G, ["a"])
    assert score == 1.0

def test_cohesion_score_disconnected():
    G = nx.Graph()
    G.add_nodes_from(["a", "b", "c"])
    score = cohesion_score(G, ["a", "b", "c"])
    assert score == 0.0

def test_cohesion_score_range():
    G = make_graph()
    communities = cluster(G)
    for cid, nodes in communities.items():
        score = cohesion_score(G, nodes)
        assert 0.0 <= score <= 1.0

def test_score_all_keys_match_communities():
    G = make_graph()
    communities = cluster(G)
    scores = score_all(G, communities)
    assert set(scores.keys()) == set(communities.keys())


def test_cluster_does_not_write_to_stdout(capsys):
    """Clustering should not emit ANSI escape codes or other output.

    graspologic's leiden() can emit ANSI escape sequences that break
    PowerShell 5.1's scroll buffer on Windows (issue #19). The output
    suppression in _partition() should prevent any output from leaking.
    """
    G = make_graph()
    cluster(G)
    captured = capsys.readouterr()
    assert captured.out == "", f"cluster() wrote to stdout: {captured.out!r}"


def test_cluster_does_not_write_to_stderr(capsys):
    """Same as above but for stderr — ANSI codes can go to either stream."""
    G = make_graph()
    cluster(G)
    captured = capsys.readouterr()
    # Allow logging output (starts with [paragraph]) but no raw ANSI codes
    for line in captured.err.splitlines():
        assert "\x1b" not in line, f"cluster() wrote ANSI to stderr: {line!r}"


def test_label_communities_covers_all_communities():
    G = make_graph()
    communities = cluster(G)
    labels = label_communities(G, communities)
    assert set(labels.keys()) == set(communities.keys())
    assert all(isinstance(l, str) and l for l in labels.values())


def test_label_communities_deterministic():
    """Same graph, same communities -> byte-identical labels every time."""
    G = make_graph()
    communities = cluster(G)
    assert label_communities(G, communities) == label_communities(G, communities)


def test_community_label_ranks_by_within_community_degree():
    G = nx.Graph()
    G.add_node("a", label="Auth")
    G.add_node("b", label="Session")
    G.add_node("c", label="Token")
    G.add_node("d", label="Cookie")
    G.add_edges_from([("a", "b"), ("a", "c"), ("a", "d"), ("b", "c")])
    label = community_label(G, ["a", "b", "c", "d"])
    # a has degree 3, b and c have degree 2, d has degree 1
    assert label.startswith("Auth · ")
    assert label == "Auth · Session · Token"


def test_community_label_tie_break_by_node_id():
    """Equal degrees fall back to node id ordering, keeping labels stable."""
    G = nx.Graph()
    G.add_node("n2", label="Beta")
    G.add_node("n1", label="Alpha")
    G.add_edge("n1", "n2")
    assert community_label(G, ["n2", "n1"]) == "Alpha · Beta"


def test_community_label_truncates_long_labels():
    G = nx.Graph()
    G.add_node("a", label="A" * 80)
    G.add_node("b", label="B" * 80)
    G.add_edge("a", "b")
    label = community_label(G, ["a", "b"])
    assert len(label) <= 50
    assert label.endswith("…")


def test_cluster_stores_labels_on_graph_metadata():
    """cluster() persists deterministic labels on G.graph, same convention as hyperedges."""
    G = make_graph()
    communities = cluster(G)
    stored = G.graph.get("community_labels")
    assert stored == label_communities(G, communities)
