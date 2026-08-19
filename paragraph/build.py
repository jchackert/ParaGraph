# assemble node+edge dicts into a NetworkX graph, preserving edge direction
#
# Node deduplication — three layers:
#
# 1. Within a file (AST): each extractor tracks a `seen_ids` set. A node ID is
#    emitted at most once per file, so duplicate class/function definitions in
#    the same source file are collapsed to the first occurrence.
#
# 2. Between files (build): NetworkX G.add_node() is idempotent — calling it
#    twice with the same ID overwrites the attributes with the second call's
#    values. Nodes are added in extraction order (AST first, then semantic),
#    so if the same entity is extracted by both passes the semantic node
#    silently overwrites the AST node. This is intentional: semantic nodes
#    carry richer labels and cross-file context, while AST nodes have precise
#    source_location. If you need to change the priority, reorder extractions
#    passed to build().
#
# 3. Semantic merge (skill): before calling build(), the skill merges cached
#    and new semantic results using an explicit `seen` set keyed on node["id"],
#    so duplicates across cache hits and new extractions are resolved there
#    before any graph construction happens.
#
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
import networkx as nx
from .validate import validate_extraction, VALID_FILE_TYPES, VALID_CONFIDENCES


def _normalize_id(s: str) -> str:
    """Normalize an ID string the same way extract._make_id does.

    Used to reconcile edge endpoints when the LLM generates IDs with slightly
    different punctuation or casing than the AST extractor.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    return cleaned.strip("_").lower()


def build_from_json(extraction: dict, *, directed: bool = False) -> nx.Graph:
    """Build a NetworkX graph from an extraction dict.

    directed=True produces a DiGraph that preserves edge direction (source→target).
    directed=False (default) produces an undirected Graph for backward compatibility.
    """
    # NetworkX <= 3.1 serialised edges as "links"; remap to "edges" for compatibility.
    if "edges" not in extraction and "links" in extraction:
        extraction = dict(extraction, edges=extraction["links"])

    # Canonicalize legacy node/edge schema before validation.
    for node in extraction.get("nodes", []):
        if isinstance(node, dict) and "source" in node and "source_file" not in node:
            # Count edges that reference this node so the warning is actionable (#479)
            node_id = node.get("id", "?")
            affected_edges = sum(
                1 for e in extraction.get("edges", [])
                if e.get("source") == node_id or e.get("target") == node_id
            )
            print(
                f"[paragraph] WARNING: node '{node_id}' uses field 'source' instead of "
                f"'source_file' — {affected_edges} edge(s) may be misrouted. "
                f"Rename the field to 'source_file' to silence this warning.",
                file=sys.stderr,
            )
            node["source_file"] = node.pop("source")

    # Normalize LLM field-name mismatches before validation.
    # The LLM extraction phase sometimes uses wrong field names:
    #   Node: "type" instead of "file_type", "file" instead of "source_file"
    #   Edge: "type" instead of "confidence" (or relation stuffed into "type")
    for node in extraction.get("nodes", []):
        if isinstance(node, dict):
            if "type" in node and "file_type" not in node:
                raw = node.pop("type")
                node["file_type"] = raw if raw in VALID_FILE_TYPES else "rationale"
            if "file" in node and "source_file" not in node:
                node["source_file"] = node["file"]
            props = node.get("properties")
            if isinstance(props, dict) and props.get("file") and not node.get("source_file"):
                node["source_file"] = props["file"]
            if "source_file" not in node:
                node["source_file"] = "<synthesized>"

    edge_list = extraction.get("edges") if "edges" in extraction else extraction.get("links", [])
    for edge in (edge_list or []):
        if isinstance(edge, dict):
            if "type" in edge and "confidence" not in edge:
                val = edge.pop("type")
                if val in VALID_CONFIDENCES:
                    edge["confidence"] = val
                else:
                    if "relation" not in edge or not edge["relation"]:
                        edge["relation"] = val
                    edge["confidence"] = "INFERRED"
            if "confidence" not in edge:
                edge["confidence"] = "INFERRED"
            if "source_file" not in edge:
                edge["source_file"] = None

    errors = validate_extraction(extraction)
    # Dangling edges (stdlib/external imports) are expected - only warn about real schema errors.
    real_errors = [e for e in errors if "does not match any node id" not in e]
    if real_errors:
        print(f"[paragraph] Extraction warning ({len(real_errors)} issues): {real_errors[0]}", file=sys.stderr)
    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()
    for node in extraction.get("nodes", []):
        G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    node_set = set(G.nodes())
    # Normalized ID map: lets edges survive when the LLM generates IDs with
    # slightly different casing or punctuation than the AST extractor.
    # e.g. "Session_ValidateToken" maps to "session_validatetoken".
    norm_to_id: dict[str, str] = {_normalize_id(nid): nid for nid in node_set}
    for edge in extraction.get("edges", []):
        if "source" not in edge and "from" in edge:
            edge["source"] = edge["from"]
        if "target" not in edge and "to" in edge:
            edge["target"] = edge["to"]
        if "source" not in edge or "target" not in edge:
            continue
        src, tgt = edge["source"], edge["target"]
        # Remap mismatched IDs via normalization before dropping the edge.
        if src not in node_set:
            src = norm_to_id.get(_normalize_id(src), src)
        if tgt not in node_set:
            tgt = norm_to_id.get(_normalize_id(tgt), tgt)
        if src not in node_set or tgt not in node_set:
            continue  # skip edges to external/stdlib nodes - expected, not an error
        attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
        # Preserve original edge direction - undirected graphs lose it otherwise,
        # causing display functions to show edges backwards.
        attrs["_src"] = src
        attrs["_tgt"] = tgt
        G.add_edge(src, tgt, **attrs)
    hyperedges = extraction.get("hyperedges", [])
    if hyperedges:
        G.graph["hyperedges"] = hyperedges
    return G

def _norm_label(label: str) -> str:
    """Canonical dedup key — lowercase, alphanumeric only."""
    return re.sub(r"[^a-z0-9 ]", "", label.lower()).strip()


_BARE_CALLABLE = re.compile(r"^\.?\w+\(\)$")  # main(), run(), .method()


def _dedup_key(node: dict) -> tuple[str, str] | None:
    """Dedup key for one node: normalised label, plus source_file for code.

    Code nodes (and anything labelled like a bare callable) only merge with
    duplicates from the SAME file — every Python script has a main(), and
    merging them across files invents cross-script edges that corrupt god
    nodes and surprising-connection analysis. Concept/document nodes merge
    by label alone, which is the cross-chunk dedup this function exists for.
    """
    label_key = _norm_label(str(node.get("label", node.get("id", ""))))
    if not label_key:
        return None
    ftype = node.get("file_type") or node.get("type")
    code_like = ftype == "code" or bool(_BARE_CALLABLE.match(str(node.get("label") or "")))
    if code_like:
        return (label_key, str(node.get("source_file") or node.get("file") or ""))
    return (label_key, "")


def deduplicate_by_label(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge nodes that share a normalised label, rewriting edge references.

    Prefers IDs without chunk suffixes (_c\\d+) and shorter IDs when tied.
    Drops self-loops created by the merge. Intended for semantic-extraction
    results only (the skill's chunk-merge step). Code nodes and bare
    callable labels are scoped to their source_file — see _dedup_key.
    """
    _CHUNK_SUFFIX = re.compile(r"_c\d+$")
    canonical: dict[tuple[str, str], dict] = {}  # dedup key -> surviving node
    remap: dict[str, str] = {}                   # old_id -> surviving_id

    for node in nodes:
        key = _dedup_key(node)
        if key is None:
            continue
        existing = canonical.get(key)
        if existing is None:
            canonical[key] = node
        else:
            has_suffix = bool(_CHUNK_SUFFIX.search(node["id"]))
            existing_has_suffix = bool(_CHUNK_SUFFIX.search(existing["id"]))
            if has_suffix and not existing_has_suffix:
                remap[node["id"]] = existing["id"]
            elif existing_has_suffix and not has_suffix:
                remap[existing["id"]] = node["id"]
                canonical[key] = node
            elif len(node["id"]) < len(existing["id"]):
                remap[existing["id"]] = node["id"]
                canonical[key] = node
            else:
                remap[node["id"]] = existing["id"]

    if not remap:
        return nodes, edges

    print(f"[paragraph] Deduplicated {len(remap)} duplicate node(s) by label.", file=sys.stderr)
    deduped_nodes = list(canonical.values())
    deduped_edges = []
    for edge in edges:
        e = dict(edge)
        e["source"] = remap.get(e["source"], e["source"])
        e["target"] = remap.get(e["target"], e["target"])
        if e["source"] != e["target"]:
            deduped_edges.append(e)
    return deduped_nodes, deduped_edges


