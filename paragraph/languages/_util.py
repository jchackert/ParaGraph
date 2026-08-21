"""Shared low-level helpers for the language extractors."""
from __future__ import annotations
import re
from pathlib import Path


def _make_id(*parts: str) -> str:
    """Build a stable node ID from one or more name parts."""
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()


def _read_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _parse_source(path: Path, grammar_module: str, error_name: str):
    """Shared tree-sitter preamble for the hand-rolled extractors.

    Returns (source, root, None) on success or (None, None, error_result).
    """
    import importlib
    try:
        ts_mod = importlib.import_module(grammar_module)
        from tree_sitter import Language, Parser
    except ImportError:
        return None, None, {"nodes": [], "edges": [], "error": f"{error_name} not installed"}
    try:
        parser = Parser(Language(ts_mod.language()))
        source = path.read_bytes()
        root = parser.parse(source).root_node
    except Exception as e:
        return None, None, {"nodes": [], "edges": [], "error": str(e)}
    return source, root, None


def _make_adders(nodes: list, edges: list, seen_ids: set, str_path: str):
    """Shared add_node/add_edge closures used by the hand-rolled extractors."""
    def add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({"id": nid, "label": label, "file_type": "code",
                          "source_file": str_path, "source_location": f"L{line}"})

    def add_edge(src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", weight: float = 1.0) -> None:
        edges.append({"source": src, "target": tgt, "relation": relation,
                      "confidence": confidence, "source_file": str_path,
                      "source_location": f"L{line}", "weight": weight})
    return add_node, add_edge
