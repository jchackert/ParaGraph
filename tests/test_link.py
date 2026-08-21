import json
import sqlite3

import pytest

from paragraph.link import link_similar, EDGE_ORIGIN


def _mk_vectors_db(tmp_path, rows):
    """rows: list of (node_id, embedding_list, model)."""
    db = tmp_path / "vectors.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE embeddings (
            node_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            embed_model TEXT NOT NULL,
            text TEXT, file_type TEXT, label TEXT, source_file TEXT,
            community INTEGER, observation_type TEXT,
            captured_at TEXT, last_touched_at TEXT, text_hash TEXT
        )
    """)
    for nid, emb, model in rows:
        conn.execute(
            "INSERT INTO embeddings (node_id, embedding, embed_model) VALUES (?, ?, ?)",
            (nid, json.dumps(emb).encode(), model))
    conn.commit()
    conn.close()
    return db


def _graph():
    return {
        "nodes": [
            {"id": "doc1", "label": "Crisis handling spec", "file_type": "document",
             "source_file": "docs/crisis.md"},
            {"id": "rat1", "label": "Why we debounce saves", "file_type": "rationale",
             "source_file": "docs/rationale.md"},
            {"id": "code1", "label": "SafetyService", "file_type": "code",
             "source_file": "src/SafetyService.swift"},
            {"id": "code2", "label": "SoundService", "file_type": "code",
             "source_file": "src/SoundService.swift"},
            {"id": "shadow", "label": "External", "file_type": "code",
             "source_file": ""},
        ],
        "links": [],
    }


def test_links_similar_doc_to_code(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0, 0.0], "m"),
        ("rat1", [0.0, 1.0, 0.0], "m"),
        ("code1", [0.9, 0.1, 0.0], "m"),   # close to doc1
        ("code2", [0.0, 0.0, 1.0], "m"),   # orthogonal to both
    ])
    out, stats = link_similar(_graph(), db, threshold=0.8, top_k=3)
    edges = [e for e in out["links"] if e.get("origin") == EDGE_ORIGIN]
    assert stats["added"] == 1
    assert len(edges) == 1
    e = edges[0]
    assert (e["source"], e["target"]) == ("doc1", "code1")
    assert e["relation"] == "conceptually_related_to"
    assert e["confidence"] == "INFERRED"
    assert 0.8 <= e["confidence_score"] <= 1.0


def test_shadow_code_nodes_never_targets(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0], "m"),
        ("shadow", [1.0, 0.0], "m"),   # identical vector but empty source_file
    ])
    out, stats = link_similar(_graph(), db, threshold=0.5)
    assert stats["added"] == 0


def test_idempotent_replaces_stale_edges(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0], "m"),
        ("code1", [1.0, 0.0], "m"),
    ])
    g = _graph()
    g["links"] = [{"source": "doc1", "target": "code2", "relation": "conceptually_related_to",
                   "confidence": "INFERRED", "origin": EDGE_ORIGIN}]
    out, stats = link_similar(g, db, threshold=0.9)
    assert stats["removed_stale"] == 1
    link_edges = [e for e in out["links"] if e.get("origin") == EDGE_ORIGIN]
    assert len(link_edges) == 1
    assert link_edges[0]["target"] == "code1"


def test_existing_precise_edge_suppresses_link(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0], "m"),
        ("code1", [1.0, 0.0], "m"),
    ])
    g = _graph()
    g["links"] = [{"source": "doc1", "target": "code1", "relation": "cites",
                   "confidence": "EXTRACTED"}]
    out, stats = link_similar(g, db, threshold=0.9)
    assert stats["added"] == 0
    assert len(out["links"]) == 1  # the cites edge, untouched


def test_top_k_and_threshold(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0, 0.0], "m"),
        ("code1", [1.0, 0.0, 0.0], "m"),
        ("code2", [0.95, 0.31225, 0.0], "m"),
    ])
    out, stats = link_similar(_graph(), db, threshold=0.9, top_k=1)
    edges = [e for e in out["links"] if e.get("origin") == EDGE_ORIGIN]
    assert len(edges) == 1
    assert edges[0]["target"] == "code1"  # highest similarity wins


def test_minority_embed_model_ignored(tmp_path):
    db = _mk_vectors_db(tmp_path, [
        ("doc1", [1.0, 0.0], "m"),
        ("code1", [1.0, 0.0], "m"),
        ("code2", [1.0], "other-model"),
    ])
    out, stats = link_similar(_graph(), db, threshold=0.9)
    targets = {e["target"] for e in out["links"] if e.get("origin") == EDGE_ORIGIN}
    assert targets == {"code1"}


def test_missing_embeddings_ok(tmp_path):
    db = _mk_vectors_db(tmp_path, [])
    out, stats = link_similar(_graph(), db)
    assert stats["added"] == 0
    assert out["links"] == []
