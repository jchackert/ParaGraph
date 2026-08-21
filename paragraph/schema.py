# the graph.json data contract — single source of truth
#
# Every pipeline step reads and writes graph.json, and for most of this
# project's life the schema was enforced by convention, separately, in each
# module: edges carry "source"/"target" (extraction schema) and/or
# "_src"/"_tgt" (direction preserved through NetworkX round-trips), the edge
# list is "links" in NetworkX exports but "edges" in extraction results, and
# every consumer re-implemented the fallbacks. Centralizing the contract here
# retires that bug class. New code should use these helpers instead of
# reaching into edge dicts directly.

from __future__ import annotations

from collections import Counter

# ── Node contract ─────────────────────────────────────────────────────────────
# Required: id, label, file_type, source_file, source_location
# Optional: community, norm_label, source_body, captured_at, last_touched_at,
#           source_url, author, contributor, chunk_index, total_chunks

VALID_FILE_TYPES = {"code", "document", "paper", "image", "rationale", "observation"}

# ── Edge contract ─────────────────────────────────────────────────────────────
# Required: source, target (extraction schema) and/or _src, _tgt (export
#           schema — preserves direction through undirected NetworkX graphs),
#           relation, confidence
# Optional: confidence_score, weight, source_file, source_location, origin

VALID_CONFIDENCES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}

# Relations the AST extractors emit (EXTRACTED unless noted).
STRUCTURAL_RELATIONS = frozenset({
    "contains",            # file -> class/function
    "method",              # class -> method
    "case_of",             # enum -> case
    "imports",             # file -> module
    "imports_from",        # file -> module (from-import)
    "inherits",            # class -> base/protocol
    "extends",             # class -> superclass (Java)
    "implements",          # class -> interface
    "calls",               # caller -> callee (cross-file: INFERRED)
    "references",          # type annotation / metatype use (INFERRED)
    "uses",                # cross-file import resolution (INFERRED)
    "uses_config",         # helper-call config key reference
    "references_constant", # class-constant access
    "uses_static_prop",    # static property access
    "bound_to",            # DI container binding
    "listened_by",         # event -> listener
})

# Relations the LLM semantic pass may emit — this set MUST stay in sync with
# the relation enum in skill.md's output-schema line (a test enforces it).
SEMANTIC_RELATIONS = frozenset({
    "calls", "implements", "references", "cites", "conceptually_related_to",
    "shares_data_with", "semantically_similar_to", "rationale_for",
})

# Relations written by deterministic pipeline passes.
PIPELINE_RELATIONS = frozenset({
    "part_of",                  # connect-chunks: chunk -> per-file parent
    "conceptually_related_to",  # link: doc/rationale -> similar code
    "investigated",             # ingest-claude-mem: observation -> code
    "modified",                 # ingest-claude-mem: observation -> code
})

HYPEREDGE_RELATIONS = frozenset({"participate_in", "implement", "form"})

KNOWN_RELATIONS = STRUCTURAL_RELATIONS | SEMANTIC_RELATIONS | PIPELINE_RELATIONS

# Origin tag for edges owned by `paragraph link` (replaced wholesale per run).
LINK_EDGE_ORIGIN = "paragraph-link"


# ── Accessors ─────────────────────────────────────────────────────────────────

def edge_endpoints(edge: dict) -> tuple[str | None, str | None]:
    """(source, target) regardless of which key style the edge carries."""
    return (edge.get("source", edge.get("_src")),
            edge.get("target", edge.get("_tgt")))


def set_edge_endpoints(edge: dict, src: str, tgt: str) -> None:
    """Rewrite both endpoint key styles that are present on the edge."""
    if "source" in edge or "_src" not in edge:
        edge["source"] = src
        edge["target"] = tgt
    if "_src" in edge or "_tgt" in edge:
        edge["_src"] = src
        edge["_tgt"] = tgt


def edge_list_key(graph_data: dict) -> str:
    """The key holding the edge list: 'links' (export) or 'edges' (extraction)."""
    if "links" in graph_data or "edges" not in graph_data:
        return "links"
    return "edges"


def edge_list(graph_data: dict) -> list[dict]:
    return graph_data.get("links", graph_data.get("edges", []))


def unknown_relations(edges: list[dict]) -> Counter:
    """Count relations outside the known vocabulary (for drift warnings)."""
    counts: Counter = Counter()
    for e in edges:
        rel = e.get("relation")
        if rel and rel not in KNOWN_RELATIONS:
            counts[rel] += 1
    return counts
