"""Tests for paragraph.retrieve — diversity-aware embedding retrieval.

Pure parts (budget mapping, ranking, tie-breaking, packing, formatting, eval
scoring) are tested with synthetic fixtures. The ollama embedding call is
monkeypatched — no network or local model is needed.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from paragraph.retrieve import (
    BUDGET_TYPE_MAP,
    Chunk,
    GraphIndex,
    RetrievalResult,
    apply_type_budget_and_rerank,
    budget_type,
    compute_recency,
    cosine_similarity,
    expand_parents,
    format_json,
    format_text,
    pack_into_budget,
    retrieve,
    score_query,
    surface_obs_linked_code,
    vector_retrieve,
)
import paragraph.retrieve as retrieve_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_vectors_db(rows: list[dict]) -> sqlite3.Connection:
    """In-memory vectors.db with the enrichment-pipeline schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE embeddings (
            node_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            embed_model TEXT NOT NULL,
            text TEXT,
            file_type TEXT,
            label TEXT,
            source_file TEXT,
            community INTEGER,
            observation_type TEXT,
            captured_at TEXT,
            last_touched_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for r in rows:
        conn.execute(
            """
            INSERT INTO embeddings
                (node_id, embedding, embed_model, text, file_type, label,
                 source_file, community, captured_at, last_touched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["node_id"], json.dumps(r["embedding"]), "nomic-embed-text",
                r.get("text", ""), r.get("file_type", "code"),
                r.get("label", r["node_id"]), r.get("source_file", ""),
                r.get("community", -1), r.get("captured_at", ""),
                r.get("last_touched_at", ""),
            ),
        )
    conn.commit()
    return conn


def _candidate(node_id: str, sim: float, file_type: str = "code", **kw) -> dict:
    c = {
        "node_id": node_id,
        "similarity": sim,
        "text": kw.pop("text", f"text of {node_id}"),
        "file_type": file_type,
        "label": kw.pop("label", node_id),
        "source_file": kw.pop("source_file", ""),
        "community": kw.pop("community", -1),
        "captured_at": kw.pop("captured_at", ""),
        "last_touched_at": kw.pop("last_touched_at", ""),
        "match_reason": kw.pop("match_reason", "vector"),
    }
    c.update(kw)
    return c


EMPTY_GIDX = GraphIndex({"nodes": [], "links": []})


# ---------------------------------------------------------------------------
# Budget mapping
# ---------------------------------------------------------------------------
def test_budget_type_known_fine_types():
    assert budget_type("document", "ruling") == "concept"
    assert budget_type("code", "code") == "code"
    assert budget_type("document", "doc") == "document"
    assert budget_type("observation", "observation") == "observation"
    assert budget_type("annotation", "annotation") == "annotation"


def test_budget_type_fine_type_takes_precedence_over_file_type():
    # Fine-grained LLM type wins over graphify-compliant file_type
    assert budget_type("code", "decision") == "concept"
    assert budget_type("document", "code") == "code"


def test_budget_type_falls_back_to_file_type_when_fine_type_empty():
    assert budget_type("code") == "code"
    assert budget_type("document", "") == "document"


def test_budget_type_unknown_maps_to_concept():
    # service, component, spec, persona etc. all collapse to concept
    for t in ("service", "component", "spec", "persona", "mechanism", ""):
        assert budget_type(t) == "concept"


def test_budget_type_map_concept_collapse_members():
    for fine in ("rationale", "ruling", "decision", "theory", "framework",
                 "methodology", "constraint", "pattern", "anti_pattern", "algorithm"):
        assert BUDGET_TYPE_MAP[fine] == "concept"


# ---------------------------------------------------------------------------
# Similarity + recency
# ---------------------------------------------------------------------------
def test_cosine_similarity_identical_and_orthogonal():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_is_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_compute_recency_neutral_when_unknown_or_malformed():
    assert compute_recency("") == 0.5
    assert compute_recency("not-a-date") == 0.5


def test_compute_recency_recent_beats_old():
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    recent = compute_recency(now)
    old = compute_recency("2020-01-01T00:00:00")
    assert recent > 0.9
    assert old < 0.01
    assert recent > old


# ---------------------------------------------------------------------------
# GraphIndex
# ---------------------------------------------------------------------------
def test_graph_index_parent_and_obs_links():
    graph = {
        "nodes": [
            {"id": "Cls"}, {"id": "Cls.method"}, {"id": "claudemem_1"}, {"id": "Other"},
        ],
        "links": [
            {"source": "Cls", "target": "Cls.method", "relation": "contains"},
            {"source": "claudemem_1", "target": "Cls", "relation": "investigated"},
            {"source": "claudemem_1", "target": "Other", "relation": "modified"},
            {"source": "Cls", "target": "Other", "relation": "calls"},
        ],
    }
    gidx = GraphIndex(graph)
    assert gidx.parent_of("Cls.method") == "Cls"
    assert gidx.parent_of("Cls") is None
    assert gidx.obs_linked_code("claudemem_1") == ["Cls", "Other"]
    assert gidx.obs_linked_code("claudemem_2") == []


# ---------------------------------------------------------------------------
# Vector retrieval (cached store, no ollama)
# ---------------------------------------------------------------------------
def test_vector_retrieve_ranks_by_similarity_and_caps_top_n():
    conn = _make_vectors_db([
        {"node_id": "far", "embedding": [0.0, 1.0]},
        {"node_id": "near", "embedding": [1.0, 0.0]},
        {"node_id": "mid", "embedding": [1.0, 1.0]},
    ])
    results = vector_retrieve(conn, [1.0, 0.0], top_n=2)
    assert [r["node_id"] for r in results] == ["near", "mid"]
    assert results[0]["similarity"] == pytest.approx(1.0)


def test_vector_retrieve_tie_break_preserves_row_order():
    # Stable sort: equal similarity keeps DB row order (deterministic)
    conn = _make_vectors_db([
        {"node_id": "first", "embedding": [1.0, 0.0]},
        {"node_id": "second", "embedding": [1.0, 0.0]},
    ])
    results = vector_retrieve(conn, [1.0, 0.0], top_n=10)
    assert [r["node_id"] for r in results] == ["first", "second"]


# ---------------------------------------------------------------------------
# Graph expansion
# ---------------------------------------------------------------------------
def test_expand_parents_promotes_container_with_discounted_score():
    gidx = GraphIndex({
        "nodes": [{"id": "Parent", "label": "Parent", "file_type": "code",
                   "source_body": "class Parent { ... }"}],
        "links": [{"source": "Parent", "target": "child", "relation": "contains"}],
    })
    cands = [_candidate("child", 0.8)]
    out = expand_parents(cands, gidx)
    assert len(out) == 2
    added = out[1]
    assert added["node_id"] == "Parent"
    assert added["similarity"] == pytest.approx(0.8 * 0.90)
    assert added["match_reason"] == "parent_promotion"


def test_expand_parents_skips_non_code_and_existing():
    gidx = GraphIndex({
        "nodes": [{"id": "Parent", "label": "Parent"}],
        "links": [{"source": "Parent", "target": "child", "relation": "contains"}],
    })
    # non-code candidate: no promotion
    assert len(expand_parents([_candidate("child", 0.8, file_type="document")], gidx)) == 1
    # parent already present: no duplicate
    cands = [_candidate("child", 0.8), _candidate("Parent", 0.5)]
    assert len(expand_parents(cands, gidx)) == 2


def test_surface_obs_linked_code_adds_linked_code():
    gidx = GraphIndex({
        "nodes": [{"id": "Svc", "label": "Svc", "file_type": "code", "source_body": "class Svc"}],
        "links": [{"source": "claudemem_9", "target": "Svc", "relation": "modified"}],
    })
    cands = [_candidate("claudemem_9", 0.7, file_type="observation")]
    out = surface_obs_linked_code(cands, gidx)
    assert len(out) == 2
    assert out[1]["node_id"] == "Svc"
    assert out[1]["similarity"] == pytest.approx(0.7 * 0.85)
    assert out[1]["match_reason"] == "obs_linked"


# ---------------------------------------------------------------------------
# Type budget + re-ranking
# ---------------------------------------------------------------------------
def test_rerank_orders_by_final_score():
    # No last_touched -> recency 0.5 for all -> order follows similarity
    cands = [_candidate(f"n{i}", sim) for i, sim in enumerate([0.2, 0.9, 0.5])]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    assert [c["node_id"] for c in ranked] == ["n1", "n2", "n0"]
    # final_score = 0.95 * sim + 0.05 * 0.5
    assert ranked[0]["final_score"] == pytest.approx(0.95 * 0.9 + 0.05 * 0.5)


def test_rerank_tie_break_is_stable_on_input_order():
    cands = [_candidate("a", 0.5), _candidate("b", 0.5), _candidate("c", 0.5)]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    assert [c["node_id"] for c in ranked] == ["a", "b", "c"]


def test_small_top_k_caps_below_floor():
    # Documented quirk (faithful to the original): with top_k=3,
    # max_per_type = int(3 * 0.60) = 1, which is below the phase-1 floor of 2,
    # so phase 2 adds nothing and only 2 results come back for a single type.
    cands = [_candidate(f"n{i}", 0.9 - i * 0.1) for i in range(3)]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=3)
    assert [c["node_id"] for c in ranked] == ["n0", "n1"]


def test_type_budget_floor_guarantees_minority_type():
    # 20 code candidates outscore 10 document candidates; documents have
    # >= 10% presence so MIN_PER_TYPE=2 documents are guaranteed in top 10,
    # and phase 2 can add more once code hits the 60% cap.
    cands = [_candidate(f"code{i}", 0.9 - i * 0.001) for i in range(20)]
    cands += [_candidate(f"doc{i}", 0.1 - i * 0.001, file_type="document") for i in range(10)]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    assert len(ranked) == 10
    doc_count = sum(1 for c in ranked if c["file_type"] == "document")
    code_count = sum(1 for c in ranked if c["file_type"] == "code")
    assert doc_count >= 2          # floor honored despite low scores
    assert code_count == 6         # dominant type capped at 60%
    # exactly MIN_PER_TYPE docs came from the phase-1 floor
    floor_docs = [c for c in ranked
                  if c["file_type"] == "document" and "type_budget" in c["match_reason"]]
    assert len(floor_docs) == 2


def test_type_budget_floor_is_one_for_rare_type():
    # documents < 10% presence -> phase-1 floor is 1, not MIN_PER_TYPE
    cands = [_candidate(f"code{i}", 0.9 - i * 0.001) for i in range(30)]
    cands += [_candidate("doc0", 0.1, file_type="document"),
              _candidate("doc1", 0.09, file_type="document")]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    floor_docs = [c for c in ranked
                  if c["file_type"] == "document" and "type_budget" in c["match_reason"]]
    assert len(floor_docs) == 1


def test_type_budget_max_cap_limits_dominant_type():
    # Phase 2 fill respects max_per_type = int(top_k * 0.60) = 6
    cands = [_candidate(f"code{i}", 0.9 - i * 0.001) for i in range(20)]
    cands += [_candidate(f"doc{i}", 0.5 - i * 0.001, file_type="document") for i in range(20)]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    assert len(ranked) == 10
    counts: dict[str, int] = {}
    for c in ranked:
        counts[c["file_type"]] = counts.get(c["file_type"], 0) + 1
    assert counts["code"] <= 6
    assert counts["document"] <= 6


def test_type_budget_marks_floor_selections():
    # Phase-1 (floor) selections get "+type_budget" appended to match_reason;
    # phase-2 fills keep their original reason.
    cands = [_candidate(f"code{i}", 0.9 - i * 0.001) for i in range(20)]
    cands += [_candidate("doc0", 0.1, file_type="document"),
              _candidate("doc1", 0.09, file_type="document"),
              _candidate("doc2", 0.08, file_type="document")]
    ranked = apply_type_budget_and_rerank(cands, EMPTY_GIDX, top_k=10)
    docs = [c for c in ranked if c["file_type"] == "document"]
    marked = [c for c in docs if "type_budget" in c["match_reason"]]
    unmarked = [c for c in docs if "type_budget" not in c["match_reason"]]
    assert len(marked) == 2                      # floor picks (13% presence -> floor 2)
    assert all(c["match_reason"] == "vector" for c in unmarked)


def test_type_budget_uses_fine_grained_type_from_graph():
    # Node whose graph `type` is "ruling" gets budgeted as concept, not document
    gidx = GraphIndex({"nodes": [{"id": "r1", "type": "ruling"}], "links": []})
    cands = [_candidate("r1", 0.9, file_type="document")]
    ranked = apply_type_budget_and_rerank(cands, gidx, top_k=10)
    assert ranked[0]["node_id"] == "r1"
    assert "_budget_type" not in ranked[0]  # temp field cleaned up


# ---------------------------------------------------------------------------
# Token packing
# ---------------------------------------------------------------------------
def test_pack_into_budget_respects_budget_and_skips_oversize():
    ranked = [
        _candidate("big", 0.9, text="x" * 4000),    # 1020 tokens
        _candidate("small", 0.8, text="x" * 40),    # 30 tokens
    ]
    for c in ranked:
        c["final_score"] = c["similarity"]
    chunks = pack_into_budget(ranked, budget_tokens=100)
    # big skipped (over budget), small still packed
    assert [c.node_id for c in chunks] == ["small"]
    assert chunks[0].tokens_approx == 40 // 4 + 20


def test_pack_into_budget_preserves_rank_order_and_fields():
    ranked = [_candidate("a", 0.9, text="aaaa"), _candidate("b", 0.8, text="bbbb")]
    for c in ranked:
        c["final_score"] = c["similarity"]
    chunks = pack_into_budget(ranked, budget_tokens=8000)
    assert [c.node_id for c in chunks] == ["a", "b"]
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].final_score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# End-to-end retrieve (mocked embedding)
# ---------------------------------------------------------------------------
def test_retrieve_end_to_end_deterministic(monkeypatch):
    monkeypatch.setattr(retrieve_mod, "get_embedding", lambda text: [1.0, 0.0])
    conn = _make_vectors_db([
        {"node_id": "near", "embedding": [1.0, 0.0], "text": "near text", "label": "Near"},
        {"node_id": "mid", "embedding": [1.0, 1.0], "text": "mid text", "label": "Mid"},
        {"node_id": "far", "embedding": [0.0, 1.0], "text": "far text", "label": "Far",
         "file_type": "document"},
    ])
    gidx = GraphIndex({"nodes": [], "links": []})
    r1 = retrieve("q", conn, gidx, top_k=3)
    r2 = retrieve("q", conn, gidx, top_k=3)
    assert [c.node_id for c in r1.chunks] == [c.node_id for c in r2.chunks]
    assert r1.chunks[0].node_id == "near"
    assert r1.query_metadata["embed_model"] == "nomic-embed-text"
    assert r1.provenance[0]["node_id"] == "near"


def test_retrieve_returns_empty_result_when_embedding_fails(monkeypatch):
    monkeypatch.setattr(retrieve_mod, "get_embedding", lambda text: None)
    conn = _make_vectors_db([])
    result = retrieve("q", conn, GraphIndex({}), top_k=3)
    assert result.query == "q"
    assert result.chunks == []


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def _result_fixture() -> RetrievalResult:
    chunk = Chunk(
        node_id="n1", label="ClassificationService", file_type="code",
        text="class ClassificationService {}", similarity=0.91, final_score=0.89,
        source_file="/repo/Sources/ClassificationService.swift",
        match_reason="vector", tokens_approx=27,
    )
    return RetrievalResult(
        query="test query", chunks=[chunk],
        provenance=[{"node_id": "n1", "final_score": 0.89}],
        query_metadata={"embed_model": "nomic-embed-text"},
    )


def test_format_text_contains_rank_scores_and_relative_path():
    from pathlib import Path
    out = format_text(_result_fixture(), root=Path("/repo"))
    assert "Query: 'test query'" in out
    assert "1. [code      ] ClassificationService" in out
    assert "score=0.890 sim=0.910 reason=vector" in out
    assert "Sources/ClassificationService.swift" in out
    assert "/repo/Sources" not in out


def test_format_json_round_trips():
    out = json.loads(format_json(_result_fixture()))
    assert out["query"] == "test query"
    assert out["chunks"][0]["node_id"] == "n1"
    assert out["provenance"][0]["final_score"] == 0.89
    assert out["query_metadata"]["embed_model"] == "nomic-embed-text"


# ---------------------------------------------------------------------------
# Eval scoring
# ---------------------------------------------------------------------------
def _eval_result(labels_types_texts: list[tuple[str, str, str]]) -> RetrievalResult:
    chunks = [
        Chunk(node_id=l, label=l, file_type=ft, text=t, similarity=0.5, final_score=0.5)
        for l, ft, t in labels_types_texts
    ]
    return RetrievalResult(query="q", chunks=chunks)


def test_score_query_nodes_substring_case_insensitive():
    q = {"expected_nodes": ["ClassificationService"], "negative_nodes": ["ClassificationStatus"]}
    result = _eval_result([("classificationservice.swift", "code", "")])
    recall, neg, detail = score_query(q, result)
    assert recall == 1.0
    assert neg == 0
    assert detail == "1/1 nodes"


def test_score_query_docs_only_count_document_chunks():
    q = {"expected_docs": ["haptic"]}
    # keyword present but in a code chunk -> no docs credit
    recall, _, _ = score_query(q, _eval_result([("X", "code", "haptic ruling")]))
    assert recall == 0.0
    recall, _, _ = score_query(q, _eval_result([("X", "document", "haptic ruling")]))
    assert recall == 1.0


def test_score_query_obs_any_keyword_any_chunk():
    q = {"expected_observations": ["120s", "idle"]}
    recall, _, detail = score_query(q, _eval_result([("X", "code", "the 120s timeout")]))
    assert recall == 1.0
    assert detail == "1/1 obs"


def test_score_query_mixed_partial_recall_and_negatives():
    q = {
        "expected_nodes": ["Alpha", "Beta"],
        "expected_docs": ["gamma"],
        "negative_nodes": ["Bad"],
    }
    result = _eval_result([("Alpha", "code", ""), ("BadService", "code", "")])
    recall, neg, detail = score_query(q, result)
    assert recall == pytest.approx(1 / 3)
    assert neg == 1
    assert detail == "1/2 nodes, 0/1 docs"


def test_score_query_no_expectations_is_perfect_recall():
    recall, neg, detail = score_query({}, _eval_result([]))
    assert recall == 1.0
    assert neg == 0
    assert detail == "n/a"


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def test_main_errors_when_vector_store_missing(tmp_path):
    from paragraph.retrieve import main
    rc = main(["some query", "--graph", str(tmp_path / "graph.json")])
    assert rc == 1


def test_main_errors_when_graph_missing(tmp_path):
    from paragraph.retrieve import main
    vdb = tmp_path / "vectors.db"
    sqlite3.connect(str(vdb)).close()
    rc = main(["some query", "--graph", str(tmp_path / "graph.json"), "--vectors", str(vdb)])
    assert rc == 1


def test_main_defaults_match_original_script():
    # Parity guard: defaults must match scripts/graph-query.py exactly
    assert retrieve_mod.VECTOR_TOP_N == 50
    assert retrieve_mod.PARENT_SCORE_FACTOR == 0.90
    assert retrieve_mod.RECENCY_HALF_LIFE_DAYS == 90
    assert retrieve_mod.RECENCY_WEIGHT == 0.05
    assert retrieve_mod.MIN_PER_TYPE == 2
    assert retrieve_mod.MAX_TYPE_FRACTION == 0.60
    assert retrieve_mod.DEFAULT_BUDGET_TOKENS == 8000
    assert retrieve_mod.CHARS_PER_TOKEN == 4
    assert retrieve_mod.EMBED_MODEL == "nomic-embed-text"
