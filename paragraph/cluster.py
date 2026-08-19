"""Community detection on NetworkX graphs. Uses Leiden (graspologic) if available, falls back to Louvain (networkx). Splits oversized communities. Returns cohesion scores."""
from __future__ import annotations
import contextlib
import inspect
import io
import sys
import networkx as nx


def _suppress_output():
    """Context manager to suppress stdout/stderr during library calls.

    graspologic's leiden() emits ANSI escape sequences (progress bars,
    colored warnings) that corrupt PowerShell 5.1's scroll buffer on
    Windows (see issue #19). Redirecting stdout/stderr to devnull during
    the call prevents this without losing any graphify output.
    """
    return contextlib.redirect_stdout(io.StringIO())


def _partition(G: nx.Graph) -> dict[str, int]:
    """Run community detection. Returns {node_id: community_id}.

    Tries Leiden (graspologic) first — best quality.
    Falls back to Louvain (built into networkx) if graspologic is not installed.

    Output from graspologic is suppressed to prevent ANSI escape codes
    from corrupting terminal scroll buffers on Windows PowerShell 5.1.
    """
    try:
        from graspologic.partition import leiden
        # Suppress graspologic output to prevent ANSI escape codes from
        # corrupting PowerShell 5.1 scroll buffer (issue #19)
        old_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            with _suppress_output():
                result = leiden(G)
        finally:
            sys.stderr = old_stderr
        return result
    except ImportError:
        pass

    # Fallback: networkx louvain (available since networkx 2.7).
    # Inspect kwargs to stay compatible across NetworkX versions — max_level
    # was added in a later release and prevents hangs on large sparse graphs.
    kwargs: dict = {"seed": 42, "threshold": 1e-4}
    if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
        kwargs["max_level"] = 10
    communities = nx.community.louvain_communities(G, **kwargs)
    return {node: cid for cid, nodes in enumerate(communities) for node in nodes}


_MAX_COMMUNITY_FRACTION = 0.25   # communities larger than 25% of graph get split
_MIN_SPLIT_SIZE = 10             # only split if community has at least this many nodes


def cluster(G: nx.Graph) -> dict[int, list[str]]:
    """Run Leiden community detection. Returns {community_id: [node_ids]}.

    Community IDs are stable across runs: 0 = largest community after splitting.
    Oversized communities (> 25% of graph nodes, min 10) are split by running
    a second Leiden pass on the subgraph.

    Accepts directed or undirected graphs. DiGraphs are converted to undirected
    internally since Louvain/Leiden require undirected input.
    """
    orig = G  # keep the caller's graph so metadata lands on it, not on copies
    if G.number_of_nodes() == 0:
        return {}
    if G.is_directed():
        G = G.to_undirected()
    if G.number_of_edges() == 0:
        result = {i: [n] for i, n in enumerate(sorted(G.nodes))}
        orig.graph["community_labels"] = label_communities(orig, result)
        return result

    # Leiden warns and drops isolates - handle them separately
    isolates = [n for n in G.nodes() if G.degree(n) == 0]
    connected_nodes = [n for n in G.nodes() if G.degree(n) > 0]
    connected = G.subgraph(connected_nodes)

    raw: dict[int, list[str]] = {}
    if connected.number_of_nodes() > 0:
        partition = _partition(connected)
        for node, cid in partition.items():
            raw.setdefault(cid, []).append(node)

    # Each isolate becomes its own single-node community
    next_cid = max(raw.keys(), default=-1) + 1
    for node in isolates:
        raw[next_cid] = [node]
        next_cid += 1

    # Split oversized communities
    max_size = max(_MIN_SPLIT_SIZE, int(G.number_of_nodes() * _MAX_COMMUNITY_FRACTION))
    final_communities: list[list[str]] = []
    for nodes in raw.values():
        if len(nodes) > max_size:
            final_communities.extend(_split_community(G, nodes))
        else:
            final_communities.append(nodes)

    # Re-index by size descending for deterministic ordering
    final_communities.sort(key=len, reverse=True)
    result = {i: sorted(nodes) for i, nodes in enumerate(final_communities)}
    # Store deterministic labels as graph-level metadata (same convention as
    # "hyperedges") so they persist into graph.json and downstream consumers
    # (report, export) can use them without a new pipeline parameter.
    orig.graph["community_labels"] = label_communities(orig, result)
    return result


