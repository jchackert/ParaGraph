"""Diversity-aware embedding retrieval over the knowledge graph.

Faithful port of PARA_Note's scripts/graph-query.py (the evaluated retriever,
recall@10 = 0.792 on the v2 20-query eval as of 2026-04-24). This is the
measured read path — distinct from `paragraph query`, which is a BFS keyword
traversal and was never evaluated.

Pipeline:
  1. Vector retrieve top-50 across all types (cached embeddings in vectors.db)
  2. Graph expansion: promote parent containers of retrieved methods
  3. Context surfacing: link observation edges to code nodes
  4. Type budget: ensure minimum representation of each type
  5. Re-rank with similarity + recency
  6. Pack into token budget with diversity floor

Only the query itself is embedded (via local ollama). The corpus embeddings
come from the vectors.db store built by 'paragraph enrich' — this module
never re-embeds the corpus.

Usage:
    paragraph retrieve "where does the avatar color come from"
    paragraph retrieve --budget 4000 "how does byok work"
    paragraph retrieve --json "nudge suppression"
    paragraph retrieve --eval docs/examples/retrieval_eval.json (template)
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_GRAPH_PATH = Path("graphify-out") / "graph.json"
DEFAULT_VECTORS_DB_PATH = Path("graphify-out") / "vectors.db"

OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

# Retrieval parameters — pinned v1 configuration (docs/RETRIEVAL_EVAL.md; ParaNote runs 7/8).
# Do not tune without re-running the eval.
VECTOR_TOP_N = 50
PARENT_SCORE_FACTOR = 0.90
RECENCY_HALF_LIFE_DAYS = 90
RECENCY_WEIGHT = 0.05
MIN_PER_TYPE = 2
MAX_TYPE_FRACTION = 0.60
DEFAULT_BUDGET_TOKENS = 8000
CHARS_PER_TOKEN = 4

# Type budget categories — collapse 40+ fine-grained LLM types into
# retrieval-relevant macro categories. The budget logic works on these.
BUDGET_TYPE_MAP = {
    "code": "code",
    "document": "document",
    "doc": "document",
    "observation": "observation",
    "annotation": "annotation",
    "concept": "concept",
    "rationale": "concept",
    "ruling": "concept",
    "decision": "concept",
    "theory": "concept",
    "framework": "concept",
    "methodology": "concept",
    "constraint": "concept",
    "pattern": "concept",
    "anti_pattern": "concept",
    "algorithm": "concept",
    # Everything else (service, component, spec, feature, model, entity,
    # mechanism, agent, persona, etc.) maps to "concept" — they're all
    # named ideas extracted by the LLM, distinct from code and prose.
}


def budget_type(file_type: str, fine_type: str = "") -> str:
    """Map to macro budget category. Uses fine-grained `type` field
    (preserved from LLM extraction) when available, falling back to
    `file_type` (graphify-compliant: code/document/rationale/image/paper)."""
    source = fine_type or file_type
    return BUDGET_TYPE_MAP.get(source, "concept")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    node_id: str
    label: str
    file_type: str
    text: str
    similarity: float
    final_score: float
    source_file: str = ""
    community: int = -1
    match_reason: str = "vector"  # vector | parent_promotion | obs_linked | type_budget
    tokens_approx: int = 0


@dataclass
class RetrievalResult:
    query: str
    chunks: list[Chunk] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    query_metadata: dict = field(default_factory=dict)
    # Set only when retrieval could not run at all (embedding backend
    # unreachable). Distinct from an empty `chunks` list, which means retrieval
    # ran and the corpus genuinely had nothing above threshold. Consumers that
    # catch RetrievalUnavailable rather than letting it propagate should
    # populate this so the distinction survives into their output.
    retrieval_failed: str | None = None


class RetrievalUnavailable(RuntimeError):
    """The embedding backend could not be reached, so retrieval never ran.

    This exists because the previous behaviour — returning an empty
    RetrievalResult when ollama was down — was indistinguishable from "the
    graph has nothing relevant for this query". Agents are instructed to run
    graph retrieval before architecture work and report whether the graph
    answered; under the old behaviour an outage was silently laundered into a
    finding about corpus coverage. An unreachable backend is an infrastructure
    failure and must be loud.
    """


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def get_embedding(text: str) -> list[float]:
    """Embed `text` via the local ollama backend.

    Raises RetrievalUnavailable if the backend is unreachable, times out, or
    returns a payload without a usable embedding (e.g. the model is not
    pulled, which comes back as a JSON error body rather than a transport
    error). Never returns None or an empty vector — callers may assume a
    successful return is usable.
    """
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text[:1500]}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as e:
        raise RetrievalUnavailable(
            f"embedding backend unreachable at {OLLAMA_URL} (model {EMBED_MODEL}): {e}"
        ) from e

    embedding = body.get("embedding")
    if not embedding:
        # A 200 with no embedding is how ollama reports a missing model and
        # similar request-level errors. Treating it as "no results" is the
        # same fail-open as a transport error.
        detail = body.get("error") or f"no 'embedding' field in response: {sorted(body)[:5]}"
        raise RetrievalUnavailable(
            f"embedding backend returned no vector for model {EMBED_MODEL}: {detail}"
        )
    return embedding


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# Graph structure (loaded once)
# ---------------------------------------------------------------------------
class GraphIndex:
    """Pre-computed lookups over the graph."""

    def __init__(self, graph: dict):
        self.nodes = {n["id"]: n for n in graph.get("nodes", [])}
        self._parent_of: dict[str, str] = {}
        self._obs_to_code: dict[str, list[str]] = {}

        for e in graph.get("links", []):
            rel = e.get("relation", "")
            src = e.get("source") or e.get("_src", "")
            tgt = e.get("target") or e.get("_tgt", "")
            if rel in ("contains", "method") and src and tgt:
                self._parent_of[tgt] = src
            if src.startswith("claudemem_") and rel in ("investigated", "modified"):
                self._obs_to_code.setdefault(src, []).append(tgt)

    def parent_of(self, node_id: str) -> str | None:
        return self._parent_of.get(node_id)

    def obs_linked_code(self, obs_node_id: str) -> list[str]:
        return self._obs_to_code.get(obs_node_id, [])


# ---------------------------------------------------------------------------
# Step 1: Vector retrieval
# ---------------------------------------------------------------------------
def vector_retrieve(
    conn: sqlite3.Connection, query_emb: list[float], top_n: int = VECTOR_TOP_N
) -> list[dict]:
    rows = conn.execute(
        """SELECT node_id, embedding, text, file_type, label,
                  source_file, community, captured_at, last_touched_at
           FROM embeddings"""
    ).fetchall()

    results = []
    for row in rows:
        emb = json.loads(row[1])
        sim = cosine_similarity(query_emb, emb)
        results.append({
            "node_id": row[0],
            "similarity": sim,
            "text": row[2] or "",
            "file_type": row[3] or "unknown",
            "label": row[4] or "",
            "source_file": row[5] or "",
            "community": row[6] or -1,
            "captured_at": row[7] or "",
            "last_touched_at": row[8] or "",
            "match_reason": "vector",
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_n]


# ---------------------------------------------------------------------------
# Step 2: Graph expansion — parent promotion
# ---------------------------------------------------------------------------
def expand_parents(candidates: list[dict], gidx: GraphIndex) -> list[dict]:
    """For method nodes in candidates, add their parent container if missing."""
    existing_ids = {c["node_id"] for c in candidates}
    additions = []

    for c in candidates:
        if c["file_type"] != "code":
            continue
        pid = gidx.parent_of(c["node_id"])
        if pid and pid not in existing_ids:
            pnode = gidx.nodes.get(pid)
            if pnode:
                additions.append({
                    "node_id": pid,
                    "similarity": c["similarity"] * PARENT_SCORE_FACTOR,
                    "text": pnode.get("source_body", pnode.get("label", ""))[:500],
                    "file_type": pnode.get("file_type", "code"),
                    "label": pnode.get("label", pid),
                    "source_file": pnode.get("source_file", ""),
                    "community": pnode.get("community", -1),
                    "captured_at": pnode.get("captured_at", ""),
                    "last_touched_at": pnode.get("last_touched_at", ""),
                    "match_reason": "parent_promotion",
                })
                existing_ids.add(pid)

    return candidates + additions


# ---------------------------------------------------------------------------
# Step 3: Observation → code context surfacing
# ---------------------------------------------------------------------------
def surface_obs_linked_code(candidates: list[dict], gidx: GraphIndex) -> list[dict]:
    """For observation nodes in candidates, add their linked code nodes."""
    existing_ids = {c["node_id"] for c in candidates}
    additions = []

    for c in candidates:
        if c["file_type"] != "observation":
            continue
        linked = gidx.obs_linked_code(c["node_id"])
        for code_id in linked:
            if code_id not in existing_ids:
                cnode = gidx.nodes.get(code_id)
                if cnode:
                    additions.append({
                        "node_id": code_id,
                        "similarity": c["similarity"] * 0.85,
                        "text": cnode.get("source_body", cnode.get("label", ""))[:500],
                        "file_type": cnode.get("file_type", "code"),
                        "label": cnode.get("label", code_id),
                        "source_file": cnode.get("source_file", ""),
                        "community": cnode.get("community", -1),
                        "captured_at": cnode.get("captured_at", ""),
                        "last_touched_at": cnode.get("last_touched_at", ""),
                        "match_reason": "obs_linked",
                    })
                    existing_ids.add(code_id)

    return candidates + additions


# ---------------------------------------------------------------------------
# Step 4: Recency scoring
# ---------------------------------------------------------------------------
def compute_recency(last_touched: str) -> float:
    """Exponential decay based on days since last touch."""
    if not last_touched:
        return 0.5  # neutral if unknown
    try:
        touched_ts = time.mktime(time.strptime(last_touched[:19], "%Y-%m-%dT%H:%M:%S"))
        days_ago = (time.time() - touched_ts) / 86400
        return math.exp(-days_ago / RECENCY_HALF_LIFE_DAYS)
    except (ValueError, OverflowError):
        return 0.5


# ---------------------------------------------------------------------------
# Step 5: Type budget + re-ranking
# ---------------------------------------------------------------------------
def apply_type_budget_and_rerank(candidates: list[dict], gidx: GraphIndex, top_k: int = 10) -> list[dict]:
    """Re-rank with type diversity constraints.

    1. Score each candidate: (1 - β) * similarity + β * recency
    2. Sort by final score
    3. Greedily fill top_k with diversity floor: at least MIN_PER_TYPE of each
       type if available, no more than MAX_TYPE_FRACTION of total from one type.
    """
    # Score
    for c in candidates:
        recency = compute_recency(c.get("last_touched_at", ""))
        c["recency_factor"] = recency
        c["final_score"] = (1 - RECENCY_WEIGHT) * c["similarity"] + RECENCY_WEIGHT * recency

    candidates.sort(key=lambda x: x["final_score"], reverse=True)

    # Identify available types using macro budget categories.
    # Look up fine-grained `type` from graph (preserved from LLM extraction)
    # for richer budget mapping than graphify-compliant file_type alone.
    from collections import Counter
    type_pool: dict[str, list[dict]] = {}
    for c in candidates:
        graph_node = gidx.nodes.get(c["node_id"], {})
        fine_type = graph_node.get("type", "")
        ft = budget_type(c["file_type"], fine_type)
        c["_budget_type"] = ft
        type_pool.setdefault(ft, []).append(c)

    available_types = [t for t in type_pool if type_pool[t]]
    total_candidates = len(candidates)
    max_per_type = int(top_k * MAX_TYPE_FRACTION)

    # Compute per-type floor: at least 1 of each type present in top-50,
    # but only guarantee MIN_PER_TYPE if the type has >= 10% presence in candidates
    type_floor: dict[str, int] = {}
    for ft in available_types:
        presence = len(type_pool[ft]) / total_candidates if total_candidates else 0
        if presence >= 0.10:
            type_floor[ft] = MIN_PER_TYPE
        else:
            type_floor[ft] = 1  # diversity floor: at least 1 if it exists

    # Phase 1: guarantee floor per type (ordered by best score in each type)
    selected: list[dict] = []
    selected_ids: set[str] = set()
    type_counts: Counter = Counter()

    for ft in available_types:
        floor = type_floor.get(ft, 1)
        for c in type_pool[ft]:
            if len(selected) >= top_k:
                break
            if type_counts[ft] >= floor:
                break
            if c["node_id"] not in selected_ids:
                c["match_reason"] = c.get("match_reason", "vector") + "+type_budget"
                selected.append(c)
                selected_ids.add(c["node_id"])
                type_counts[ft] += 1

    # Phase 2: fill remaining slots by score, respecting max cap
    for c in candidates:
        if len(selected) >= top_k:
            break
        if c["node_id"] in selected_ids:
            continue
        ft = c["_budget_type"]
        if type_counts[ft] >= max_per_type:
            continue
        selected.append(c)
        selected_ids.add(c["node_id"])
        type_counts[ft] += 1

    # Clean up temp field and re-sort by final score
    for c in selected:
        c.pop("_budget_type", None)
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    return selected


# ---------------------------------------------------------------------------
# Step 6: Token budget packing
# ---------------------------------------------------------------------------
def pack_into_budget(ranked: list[dict], budget_tokens: int) -> list[Chunk]:
    """Greedily pack ranked results into token budget."""
    chunks = []
    used_tokens = 0

    for r in ranked:
        text = r.get("text", "")
        approx_tokens = len(text) // CHARS_PER_TOKEN + 20  # overhead for metadata
        if used_tokens + approx_tokens > budget_tokens:
            continue

        chunks.append(Chunk(
            node_id=r["node_id"],
            label=r["label"],
            file_type=r["file_type"],
            text=text,
            similarity=r["similarity"],
            final_score=r["final_score"],
            source_file=r.get("source_file", ""),
            community=r.get("community", -1),
            match_reason=r.get("match_reason", "vector"),
            tokens_approx=approx_tokens,
        ))
        used_tokens += approx_tokens

    return chunks


# ---------------------------------------------------------------------------
# Main retrieve function
# ---------------------------------------------------------------------------
def retrieve(
    seed: str,
    conn: sqlite3.Connection,
    gidx: GraphIndex,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    top_k: int = 10,
) -> RetrievalResult:
    """Full retrieval pipeline.

    Raises RetrievalUnavailable if the embedding backend is unreachable. This
    propagates deliberately rather than degrading to an empty result: a caller
    that cannot tell "retrieval failed" from "nothing matched" will report the
    latter, which is how an outage becomes a false statement about the corpus.
    """
    query_emb = get_embedding(seed)

    # Step 1: Vector retrieval
    candidates = vector_retrieve(conn, query_emb, VECTOR_TOP_N)

    # Capture pre-expansion type distribution for metadata
    from collections import Counter
    pre_types = Counter(c["file_type"] for c in candidates)

    # Step 2: Graph expansion — parent promotion
    candidates = expand_parents(candidates, gidx)

    # Step 3: Observation → code context surfacing
    candidates = surface_obs_linked_code(candidates, gidx)

    # Step 4+5: Type budget + re-ranking (includes recency)
    ranked = apply_type_budget_and_rerank(candidates, gidx, top_k=top_k)

    # Step 6: Token budget packing
    chunks = pack_into_budget(ranked, budget_tokens)

    # Build provenance
    provenance = []
    for c in ranked:
        provenance.append({
            "node_id": c["node_id"],
            "label": c["label"],
            "file_type": c["file_type"],
            "similarity": round(c["similarity"], 4),
            "recency_factor": round(c.get("recency_factor", 0), 4),
            "final_score": round(c["final_score"], 4),
            "match_reason": c.get("match_reason", "vector"),
        })

    post_types = Counter(c.file_type for c in chunks)
    metadata = {
        "top50_type_distribution": dict(pre_types),
        "final_type_distribution": dict(post_types),
        "candidates_after_expansion": len(candidates),
        "total_tokens_used": sum(c.tokens_approx for c in chunks),
        "budget_tokens": budget_tokens,
        "embed_model": EMBED_MODEL,
    }

    return RetrievalResult(query=seed, chunks=chunks, provenance=provenance, query_metadata=metadata)


# ---------------------------------------------------------------------------
# Eval scoring (protocol: docs/RETRIEVAL_EVAL.md)
# ---------------------------------------------------------------------------
def score_query(q: dict, result: RetrievalResult) -> tuple[float, int, str]:
    """Score one eval query against a retrieval result.

    Returns (recall, negative_hits, detail_string). Scoring is identical to
    the original run_eval_with_graph_query: expected_nodes match by
    case-insensitive substring on chunk labels; expected_observations and
    expected_docs are keyword groups scored 0/1 against chunk texts
    (docs restricted to file_type == "document").
    """
    labels = [c.label for c in result.chunks]
    texts = [c.text for c in result.chunks]
    doc_texts = [c.text for c in result.chunks if c.file_type == "document"]

    expected_nodes = q.get("expected_nodes", [])
    expected_obs = q.get("expected_observations", [])
    expected_docs = q.get("expected_docs", [])
    negative_nodes = q.get("negative_nodes", [])

    found_nodes = sum(1 for exp in expected_nodes if any(exp.lower() in l.lower() for l in labels))
    found_obs = 1 if expected_obs and any(any(kw.lower() in t.lower() for kw in expected_obs) for t in texts) else 0
    found_docs = 1 if expected_docs and any(any(kw.lower() in t.lower() for kw in expected_docs) for t in doc_texts) else 0

    total_expected = len(expected_nodes) + (1 if expected_obs else 0) + (1 if expected_docs else 0)
    recall = (found_nodes + found_obs + found_docs) / total_expected if total_expected > 0 else 1.0

    neg_hits = sum(1 for neg in negative_nodes if any(neg.lower() in l.lower() for l in labels))

    parts = []
    if expected_nodes:
        parts.append(f"{found_nodes}/{len(expected_nodes)} nodes")
    if expected_obs:
        parts.append(f"{found_obs}/1 obs")
    if expected_docs:
        parts.append(f"{found_docs}/1 docs")
    detail = ", ".join(parts) if parts else "n/a"
    return recall, neg_hits, detail


def run_eval(eval_path: Path, conn: sqlite3.Connection, gidx: GraphIndex) -> float:
    """Run the labeled retrieval eval. Prints per-query results, returns avg recall@10."""
    eval_data = json.loads(eval_path.read_text())
    queries = eval_data["queries"]

    total_recall = 0.0
    total_queries = len(queries)
    neg_hits_total = 0

    print(f"Running paragraph-retrieve eval: {total_queries} queries")
    print("Pipeline: vector(50) -> parent_expand -> obs_link -> type_budget -> rerank")
    print()

    for q in queries:
        result = retrieve(q["query"], conn, gidx, top_k=10)
        recall, neg_hits, detail = score_query(q, result)
        total_recall += recall
        neg_hits_total += neg_hits

        status = "PASS" if recall >= 0.5 else "FAIL"
        neg_warn = f" NEG_HIT:{neg_hits}" if neg_hits else ""
        print(f"  {status} {q['id']} recall={recall:.2f} ({detail}){neg_warn}")

        if recall < 0.5 or neg_hits:
            for c in result.chunks[:5]:
                print(f"       {c.final_score:.3f} {c.file_type:<12} {c.label[:50]} [{c.match_reason}]")

    avg = total_recall / total_queries
    print()
    print("=" * 60)
    print("PARAGRAPH RETRIEVE: diversity-aware retrieval")
    print(f"  Avg recall@10: {avg:.3f}")
    print(f"  Queries: {total_queries}")
    print(f"  Negative hits: {neg_hits_total}")
    print("=" * 60)
    return avg


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def format_json(result: RetrievalResult) -> str:
    out = {
        "query": result.query,
        "chunks": [asdict(c) for c in result.chunks],
        "provenance": result.provenance,
        "query_metadata": result.query_metadata,
    }
    # Only present when retrieval could not run. A consumer seeing zero chunks
    # must check this field before concluding the corpus had nothing — that
    # conflation is the bug this field exists to prevent.
    if result.retrieval_failed:
        out["retrieval_failed"] = result.retrieval_failed
    return json.dumps(out, indent=2)


def format_text(result: RetrievalResult, root: Path | None = None) -> str:
    """Human-readable output. `root` strips a leading path prefix from
    source files (the project root — parent of the graphify-out dir)."""
    lines = []
    lines.append(f"\nQuery: '{result.query}'")
    lines.append(f"Metadata: {json.dumps(result.query_metadata, indent=2)}")
    lines.append("")
    for i, c in enumerate(result.chunks):
        lines.append(f"  {i+1:>2}. [{c.file_type:<10}] {c.label[:55]}")
        lines.append(f"      score={c.final_score:.3f} sim={c.similarity:.3f} reason={c.match_reason}")
        if c.source_file:
            rel = c.source_file
            if root is not None:
                rel = rel.replace(str(root) + "/", "")
            lines.append(f"      {rel}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="paragraph retrieve",
        description="Diversity-aware embedding retrieval over the knowledge graph",
    )
    parser.add_argument("query", nargs="?", help="Free-text query")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS,
                        help=f"token budget for packed chunks (default {DEFAULT_BUDGET_TOKENS})")
    parser.add_argument("--top-k", type=int, default=10, help="ranked results to keep (default 10)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH_PATH),
                        help="path to graph.json (default graphify-out/graph.json)")
    parser.add_argument("--vectors", default=None,
                        help="path to vectors.db (default: vectors.db next to graph.json)")
    parser.add_argument("--eval", dest="eval_path", metavar="EVAL_JSON", default=None,
                        help="run the labeled retrieval eval at this path instead of a single query")
    args = parser.parse_args(argv)

    graph_path = Path(args.graph)
    vectors_path = Path(args.vectors) if args.vectors else graph_path.parent / "vectors.db"

    if not vectors_path.exists():
        print(f"Vector store not found: {vectors_path}", file=sys.stderr)
        print("Build it first: paragraph enrich . (needs a local ollama with nomic-embed-text)", file=sys.stderr)
        return 1
    if not graph_path.exists():
        print(f"error: graph file not found: {graph_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(vectors_path))
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        gidx = GraphIndex(graph)

        if args.eval_path:
            eval_path = Path(args.eval_path)
            if not eval_path.exists():
                print(f"error: eval file not found: {eval_path}", file=sys.stderr)
                return 1
            try:
                run_eval(eval_path, conn, gidx)
            except RetrievalUnavailable as e:
                # Do not score the remaining queries as misses. A backend that
                # dies mid-eval would otherwise produce a plausible-looking
                # degraded score with no indication the run was invalid.
                print(f"error: retrieval unavailable, eval aborted: {e}", file=sys.stderr)
                return 2
            return 0

        if not args.query:
            print('Usage: paragraph retrieve "<query>" | --eval <eval.json>', file=sys.stderr)
            return 1

        try:
            result = retrieve(args.query, conn, gidx, budget_tokens=args.budget, top_k=args.top_k)
        except RetrievalUnavailable as e:
            # Exit 2, distinct from 1 (bad arguments / missing files), so a
            # caller can tell "retrieval could not run" from "you asked wrong"
            # and from "retrieval ran and found nothing" (exit 0, no chunks).
            print(f"error: retrieval could not run: {e}", file=sys.stderr)
            print(
                "This is an infrastructure failure, NOT a statement about the corpus. "
                "Do not report it as 'the graph had no answer'. "
                f"Check that ollama is running and the {EMBED_MODEL} model is pulled.",
                file=sys.stderr,
            )
            if args.json:
                print(format_json(RetrievalResult(query=args.query, retrieval_failed=str(e))))
            return 2

        if args.json:
            print(format_json(result))
        else:
            root = graph_path.resolve().parent.parent
            print(format_text(result, root=root))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
