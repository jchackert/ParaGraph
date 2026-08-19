import json
from pathlib import Path
from paragraph.build import build_from_json

FIXTURES = Path(__file__).parent / "fixtures"

def load_extraction():
    return json.loads((FIXTURES / "extraction.json").read_text())

def test_build_from_json_node_count():
    G = build_from_json(load_extraction())
    assert G.number_of_nodes() == 4

def test_build_from_json_edge_count():
    G = build_from_json(load_extraction())
    assert G.number_of_edges() == 4

def test_nodes_have_label():
    G = build_from_json(load_extraction())
    assert G.nodes["n_transformer"]["label"] == "Transformer"

def test_edges_have_confidence():
    G = build_from_json(load_extraction())
    data = G.edges["n_attention", "n_concept_attn"]
    assert data["confidence"] == "INFERRED"

def test_ambiguous_edge_preserved():
    G = build_from_json(load_extraction())
    data = G.edges["n_layernorm", "n_concept_attn"]
    assert data["confidence"] == "AMBIGUOUS"

def test_legacy_node_source_canonicalized():
    """Legacy 'source' key on nodes is renamed to 'source_file' before graph build."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source": "a.py"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert "source_file" in G.nodes["n1"]
    assert G.nodes["n1"]["source_file"] == "a.py"
    assert "source" not in G.nodes["n1"]


def test_legacy_edge_from_to_canonicalized():
    """Legacy 'from'/'to' keys on edges are accepted alongside 'source'/'target'."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source_file": "a.py"},
                     {"id": "n2", "label": "B", "file_type": "code", "source_file": "b.py"}],
           "edges": [{"from": "n1", "to": "n2", "relation": "calls",
                      "confidence": "EXTRACTED", "source_file": "a.py", "weight": 1.0}],
           "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert G.number_of_edges() == 1


def test_dedup_never_merges_bare_callables_across_files():
    from paragraph.build import deduplicate_by_label
    nodes = [
        {"id": "scripta_main", "label": "main()", "file_type": "code", "source_file": "scripts/a.py"},
        {"id": "scriptb_main", "label": "main()", "file_type": "code", "source_file": "scripts/b.py"},
    ]
    deduped, _ = deduplicate_by_label(nodes, [])
    assert len(deduped) == 2


def test_dedup_merges_chunk_duplicates_same_file():
    from paragraph.build import deduplicate_by_label
    nodes = [
        {"id": "auth_flow", "label": "Auth Flow", "file_type": "code", "source_file": "auth.py"},
        {"id": "auth_flow_c2", "label": "Auth Flow", "file_type": "code", "source_file": "auth.py"},
    ]
    edges = [{"source": "auth_flow_c2", "target": "auth_flow", "relation": "x"},
             {"source": "auth_flow_c2", "target": "other", "relation": "y"}]
    deduped, dedup_edges = deduplicate_by_label(nodes, edges)
    assert [n["id"] for n in deduped] == ["auth_flow"]
    # self-loop dropped, other edge remapped to survivor
    assert dedup_edges == [{"source": "auth_flow", "target": "other", "relation": "y"}]


def test_dedup_merges_concepts_across_files():
    from paragraph.build import deduplicate_by_label
    nodes = [
        {"id": "attention_mechanism", "label": "Attention Mechanism", "file_type": "rationale", "source_file": "paper1.md"},
        {"id": "attention_mechanism_c3", "label": "Attention Mechanism", "file_type": "rationale", "source_file": "paper2.md"},
    ]
    deduped, _ = deduplicate_by_label(nodes, [])
    assert len(deduped) == 1


def test_dedup_callable_label_without_file_type_is_still_scoped():
    from paragraph.build import deduplicate_by_label
    # LLM output sometimes lacks file_type at merge time — label shape guards it
    nodes = [
        {"id": "a_run", "label": "run()", "source_file": "a.py"},
        {"id": "b_run", "label": "run()", "source_file": "b.py"},
    ]
    deduped, _ = deduplicate_by_label(nodes, [])
    assert len(deduped) == 2


def test_connect_orphan_chunks_links_to_file_anchor():
    from paragraph.build import connect_orphan_chunks
    graph = {
        "nodes": [
            {"id": "doc_rulings_1", "label": "Ruling A", "file_type": "document", "source_file": "docs/rulings.md"},
            {"id": "doc_rulings_2", "label": "Ruling B", "file_type": "document", "source_file": "docs/rulings.md"},
            {"id": "code_a", "label": "A", "file_type": "code", "source_file": "a.py"},
            {"id": "code_b", "label": "B", "file_type": "code", "source_file": "a.py"},
        ],
        "links": [{"source": "code_a", "target": "code_b", "relation": "calls"}],
    }
    graph, stats = connect_orphan_chunks(graph)
    assert stats == {"files": 1, "linked": 2, "file_nodes_created": 1}
    part_of = [e for e in graph["links"] if e["relation"] == "part_of"]
    assert len(part_of) == 2
    anchor_ids = {e["target"] for e in part_of}
    assert len(anchor_ids) == 1
    anchor = next(n for n in graph["nodes"] if n["id"] in anchor_ids)
    assert anchor["label"] == "rulings.md"


def test_connect_orphan_chunks_prefers_existing_anchor():
    from paragraph.build import connect_orphan_chunks
    graph = {
        "nodes": [
            {"id": "doc_main", "label": "Guide", "file_type": "document", "source_file": "docs/g.md"},
            {"id": "doc_chunk", "label": "Guide chunk", "file_type": "document", "source_file": "docs/g.md"},
            {"id": "other", "label": "Other", "file_type": "code", "source_file": "x.py"},
        ],
        "links": [{"source": "doc_main", "target": "other", "relation": "references"}],
    }
    graph, stats = connect_orphan_chunks(graph)
    assert stats["file_nodes_created"] == 0
    part_of = [e for e in graph["links"] if e["relation"] == "part_of"]
    assert part_of == [e for e in part_of if e["target"] == "doc_main"]


def test_connect_orphan_chunks_leaves_code_and_observations_alone():
    from paragraph.build import connect_orphan_chunks
    graph = {
        "nodes": [
            {"id": "lonely_code", "label": "Lonely", "file_type": "code", "source_file": "z.py"},
            {"id": "claudemem_1", "label": "Obs", "file_type": "observation"},
        ],
        "links": [],
    }
    graph, stats = connect_orphan_chunks(graph)
    assert stats["linked"] == 0
    assert len(graph["links"]) == 0