def _split_community(G: nx.Graph, nodes: list[str]) -> list[list[str]]:
    """Run a second Leiden pass on a community subgraph to split it further."""
    subgraph = G.subgraph(nodes)
    if subgraph.number_of_edges() == 0:
        # No edges - split into individual nodes
        return [[n] for n in sorted(nodes)]
    try:
        sub_partition = _partition(subgraph)
        sub_communities: dict[int, list[str]] = {}
        for node, cid in sub_partition.items():
            sub_communities.setdefault(cid, []).append(node)
        if len(sub_communities) <= 1:
            return [sorted(nodes)]
        return [sorted(v) for v in sub_communities.values()]
    except Exception:
        return [sorted(nodes)]


_LABEL_MAX_MEMBERS = 3
_LABEL_MAX_LEN = 50
_LABEL_SEPARATOR = " · "


def community_label(G: nx.Graph, nodes: list[str]) -> str:
    """Deterministic human-readable label for one community. No LLM calls.

    Joins the labels of the top members ranked by within-community degree
    (ties broken by node id, so the label is stable across runs on the same
    graph) with " · ", capped at _LABEL_MAX_LEN characters. AST file-hub and
    method-stub nodes are skipped when the community has real nodes, matching
    what the report displays.
    """
    from .analyze import _is_file_node  # local import - analyze imports are heavier

    sub = G.subgraph(nodes)
    real = [n for n in nodes if not _is_file_node(G, n)]
    ranked = sorted(real or list(nodes), key=lambda n: (-sub.degree(n), str(n)))
    parts: list[str] = []
    for n in ranked:
        raw = str(G.nodes[n].get("label", n)).strip()
        if not raw or raw in parts:
            continue
        if parts and len(_LABEL_SEPARATOR.join([*parts, raw])) > _LABEL_MAX_LEN:
            break
        parts.append(raw)
        if len(parts) >= _LABEL_MAX_MEMBERS:
            break
    label = _LABEL_SEPARATOR.join(parts)
    if len(label) > _LABEL_MAX_LEN:
        label = label[: _LABEL_MAX_LEN - 1].rstrip() + "…"
    return label


def label_communities(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, str]:
    """Deterministic labels for all communities: {community_id: label}.

    Falls back to "Community {cid}" when a community yields no usable member
    labels (e.g. all labels empty).
    """
    return {
        cid: (community_label(G, nodes) or f"Community {cid}")
        for cid, nodes in communities.items()
    }


def carry_over_labels(
    G: nx.Graph,
    communities: dict[int, list[str]],
    old_node_communities: dict[str, int],
    old_labels: dict[int, str],
    *,
    min_overlap: float = 0.5,
) -> dict[int, str]:
    """Label re-clustered communities without losing good names.

    Re-clustering renumbers community IDs, so labels keyed by ID go stale.
    Starts from deterministic member-based labels, then keeps a previous
    label (e.g. one Claude wrote during a full /paragraph run) for any new
    community whose members came at least min_overlap from a single previous
    community with a non-placeholder label.
    """
    from collections import Counter

    labels = label_communities(G, communities)
    if not old_labels or not old_node_communities:
        return labels
    for cid, members in communities.items():
        if not members:
            continue
        counts = Counter(
            old_node_communities[n] for n in members if n in old_node_communities
        )
        if not counts:
            continue
        old_cid, hits = counts.most_common(1)[0]
        if hits / len(members) < min_overlap:
            continue
        old = old_labels.get(old_cid)
        if old and not old.startswith("Community "):
            labels[cid] = old
    return labels


def cohesion_score(G: nx.Graph, community_nodes: list[str]) -> float:
    """Ratio of actual intra-community edges to maximum possible."""
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    subgraph = G.subgraph(community_nodes)
    actual = subgraph.number_of_edges()
    possible = n * (n - 1) / 2
    return round(actual / possible, 2) if possible > 0 else 0.0


def score_all(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, float]:
    return {cid: cohesion_score(G, nodes) for cid, nodes in communities.items()}
