# embedding-based doc<->code bridging
#
# The LLM semantic pass only links concepts that co-occur in one extraction
# chunk, and chunks group by directory — so documents and the code they
# govern almost never meet (observed: 6 doc<->code edges out of ~7,000 in a
# corpus with 1,900+ document nodes). This module closes that gap
# deterministically using the embeddings that `paragraph enrich` already
# stored in vectors.db: for each document/rationale node, find the most
# similar code nodes and add `conceptually_related_to` INFERRED edges.
#
# Edges written by this module carry `"origin": "paragraph-link"` and are
# replaced wholesale on every run, so the command is idempotent and safe to
# re-run after threshold changes.
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

EDGE_ORIGIN = "paragraph-link"
DEFAULT_THRESHOLD = 0.78
DEFAULT_TOP_K = 3

try:
    import numpy as _np
except ImportError:  # pure-Python fallback, fine for small graphs/tests
    _np = None

# Above this many similarity pairs, the pure-Python path is too slow to be
# useful — tell the user to install numpy instead of hanging for minutes.
_PUREPY_PAIR_LIMIT = 500_000


def _load_embeddings(db_path: Path, node_ids: set[str]) -> dict[str, list[float]]:
    """node_id -> embedding for the ids present in vectors.db.

    If several embed_models are present, only the most common one is used so
    cosine similarities stay comparable.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT embed_model, COUNT(*) c FROM embeddings GROUP BY embed_model ORDER BY c DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {}
        model = row[0]
        out: dict[str, list[float]] = {}
        for node_id, blob in conn.execute(
            "SELECT node_id, embedding FROM embeddings WHERE embed_model = ?", (model,)
        ):
            if node_id in node_ids:
                try:
                    out[node_id] = json.loads(blob)
                except (json.JSONDecodeError, TypeError):
                    continue
        return out
    finally:
        conn.close()


def _top_similar(src_vecs: dict[str, list[float]],
                 tgt_vecs: dict[str, list[float]],
                 threshold: float, top_k: int) -> dict[str, list[tuple[str, float]]]:
    """For each source id, the top_k target ids with cosine >= threshold."""
    if not src_vecs or not tgt_vecs:
        return {}
    src_ids = list(src_vecs)
    tgt_ids = list(tgt_vecs)

    if _np is not None:
        S = _np.asarray([src_vecs[i] for i in src_ids], dtype=_np.float32)
        T = _np.asarray([tgt_vecs[i] for i in tgt_ids], dtype=_np.float32)
        S /= _np.maximum(_np.linalg.norm(S, axis=1, keepdims=True), 1e-12)
        T /= _np.maximum(_np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
        sims = S @ T.T  # (n_src, n_tgt)
        result: dict[str, list[tuple[str, float]]] = {}
        for i, sid in enumerate(src_ids):
            row = sims[i]
            k = min(top_k, len(tgt_ids))
            idx = _np.argpartition(-row, k - 1)[:k] if k < len(tgt_ids) else _np.arange(len(tgt_ids))
            picks = [(tgt_ids[j], float(row[j])) for j in idx if row[j] >= threshold]
            picks.sort(key=lambda p: -p[1])
            if picks:
                result[sid] = picks[:top_k]
        return result

    if len(src_ids) * len(tgt_ids) > _PUREPY_PAIR_LIMIT:
        raise RuntimeError(
            f"{len(src_ids)} x {len(tgt_ids)} similarity pairs is too many for the "
            f"pure-Python path. Install numpy (pip install numpy) and re-run."
        )

    import math

    def _norm(v: list[float]) -> float:
        return math.sqrt(sum(x * x for x in v)) or 1e-12

    src_norm = {i: _norm(v) for i, v in src_vecs.items()}
    tgt_norm = {i: _norm(v) for i, v in tgt_vecs.items()}
    result = {}
    for sid in src_ids:
        sv, sn = src_vecs[sid], src_norm[sid]
        scored = []
        for tid in tgt_ids:
            tv = tgt_vecs[tid]
            sim = sum(a * b for a, b in zip(sv, tv)) / (sn * tgt_norm[tid])
            if sim >= threshold:
                scored.append((tid, sim))
        scored.sort(key=lambda p: -p[1])
        if scored:
            result[sid] = scored[:top_k]
    return result


def link_similar(graph_data: dict, vectors_db: Path, *,
                 threshold: float = DEFAULT_THRESHOLD,
                 top_k: int = DEFAULT_TOP_K,
                 source_types: tuple[str, ...] = ("document", "rationale")) -> tuple[dict, dict]:
    """Replace paragraph-link edges with fresh doc->code similarity edges.

    Sources: nodes whose file_type is in source_types.
    Targets: code nodes with a real source_file (external/shadow symbols are
    never link targets). Existing non-link edges between a pair suppress a
    new link edge — the relation is already known more precisely.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("links", graph_data.get("edges", []))

    kept_edges = [e for e in edges if e.get("origin") != EDGE_ORIGIN]
    removed = len(edges) - len(kept_edges)

    src_ids = {n["id"] for n in nodes if n.get("file_type") in source_types}
    tgt_ids = {n["id"] for n in nodes
               if n.get("file_type") == "code" and n.get("source_file")}

    src_vecs = _load_embeddings(vectors_db, src_ids)
    tgt_vecs = _load_embeddings(vectors_db, tgt_ids)

    def _endpoints(e: dict) -> tuple[str | None, str | None]:
        return (e.get("source", e.get("_src")), e.get("target", e.get("_tgt")))

    already = set()
    for e in kept_edges:
        s, t = _endpoints(e)
        already.add((s, t))
        already.add((t, s))

    matches = _top_similar(src_vecs, tgt_vecs, threshold, top_k)
    new_edges = []
    for sid, picks in matches.items():
        for tid, sim in picks:
            if (sid, tid) in already:
                continue
            new_edges.append({
                "source": sid,
                "target": tid,
                "_src": sid,
                "_tgt": tid,
                "relation": "conceptually_related_to",
                "confidence": "INFERRED",
                "confidence_score": round(sim, 3),
                "weight": round(sim, 3),
                "origin": EDGE_ORIGIN,
                "source_file": None,
                "source_location": None,
            })

    out = dict(graph_data)
    key = "links" if ("links" in graph_data or "edges" not in graph_data) else "edges"
    out[key] = kept_edges + new_edges
    stats = {
        "removed_stale": removed,
        "added": len(new_edges),
        "sources_embedded": len(src_vecs),
        "targets_embedded": len(tgt_vecs),
        "sources_linked": len(matches),
    }
    return out, stats


