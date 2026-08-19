import json
import tempfile
from pathlib import Path
from paragraph.build import build_from_json
from paragraph.cluster import cluster
from paragraph.export import to_json, to_cypher, to_graphml, to_html

FIXTURES = Path(__file__).parent / "fixtures"

def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))

def test_to_json_creates_file():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.json"
        to_json(G, communities, str(out))
        assert out.exists()

def test_to_json_valid_json():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.json"
        to_json(G, communities, str(out))
        data = json.loads(out.read_text())
        assert "nodes" in data
        assert "links" in data

def test_to_json_nodes_have_community():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.json"
        to_json(G, communities, str(out))
        data = json.loads(out.read_text())
        for node in data["nodes"]:
            assert "community" in node

def test_to_cypher_creates_file():
    G = make_graph()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cypher.txt"
        to_cypher(G, str(out))
        assert out.exists()

def test_to_cypher_contains_merge_statements():
    G = make_graph()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cypher.txt"
        to_cypher(G, str(out))
        content = out.read_text()
        assert "MERGE" in content

def test_to_graphml_creates_file():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.graphml"
        to_graphml(G, communities, str(out))
        assert out.exists()

def test_to_graphml_valid_xml():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.graphml"
        to_graphml(G, communities, str(out))
        content = out.read_text()
        assert "<graphml" in content
        assert "<node" in content

def test_to_graphml_has_community_attribute():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.graphml"
        to_graphml(G, communities, str(out))
        content = out.read_text()
        assert "community" in content

def test_to_graphml_json_stringifies_non_scalar_attrs():
    """GraphML only supports scalar attr values; non-scalars (dicts/lists like
    hyperedges or community_labels) are JSON-stringified — chosen over dropping
    them because the data round-trips: read the graph back, json.loads() the
    attribute, and the original structure is recovered. None values are dropped
    (GraphML has no null)."""
    import networkx as nx
    G = make_graph()
    communities = cluster(G)  # cluster() itself writes dict attrs into G.graph
    G.graph["community_labels"] = {"0": "core", "1": "docs"}
    G.graph["hyperedges"] = [["a", "b", "c"]]
    G.graph["dropped_none"] = None
    first_node = next(iter(G.nodes()))
    G.nodes[first_node]["aliases"] = ["alt-name"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.graphml"
        to_graphml(G, communities, str(out))
        H = nx.read_graphml(str(out))
        # Round-trip: JSON-stringified attrs decode back to the original value
        assert json.loads(H.graph["community_labels"]) == {"0": "core", "1": "docs"}
        assert json.loads(H.graph["hyperedges"]) == [["a", "b", "c"]]
        assert "dropped_none" not in H.graph
        assert json.loads(H.nodes[first_node]["aliases"]) == ["alt-name"]

def test_to_html_creates_file():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out))
        assert out.exists()

def test_to_html_contains_visjs():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out))
        content = out.read_text()
        assert "vis-network" in content

def test_to_html_contains_search():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out))
        content = out.read_text()
        assert "search" in content.lower()

def test_to_html_contains_legend_with_labels():
    G = make_graph()
    communities = cluster(G)
    labels = {cid: f"Group {cid}" for cid in communities}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out), community_labels=labels)
        content = out.read_text()
        assert "Group 0" in content

def test_to_html_contains_nodes_and_edges():
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out))
        content = out.read_text()
        assert "RAW_NODES" in content
        assert "RAW_EDGES" in content


def test_to_html_member_counts_accepted():
    """to_html accepts member_counts without raising."""
    G = make_graph()
    communities = cluster(G)
    member_counts = {cid: len(members) for cid, members in communities.items()}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out), member_counts=member_counts)
        assert out.exists()

def test_to_html_auto_full_under_limit():
    from paragraph.export import to_html_auto
    G = make_graph()
    communities = cluster(G)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        assert to_html_auto(G, communities, str(out)) == "full"
        assert out.exists()


def test_to_html_auto_aggregates_over_limit(monkeypatch):
    import paragraph.export as export_mod
    G = make_graph()
    communities = cluster(G)
    assert 1 < len(communities) < G.number_of_nodes(), "fixture must have multiple communities"
    monkeypatch.setattr(export_mod, "MAX_NODES_FOR_VIZ", len(communities))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        assert export_mod.to_html_auto(G, communities, str(out)) == "aggregated"
        content = out.read_text()
        assert "vis-network" in content
        assert "AGGREGATED" in content


def test_to_html_auto_skips_single_oversized_community(monkeypatch):
    import paragraph.export as export_mod
    G = make_graph()
    communities = {0: list(G.nodes())}
    monkeypatch.setattr(export_mod, "MAX_NODES_FOR_VIZ", 1)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        out.write_text("stale")
        assert export_mod.to_html_auto(G, communities, str(out)) == "skipped"
        assert not out.exists(), "stale graph.html must be removed on skip"


def test_to_json_persists_community_labels():
    G = make_graph()
    communities = cluster(G)
    labels = {cid: f"Named {cid}" for cid in communities}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.json"
        to_json(G, communities, str(out), community_labels=labels)
        data = json.loads(out.read_text())
        assert data["graph"]["community_labels"] == {str(k): v for k, v in labels.items()}


def test_to_html_auto_aggregated_writes_drilldown_pages(monkeypatch):
    import paragraph.export as export_mod
    G = make_graph()
    communities = cluster(G)
    assert 1 < len(communities) < G.number_of_nodes()
    monkeypatch.setattr(export_mod, "MAX_NODES_FOR_VIZ", len(communities))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        assert export_mod.to_html_auto(G, communities, str(out),
                                       community_labels={cid: f"Area {cid}" for cid in communities}) == "aggregated"
        overview = out.read_text()
        pages = sorted((Path(tmp) / "graph_communities").glob("community_*.html"))
        # every multi-member community gets a page, linked from the overview
        multi = [cid for cid, m in communities.items() if len(m) > 1]
        assert len(pages) == len(multi)
        for cid in multi:
            assert f"graph_communities/community_{cid}.html" in overview
        page = pages[0].read_text()
        assert "Overview</a>" in page and "vis-network" in page


def test_to_html_auto_collapses_unconnected_singletons(monkeypatch):
    import networkx as nx
    import paragraph.export as export_mod
    G = nx.Graph()
    # two connected communities
    for n in ("a1", "a2", "b1", "b2"):
        G.add_node(n, label=n)
    G.add_edge("a1", "a2"); G.add_edge("b1", "b2"); G.add_edge("a1", "b1")
    communities = {0: ["a1", "a2"], 1: ["b1", "b2"]}
    # 30 orphan singleton communities
    for i in range(30):
        nid = f"orphan{i}"
        G.add_node(nid, label=f"Ticket {i} marked complete")
        communities[100 + i] = [nid]
    monkeypatch.setattr(export_mod, "MAX_NODES_FOR_VIZ", 10)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        assert export_mod.to_html_auto(G, communities, str(out)) == "aggregated"
        content = out.read_text()
        assert "Unconnected content (30 nodes)" in content
        # orphan community labels do not spam the overview
        assert "Ticket 5 marked complete" not in content
        # connected communities still get drill-down pages
        pages = list((Path(tmp) / "graph_communities").glob("community_*.html"))
        assert len(pages) == 2
