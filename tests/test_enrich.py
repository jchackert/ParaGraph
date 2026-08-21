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
    embedded, skipped, results = embed_nodes(graph, StubEmbedder())
    assert embedded == len(results) == 4
    assert skipped == 0
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


def test_document_embed_text_includes_chunk_body():
    # Regression: label-only document embeds clobbered rich chunk embeddings
    node = {"id": "doc_x_0", "label": "spec.md: Overview", "file_type": "document",
            "source_body": "The capture queue persists locally and syncs later."}
    text = build_embed_text(node)
    assert "Document: spec.md: Overview" in text
    assert "persists locally" in text


def test_rationale_embed_text_includes_body():
    node = {"id": "memfact_x", "label": "Launch pushed to May 1", "file_type": "rationale",
            "source_body": "PM-1 through PM-5 are launch blockers."}
    text = build_embed_text(node)
    assert text.startswith("Rationale: Launch pushed to May 1")
    assert "launch blockers" in text


def test_run_bodies_only_no_ollama(tmp_path):
    make_project(tmp_path)
    assert run(tmp_path, bodies_only=True) == 0
    data = json.loads((tmp_path / "graphify-out" / "graph.json").read_text())
    assert any(n.get("source_body") for n in data["nodes"])
    assert not (tmp_path / "graphify-out" / "vectors.db").exists()


def test_run_missing_graph_errors(tmp_path):
    assert run(tmp_path, bodies_only=True) == 1


def test_incremental_embed_skips_unchanged(tmp_path):
    from paragraph.enrich import load_existing_hashes, prune_deleted_nodes
    graph, out = make_project(tmp_path)
    emb = StubEmbedder()
    _, _, results = embed_nodes(graph, emb)
    conn = init_vector_store(out / "vectors.db")
    store_embeddings(conn, results)

    # Re-run with existing hashes: everything unchanged -> all skipped
    existing = load_existing_hashes(conn, emb.model_name)
    embedded, skipped, results2 = embed_nodes(graph, emb, existing_hashes=existing)
    assert embedded == 0 and skipped == 4 and results2 == []

    # Change one node's label -> only that node re-embeds
    graph["nodes"][0]["label"] = "SessionManagerRenamed"
    embedded, skipped, results3 = embed_nodes(graph, emb, existing_hashes=existing)
    assert embedded == 1 and skipped == 3
    assert results3[0]["node_id"] == "sessionmanager"

    # Deleting a node prunes its embedding row
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "validate"]
    assert prune_deleted_nodes(conn, graph) == 1
    remaining = {r[0] for r in conn.execute("SELECT node_id FROM embeddings")}
    assert "validate" not in remaining
    conn.close()


def test_old_schema_migrates(tmp_path):
    import sqlite3 as _sq
    from paragraph.enrich import load_existing_hashes
    db = tmp_path / "vectors.db"
    conn = _sq.connect(str(db))
    conn.execute("""CREATE TABLE embeddings (
        node_id TEXT PRIMARY KEY, embedding BLOB NOT NULL, embed_model TEXT NOT NULL,
        text TEXT, file_type TEXT, label TEXT, source_file TEXT, community INTEGER,
        observation_type TEXT, captured_at TEXT, last_touched_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()
    conn = init_vector_store(db)  # migration adds text_hash
    assert load_existing_hashes(conn, "nomic-embed-text") == {}
    cols = [r[1] for r in conn.execute("PRAGMA table_info(embeddings)")]
    assert "text_hash" in cols
    conn.close()