def run(watch_path: Path, *, threshold: float = DEFAULT_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
        source_types: tuple[str, ...] = ("document", "rationale"),
        dry_run: bool = False) -> int:
    graph_json = watch_path / "graphify-out" / "graph.json"
    vectors_db = watch_path / "graphify-out" / "vectors.db"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        return 1
    if not vectors_db.exists():
        print(f"error: no vectors.db at {vectors_db} — run `paragraph enrich` first", file=sys.stderr)
        return 1
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    try:
        data, stats = link_similar(data, vectors_db, threshold=threshold,
                                   top_k=top_k, source_types=source_types)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Embedded sources: {stats['sources_embedded']} ({'/'.join(source_types)}) | "
          f"embedded code targets: {stats['targets_embedded']}")
    print(f"Stale paragraph-link edges removed: {stats['removed_stale']}")
    print(f"New conceptually_related_to edges: {stats['added']} "
          f"across {stats['sources_linked']} source node(s) "
          f"(threshold {threshold}, top-k {top_k})")
    if dry_run:
        print("Dry run — graph.json not modified.")
        return 0
    if stats["added"] == 0 and stats["removed_stale"] == 0:
        print("Nothing to do.")
        return 0
    graph_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Run `paragraph cluster-only {watch_path}` to re-cluster and refresh the report/viz.")
    return 0
