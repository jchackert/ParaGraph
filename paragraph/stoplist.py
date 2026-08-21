# generic-symbol stoplist + shadow-node resolution
#
# The AST extractors synthesize a bare node (empty source_file) whenever a
# class inherits from / conforms to a type not defined in the same file.
# Those bare nodes fall into two classes:
#
#   1. Generic language/framework symbols (Sendable, View, String, Exception).
#      Every file's conformance collapses into one global god node that glues
#      unrelated communities together and carries no architectural signal.
#      These are dropped.
#
#   2. Shadows of real project types (class Foo: BaseService, where
#      BaseService lives in another file). The real node is stem-qualified
#      (baseservice_baseservice) so the bare shadow (baseservice) duplicates
#      it and the inheritance edge lands on the duplicate. These are merged
#      into the real node, recovering the cross-file edge.
#
# resolve_shadow_nodes() applies both, in that order (merge first, so a
# project type that happens to share a name with a stoplisted symbol is
# merged, not dropped). It runs at the end of extract() for new extractions
# and is exposed as `paragraph prune-generic` for existing graph.json files.

from __future__ import annotations

from .schema import edge_endpoints, set_edge_endpoints, edge_list, edge_list_key

# Case-insensitive. Swift stdlib/SwiftUI/Foundation protocols and types that
# appear as conformance targets, plus Python/Java/C# equivalents. A name is
# only ever suppressed when the project does NOT define a type of the same
# name — shadow-merge runs first and wins.
DEFAULT_STOPLIST: frozenset[str] = frozenset(s.lower() for s in {
    # Swift stdlib protocols
    "Sendable", "Equatable", "Hashable", "Comparable", "Codable",
    "Encodable", "Decodable", "CaseIterable", "Identifiable",
    "RawRepresentable", "CustomStringConvertible",
    "CustomDebugStringConvertible", "ExpressibleByStringLiteral",
    "ExpressibleByIntegerLiteral", "ExpressibleByArrayLiteral",
    "ExpressibleByDictionaryLiteral", "OptionSet", "Sequence",
    "Collection", "IteratorProtocol", "AnyObject", "Any",
    # Swift stdlib / Foundation types
    "String", "Int", "Int8", "Int16", "Int32", "Int64", "UInt",
    "Double", "Float", "CGFloat", "Bool", "Character", "Substring",
    "Array", "Dictionary", "Set", "Optional", "Result", "Data",
    "Date", "URL", "UUID", "TimeInterval", "IndexSet", "Void",
    "Error", "LocalizedError", "CustomNSError", "NSObject",
    "NSError", "Notification", "Calendar", "Locale", "TimeZone",
    # Swift concurrency
    "Actor", "MainActor", "AsyncSequence", "AsyncStream",
    # SwiftUI / UIKit / AppKit conformance targets
    "View", "ViewModifier", "App", "Scene", "Shape", "InsettableShape",
    "PreviewProvider", "DynamicProperty", "EnvironmentKey",
    "PreferenceKey", "Animatable", "Transferable", "ObservableObject",
    "Observable", "UIViewController", "UIView", "UIViewRepresentable",
    "UIViewControllerRepresentable", "NSViewRepresentable",
    "NSViewControllerRepresentable", "UIApplicationDelegate",
    "NSApplicationDelegate", "AppIntent", "WidgetBundle", "Widget",
    "TimelineProvider", "XCTestCase",
    # Python builtins
    "str", "int", "float", "bool", "bytes", "list", "dict", "tuple",
    "set", "frozenset", "object", "type", "Exception", "BaseException",
    "ValueError", "TypeError", "KeyError", "RuntimeError", "OSError",
    "IOError", "AttributeError", "NotImplementedError", "StopIteration",
    "Enum", "IntEnum", "StrEnum", "ABC", "Protocol", "NamedTuple",
    "TypedDict", "dataclass",
    # Java / C# / common
    "Object", "Serializable", "Cloneable", "Runnable", "Comparable",
    "IDisposable", "IEnumerable", "IEquatable", "EventArgs",
})