def connect_orphan_chunks(graph_data: dict) -> tuple[dict, dict]:
    """Link orphaned doc/rationale chunks to a per-file parent node.

    Chunk-ingested documents (e.g. a rulings file split into 100+ chunks)
    arrive with no edges, so each chunk becomes its own community and the
    viz fills with singleton dots. This adds `part_of` edges from every
    orphaned document/rationale node to an anchor node for its source file
    (the file's highest-degree existing node, or a new file-level node), so
    a chunked file clusters as one community. Code and observation nodes are
    left alone. Returns (graph_data, stats); mutates graph_data in place.
    """
    nodes = graph_data.get("nodes", [])
    links = graph_data.setdefault("links", [])

    degree: dict[str, int] = {}
    for e in links:
        degree[e.get("source")] = degree.get(e.get("source"), 0) + 1
        degree[e.get("target")] = degree.get(e.get("target"), 0) + 1

    by_file: dict[str, list[dict]] = {}
    for n in nodes:
        sf = n.get("source_file")
        if sf and sf != "<synthesized>":
            by_file.setdefault(str(sf), []).append(n)

    linked = 0
    files_touched = 0
    created = 0
    for sf, file_nodes in by_file.items():
        orphans = [n for n in file_nodes
                   if degree.get(n["id"], 0) == 0
                   and n.get("file_type") in ("document", "rationale")]
        if not orphans:
            continue
        anchored = [n for n in file_nodes if degree.get(n["id"], 0) > 0]
        if anchored:
            anchor = max(anchored, key=lambda n: degree.get(n["id"], 0))
        else:
            anchor_id = "file_" + _normalize_id(sf)
            existing = next((n for n in nodes if n["id"] == anchor_id), None)
            if existing is None:
                anchor = {
                    "id": anchor_id,
                    "label": Path(sf).name,
                    "file_type": "document",
                    "source_file": sf,
                }
                nodes.append(anchor)
                created += 1
            else:
                anchor = existing
        for n in orphans:
            if n["id"] == anchor["id"]:
                continue
            links.append({
                "source": n["id"],
                "target": anchor["id"],
                "relation": "part_of",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": sf,
                "weight": 1.0,
                "_src": n["id"],
                "_tgt": anchor["id"],
            })
            linked += 1
        files_touched += 1

    return graph_data, {"files": files_touched, "linked": linked, "file_nodes_created": created}
