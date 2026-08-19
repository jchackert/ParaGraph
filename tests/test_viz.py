"""Tests for the layered-architecture / lenses / blast-radius viz features
added to the generated vis.js HTML (paragraph/export.py)."""
import json
import re
import tempfile
from pathlib import Path
import networkx as nx
from paragraph.export import to_html


def make_layered_graph():
    """Small fabricated Swift-ish graph spanning several architectural layers."""
    G = nx.Graph()
    G.add_node("view:ContentView", label="ContentView", file_type="code",
               source_file="App/Views/ContentView.swift")
    G.add_node("vm:ContentViewModel", label="ContentViewModel", file_type="code",
               source_file="App/ViewModels/ContentViewModel.swift")
    G.add_node("model:User", label="User", file_type="code",
               source_file="App/Models/User.swift")
    G.add_node("tool:build", label="build.py", file_type="code",
               source_file="scripts/build.py")
    # Legal downward dependency: view -> viewmodel
    G.add_edge("view:ContentView", "vm:ContentViewModel", relation="calls",
               confidence="EXTRACTED",
               _src="view:ContentView", _tgt="vm:ContentViewModel")
    # Layering violation: model -> view points UP the stack
    G.add_edge("model:User", "view:ContentView", relation="imports",
               confidence="INFERRED",
               _src="model:User", _tgt="view:ContentView")
    communities = {0: list(G.nodes())}
    return G, communities


def render(G, communities):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "graph.html"
        to_html(G, communities, str(out))
        return out.read_text()


def extract_json(content, name):
    """Pull an embedded `const NAME = [...];` JSON array out of the HTML."""
    m = re.search(rf"^const {name} = (.*);$", content, re.MULTILINE)
    assert m, f"{name} not found in generated HTML"
    return json.loads(m.group(1))


def test_html_contains_all_three_controls():
    G, communities = make_layered_graph()
    content = render(G, communities)
    assert 'id="layers-toggle"' in content          # Layers toggle
    assert 'id="lenses-section"' in content and ">Lenses<" in content
    assert 'id="blast-btn"' in content and "Blast radius" in content


def test_nodes_carry_layer_data():
    G, communities = make_layered_graph()
    nodes = extract_json(render(G, communities), "RAW_NODES")
    by_label = {n["label"]: n["layer"] for n in nodes}
    assert by_label["ContentView"] == "view"          # Views/*.swift -> view
    assert by_label["ContentViewModel"] == "viewmodel"
    assert by_label["User"] == "model"
    assert by_label["build.py"] == "tooling"          # .py source -> tooling


def test_edges_carry_direction_and_violation_flags():
    G, communities = make_layered_graph()
    edges = extract_json(render(G, communities), "RAW_EDGES")
    by_src = {e["src"]: e for e in edges}
    # model -> view points up the stack: violation
    violating = by_src["model:User"]
    assert violating["tgt"] == "view:ContentView"
    assert violating["violation"] is True
    assert violating["relation"] == "imports"
    assert violating["confidence"] == "INFERRED"
    # view -> viewmodel is a legal downward dependency
    legal = by_src["view:ContentView"]
    assert legal["tgt"] == "vm:ContentViewModel"
    assert legal["violation"] is False
    assert legal["relation"] == "calls"
    assert legal["confidence"] == "EXTRACTED"


def test_lens_checkboxes_exist_for_all_three_groups():
    G, communities = make_layered_graph()
    content = render(G, communities)
    for kind, value in [("relation", "calls"), ("relation", "imports"),
                        ("relation", "contains/method"),
                        ("confidence", "EXTRACTED"), ("confidence", "INFERRED"),
                        ("confidence", "AMBIGUOUS"),
                        ("file_type", "code"), ("file_type", "document"),
                        ("file_type", "observation"), ("file_type", "rationale")]:
        assert f'data-kind="{kind}" data-value="{value}"' in content
    # checked by default
    assert 'class="lens-cb" checked' in content


def test_layers_toggle_suppressed_for_single_layer_graph():
    """Aggregated overview pages (nodes without source_file all classify as
    'other') must not render the Layers toggle."""
    G = nx.Graph()
    G.add_node("0", label="Community 0")
    G.add_node("1", label="Community 1")
    G.add_edge("0", "1", relation="3 cross-community edges", confidence="AGGREGATED")
    content = render(G, {0: ["0"], 1: ["1"]})
    assert 'id="layers-toggle"' not in content
    # Lenses and blast radius are still available
    assert 'id="blast-btn"' in content
    assert 'id="lenses-section"' in content


def test_edge_direction_falls_back_to_uv_without_src_tgt():
    G = nx.Graph()
    G.add_node("a", label="AView", file_type="code", source_file="Views/AView.swift")
    G.add_node("b", label="BModel", file_type="code", source_file="Models/BModel.swift")
    G.add_edge("a", "b", relation="uses", confidence="EXTRACTED")  # no _src/_tgt
    edges = extract_json(render(G, {0: ["a", "b"]}), "RAW_EDGES")
    assert len(edges) == 1
    e = edges[0]
    assert {e["src"], e["tgt"]} == {"a", "b"}
    assert e["src"] == e["from"] and e["tgt"] == e["to"]


def test_blast_radius_script_uses_reverse_bfs_over_direction():
    G, communities = make_layered_graph()
    content = render(G, communities)
    # the reverse-adjacency BFS and its reset paths are wired in
    assert "runBlast" in content
    assert "resetBlast" in content
    assert "Escape" in content


def test_existing_features_still_present():
    G, communities = make_layered_graph()
    content = render(G, communities)
    assert "RAW_NODES" in content and "RAW_EDGES" in content
    assert 'id="search"' in content
    assert 'id="legend"' in content
    assert "hyperedges" in content
    assert "doubleClick" in content  # community drill-down handler