def is_stoplisted(label: str, extra: frozenset[str] | set[str] = frozenset()) -> bool:
    """True if `label` is a generic symbol that should not become a node."""
    key = label.strip().strip("()").lstrip(".").lower()
    return key in DEFAULT_STOPLIST or key in extra


def resolve_shadow_nodes(
    nodes: list[dict],
    edges: list[dict],
    *,
    extra_stoplist: frozenset[str] | set[str] = frozenset(),
    keep: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[dict], list[dict], dict]:
    """Merge shadow nodes into their real definitions, drop stoplisted ones.

    A shadow node is a synthesized node with an empty/missing source_file.
    - If its label is stoplisted, the shadow and its edges are dropped — even
      when the project has a real node with that label. Swift projects extend
      framework types (`extension View { ... }` produces a real node labeled
      View), and merging every `: View` conformance into that extension node
      would recreate the god node the stoplist exists to remove.
    - Else, if a real node (non-empty source_file, file_type code) shares its
      label (case-insensitive), the shadow is removed and its edges are
      remapped to the real node.
    - Else it is kept (external symbol worth tracking, e.g. a third-party type).

    `keep` exempts labels from the stoplist (restoring merge for a project
    that genuinely owns a type with a stoplisted name). Returns
    (nodes, edges, stats). Deterministic: when several real nodes share a
    label, the first in node order wins.
    """
    keep_keys = {k.lower() for k in keep}

    real_by_label: dict[str, str] = {}
    for n in nodes:
        if n.get("source_file") and n.get("file_type", "code") == "code":
            key = str(n.get("label", "")).strip().strip("()").lstrip(".").lower()
            if key and key not in real_by_label:
                real_by_label[key] = n["id"]

    remap: dict[str, str] = {}   # shadow id -> real id
    drop: set[str] = set()       # shadow ids to delete outright
    for n in nodes:
        if n.get("source_file"):
            continue
        label = str(n.get("label", ""))
        key = label.strip().strip("()").lstrip(".").lower()
        if not key:
            continue
        if key not in keep_keys and is_stoplisted(label, extra_stoplist):
            drop.add(n["id"])
            continue
        real = real_by_label.get(key)
        if real and real != n["id"]:
            remap[n["id"]] = real

    if not remap and not drop:
        return nodes, edges, {"merged": 0, "dropped": 0, "edges_removed": 0, "edges_remapped": 0}

    new_nodes = [n for n in nodes if n["id"] not in remap and n["id"] not in drop]

    new_edges: list[dict] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    edges_removed = 0
    edges_remapped = 0
    for e in edges:
        src, tgt = edge_endpoints(e)
        if src in drop or tgt in drop:
            edges_removed += 1
            continue
        mapped = False
        if src in remap:
            src = remap[src]; mapped = True
        if tgt in remap:
            tgt = remap[tgt]; mapped = True
        if src == tgt:  # merge produced a self-loop
            edges_removed += 1
            continue
        pair = (src, tgt, e.get("relation", ""))
        if pair in seen_pairs:
            edges_removed += 1
            continue
        seen_pairs.add(pair)
        if mapped:
            edges_remapped += 1
            e = dict(e)
            set_edge_endpoints(e, src, tgt)
        new_edges.append(e)

    stats = {
        "merged": len(remap),
        "dropped": len(drop),
        "edges_removed": edges_removed,
        "edges_remapped": edges_remapped,
    }
    return new_nodes, new_edges, stats


def prune_generic(graph_data: dict, *,
                  extra_stoplist: frozenset[str] | set[str] = frozenset(),
                  keep: frozenset[str] | set[str] = frozenset()) -> tuple[dict, dict]:
    """Apply resolve_shadow_nodes to a loaded graph.json dict (links schema)."""
    nodes = graph_data.get("nodes", [])
    edges = edge_list(graph_data)
    new_nodes, new_edges, stats = resolve_shadow_nodes(
        nodes, edges, extra_stoplist=extra_stoplist, keep=keep)
    out = dict(graph_data)
    out["nodes"] = new_nodes
    out[edge_list_key(graph_data)] = new_edges
    return out, stats
