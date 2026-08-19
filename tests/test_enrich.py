import json
import sqlite3
from pathlib import Path

from paragraph.enrich import (
    Embedder, enrich_bodies, enrich_timestamps, build_children_index,
    build_embed_text, embed_nodes, init_vector_store, store_embeddings, run,
)


class StubEmbedder(Embedder):
    """Deterministic embedder — no ollama needed."""
    def embed(self, text: str):
        return [float(len(text) % 7), 1.0, 0.5]


def make_project(tmp_path):
    src = tmp_path / "app.swift"
    src.write_text(
        "/// Session manager\n"
        "class SessionManager {\n"
        "    var token: String = \"\"\n"
        "    func login() {\n"
        "        validate()\n"
        "    }\n"
        "}\n"
        "func validate() -> Bool {\n"
        "    return true\n"
        "}\n"
    )
    out = tmp_path / "graphify-out"
    out.mkdir()
    graph = {
        "nodes": [
            {"id": "sessionmanager", "label": "SessionManager", "file_type": "code",
             "source_file": str(src), "source_location": "L2"},
            {"id": "sessionmanager_login", "label": ".login()", "file_type": "code",
             "source_file": str(src), "source_location": "L4"},
            {"id": "validate", "label": "validate()", "file_type": "code",
             "source_file": str(src), "source_location": "L8"},
            {"id": "obs1", "label": "Auth decision", "file_type": "observation",
             "observation_type": "decision", "narrative": "Chose token auth.",
             "created_at": "2026-01-01T00:00:00Z"},
        ],
        "links": [
            {"source": "sessionmanager", "target": "sessionmanager_login", "relation": "method"},
        ],
    }
    (out / "graph.json").write_text(json.dumps(graph))
    return graph, out


def test_enrich_bodies_adds_source_body(tmp_path):
    graph, _ = make_project(tmp_path)
    count = enrich_bodies(graph)
    assert count >= 2
    by_id = {n["id"]: n for n in graph["nodes"]}
    # Container body spans up to its first child node and keeps declarations
    assert "class SessionManager" in by_id["sessionmanager"]["source_body"]
    assert "var token" in by_id["sessionmanager"]["source_body"]
    # Leaf body is the full method source
    assert "validate()" in by_id["sessionmanager_login"]["source_body"]


def test_enrich_timestamps_all_nodes(tmp_path):
    graph, _ = make_project(tmp_path)
    count = enrich_timestamps(graph)
    assert count == len(graph["nodes"])
    for n in graph["nodes"]:
        assert n.get("captured_at") and n.get("last_touched_at")
    obs = next(n for n in graph["nodes"] if n["id"] == "obs1")
    assert obs["captured_at"] == "2026-01-01T00:00:00Z"


def test_embed_and_store_roundtrip(tmp_path):
    graph, out = make_project(tmp_path)
    embedded, results = embed_nodes(graph, StubEmbedder())
    assert embedded == len(results) == 4
    conn = init_vector_store(out / "vectors.db")
    stored = store_embeddings(conn, results)
    assert stored == 4
    rows = conn.execute("SELECT node_id, embedding, file_type FROM embeddings").fetchall()
    conn.close()
    assert len(rows) == 4
    assert all(json.loads(r[1]) for r in rows)


def test_container_embed_text_includes_members(tmp_path):
    graph, _ = make_project(tmp_path)
    idx = build_children_index(graph)
    container = next(n for n in graph["nodes"] if n["id"] == "sessionmanager")
    text = build_embed_text(container, idx)
    assert "Members:" in text and ".login()" in text


def test_run_bodies_only_no_ollama(tmp_path):
    make_project(tmp_path)
    assert run(tmp_path, bodies_only=True) == 0
    data = json.loads((tmp_path / "graphify-out" / "graph.json").read_text())
    assert any(n.get("source_body") for n in data["nodes"])
    assert not (tmp_path / "graphify-out" / "vectors.db").exists()


def test_run_missing_graph_errors(tmp_path):
    assert run(tmp_path, bodies_only=True) == 1
