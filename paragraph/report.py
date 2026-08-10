# generate GRAPH_REPORT.md - the human-readable audit trail
from __future__ import annotations
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
import networkx as nx

_BARE_LABEL = re.compile(r"^Community \d+$")

FRESHNESS_FILENAME = "GRAPH_FRESHNESS.md"

_TRUTHY = {"1", "true", "True", "yes", "on"}


def stable_mode_default() -> bool:
    """Whether to omit wall-clock-varying fields from GRAPH_REPORT.md.

    Off by default. Set PARAGRAPH_STABLE_REPORT=1 to turn it on.

    Why this exists: the report's generation date, corpus file/word counts, and
    "source files changed since" counters move on every run whether or not the
    graph changed. For a consumer that commits GRAPH_REPORT.md to git, that means
    a rebuild can never produce a no-op diff, so "the file is dirty" stops meaning
    "something happened" and the signal is lost. In stable mode those fields move
    to a sidecar (see `freshness_report`) which the consumer can gitignore; they
    are relocated, never dropped.
    """
    return os.environ.get("PARAGRAPH_STABLE_REPORT", "") in _TRUTHY


def _auto_labels(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, str]:
    """Deterministic member-based labels for communities.

    Prefers the labels cluster() stored on G.graph["community_labels"]
    (graph.json round-trips them with string keys - normalize back to int).
    Falls back to computing them fresh, so graphs loaded from JSON built by
    older versions still get labels.
    """
    stored = G.graph.get("community_labels")
    if isinstance(stored, dict) and stored:
        out: dict[int, str] = {}
        for k, v in stored.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    from .cluster import label_communities
    return label_communities(G, communities)


def _resolve_labels(
    G: nx.Graph,
    communities: dict[int, list[str]],
    community_labels: dict[int, str],
) -> tuple[dict[int, str], dict[int, str]]:
    """Return (pretty, display) label dicts for every community.

    Caller-provided labels win when they are real names (e.g. LLM-generated).
    Bare "Community {N}" defaults are upgraded to the deterministic
    member-based label. `pretty` is the name alone (for headings that already
    show the id); `display` keeps the numeric id visible for stable reference:
    "Community 12 — SubscriptionService · CaptureViewModel".
    """
    auto = _auto_labels(G, communities)
    pretty: dict[int, str] = {}
    display: dict[int, str] = {}
    for cid in communities:
        provided = community_labels.get(cid, f"Community {cid}")
        if provided and not _BARE_LABEL.match(provided):
            p = provided
        else:
            a = auto.get(cid, "")
            p = a if a and not _BARE_LABEL.match(a) else (provided or f"Community {cid}")
        pretty[cid] = p
        display[cid] = p if p == provided else f"Community {cid} — {p}"
    return pretty, display


def _freshness_lines(root: str, out_dir: str | Path | None = None) -> list[str]:
    """Render the Extraction Freshness section.

    The only durable record of the last full semantic (LLM) extraction is
    graphify-out/manifest.json - it is written solely by full runs and
    --update runs (skill.md step 9), never by code-only watch rebuilds. Its
    mtime is therefore an honest "last full semantic extraction" timestamp.
    Never fabricates: with no manifest we say so explicitly.
    """
    lines = ["", "## Extraction Freshness"]
    out = Path(out_dir) if out_dir else None
    if out is None:
        candidate = Path(root) / "graphify-out"
        if candidate.is_dir():
            out = candidate
        elif Path("graphify-out").is_dir():
            out = Path("graphify-out")
    manifest_path = (out / "manifest.json") if out is not None else None
    if manifest_path is None or not manifest_path.is_file():
        lines.append("- Last full semantic extraction: unknown — no record")
        return lines

    when = datetime.fromtimestamp(manifest_path.stat().st_mtime).date().isoformat()
    lines.append(f"- Last full semantic extraction: {when} (manifest.json last written)")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        manifest = {}
    if isinstance(manifest, dict) and manifest:
        base = out.parent
        modified = deleted = 0
        for f, stored_mtime in manifest.items():
            p = Path(f)
            if not p.is_absolute():
                p = base / f
            try:
                current = p.stat().st_mtime
            except OSError:
                deleted += 1
                continue
            if isinstance(stored_mtime, (int, float)) and current > stored_mtime + 1e-6:
                modified += 1
        lines.append(
            f"- Source files changed since: {modified} modified · {deleted} deleted"
            f" (of {len(manifest)} tracked; files added since are not in the manifest)"
        )
    if (out / "needs_update").exists():
        lines.append(
            "- needs_update flag is set — semantic re-extraction pending"
            " (run `/paragraph --update`)"
        )
    return lines


def freshness_report(
    detection_result: dict,
    root: str,
    out_dir: str | Path | None = None,
) -> str:
    """The wall-clock-varying half of the report, as a standalone sidecar document.

    Holds exactly what stable mode removes from GRAPH_REPORT.md: generation date,
    corpus file/word counts, and extraction-freshness counters. Callers write this
    to `FRESHNESS_FILENAME` so the information stays available (it is the "is my
    graph stale" signal) without making the committed report churn.
    """
    lines = [
        f"# Graph Freshness - {root}  ({date.today().isoformat()})",
        "",
        "> Regenerated every run; these values track wall-clock state, not graph",
        "> structure. Kept out of GRAPH_REPORT.md so that report only changes when",
        "> the graph does.",
        "",
        "## Corpus Check",
    ]
    if detection_result.get("warning"):
        lines.append(f"- {detection_result['warning']}")
    else:
        lines.append(
            f"- {detection_result['total_files']} files"
            f" · ~{detection_result['total_words']:,} words"
        )
    lines += _freshness_lines(root, out_dir)
    return "\n".join(lines) + "\n"


def _safe_community_name(label: str) -> str:
    """Mirrors export.safe_name so community hub filenames and report wikilinks always agree."""
    cleaned = re.sub(r'[\\/*?:"<>|#^[\]]', "", label.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")).strip()
    cleaned = re.sub(r"\.(md|mdx|markdown)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned or "unnamed"


def generate(
    G: nx.Graph,
    communities: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    community_labels: dict[int, str],
    god_node_list: list[dict],
    surprise_list: list[dict],
    detection_result: dict,
    token_cost: dict,
    root: str,
    suggested_questions: list[dict] | None = None,
    out_dir: str | Path | None = None,
    stable: bool | None = None,
) -> str:
    """Render GRAPH_REPORT.md.

    `stable` omits every wall-clock-varying field so the output changes only when
    the graph does; the omitted values move to the `freshness_report` sidecar.
    None (the default) defers to PARAGRAPH_STABLE_REPORT, which is off unless set,
    so existing callers keep their current output byte for byte.
    """
    if stable is None:
        stable = stable_mode_default()
    today = date.today().isoformat()

    confidences = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
    total = len(confidences) or 1
    ext_pct = round(confidences.count("EXTRACTED") / total * 100)
    inf_pct = round(confidences.count("INFERRED") / total * 100)
    amb_pct = round(confidences.count("AMBIGUOUS") / total * 100)

    inf_edges = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("confidence") == "INFERRED"]
    inf_scores = [d.get("confidence_score", 0.5) for _, _, d in inf_edges]
    inf_avg = round(sum(inf_scores) / len(inf_scores), 2) if inf_scores else None

    lines = [
        f"# Graph Report - {root}" if stable else f"# Graph Report - {root}  ({today})",
        "",
        "## Corpus Check",
    ]
    if detection_result.get("warning"):
        lines.append(f"- {detection_result['warning']}")
    elif stable:
        # File and word counts drift on every run; they live in the sidecar now.
        lines.append("- Verdict: corpus is large enough that graph structure adds value.")
    else:
        lines += [
            f"- {detection_result['total_files']} files · ~{detection_result['total_words']:,} words",
            "- Verdict: corpus is large enough that graph structure adds value.",
        ]

    if stable:
        lines += ["", f"- Corpus and extraction-freshness stats: see `{FRESHNESS_FILENAME}`."]
    else:
        lines += _freshness_lines(root, out_dir)

    from .analyze import _is_file_node as _ifn
    non_empty = {cid: nodes for cid, nodes in communities.items()
                 if any(not _ifn(G, n) for n in nodes)}

    pretty_labels, display_labels = _resolve_labels(G, communities, community_labels)

    lines += [
        "",
        "## Summary",
        f"- {G.number_of_nodes()} nodes · {G.number_of_edges()} edges · {len(non_empty)} communities detected",
        f"- Extraction: {ext_pct}% EXTRACTED · {inf_pct}% INFERRED · {amb_pct}% AMBIGUOUS"
        + (f" · INFERRED: {len(inf_edges)} edges (avg confidence: {inf_avg})" if inf_avg is not None else ""),
        f"- Token cost: {token_cost.get('input', 0):,} input · {token_cost.get('output', 0):,} output",
    ]

    # Community hub navigation - links to _COMMUNITY_*.md files in the Obsidian vault.
    # Without these, GRAPH_REPORT.md is a dead-end and the vault splits into disconnected components.
    if non_empty:
        lines += ["", "## Community Hubs (Navigation)"]
        for cid in non_empty:
            # Hub filenames are derived from the caller-provided labels in
            # export - keep the link target on those, prettify only the alias.
            label = community_labels.get(cid, f"Community {cid}")
            safe = _safe_community_name(label)
            lines.append(f"- [[_COMMUNITY_{safe}|{display_labels.get(cid, label)}]]")

    lines += [
        "",
        "## God Nodes (most connected - your core abstractions)",
    ]
    for i, node in enumerate(god_node_list, 1):
        lines.append(f"{i}. `{node['label']}` - {node['degree']} edges")

    lines += ["", "## Surprising Connections (you probably didn't know these)"]
    if surprise_list:
        for s in surprise_list:
            relation = s.get("relation", "related_to")
            note = s.get("note", "")
            files = s.get("source_files", ["", ""])
            conf = s.get("confidence", "EXTRACTED")
            cscore = s.get("confidence_score")
            if conf == "INFERRED" and cscore is not None:
                conf_tag = f"INFERRED {cscore:.2f}"
            else:
                conf_tag = conf
            sem_tag = " [semantically similar]" if relation == "semantically_similar_to" else ""
            lines += [
                f"- `{s['source']}` --{relation}--> `{s['target']}`  [{conf_tag}]{sem_tag}",
                f"  {files[0]} → {files[1]}" + (f"  _{note}_" if note else ""),
            ]
    else:
        lines.append("- None detected - all connections are within the same source files.")

    hyperedges = G.graph.get("hyperedges", [])
    if hyperedges:
        lines += ["", "## Hyperedges (group relationships)"]
        for h in hyperedges:
            node_labels = ", ".join(h.get("nodes", []))
            conf = h.get("confidence", "INFERRED")
            cscore = h.get("confidence_score")
            conf_tag = f"{conf} {cscore:.2f}" if cscore is not None else conf
            lines.append(f"- **{h.get('label', h.get('id', ''))}** — {node_labels} [{conf_tag}]")

    lines += ["", "## Communities"]
    for cid, nodes in communities.items():
        label = pretty_labels.get(cid, f"Community {cid}")
        score = cohesion_scores.get(cid, 0.0)
        # Filter method/function stubs from display - they're structural noise
        real_nodes = [n for n in nodes if not _ifn(G, n)]
        if not real_nodes:
            continue
        display = [G.nodes[n].get("label", n) for n in real_nodes[:8]]
        suffix = f" (+{len(real_nodes)-8} more)" if len(real_nodes) > 8 else ""
        lines += [
            "",
            f"### Community {cid} - \"{label}\"",
            f"Cohesion: {score}",
            f"Nodes ({len(real_nodes)}): {', '.join(display)}{suffix}",
        ]

    ambiguous = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("confidence") == "AMBIGUOUS"]
    if ambiguous:
        lines += ["", "## Ambiguous Edges - Review These"]
        for u, v, d in ambiguous:
            ul = G.nodes[u].get("label", u)
            vl = G.nodes[v].get("label", v)
            lines += [
                f"- `{ul}` → `{vl}`  [AMBIGUOUS]",
                f"  {d.get('source_file', '')} · relation: {d.get('relation', 'unknown')}",
            ]

    # --- Gaps section ---
    from .analyze import _is_file_node, _is_concept_node

    isolated = [
        n for n in G.nodes()
        if G.degree(n) <= 1 and not _is_file_node(G, n) and not _is_concept_node(G, n)
    ]
    thin_communities = {
        cid: nodes for cid, nodes in communities.items()
        if 0 < sum(1 for n in nodes if not _is_file_node(G, n)) < 3
    }
    gap_count = len(isolated) + len(thin_communities)

    if gap_count > 0 or amb_pct > 20:
        lines += ["", "## Knowledge Gaps"]
        if isolated:
            isolated_labels = [G.nodes[n].get("label", n) for n in isolated[:5]]
            suffix = f" (+{len(isolated)-5} more)" if len(isolated) > 5 else ""
            lines.append(f"- **{len(isolated)} isolated node(s):** {', '.join(f'`{l}`' for l in isolated_labels)}{suffix}")
            lines.append("  These have ≤1 connection - possible missing edges or undocumented components.")
        if thin_communities:
            for cid, nodes in thin_communities.items():
                label = display_labels.get(cid, f"Community {cid}")
                node_labels = [G.nodes[n].get("label", n) for n in nodes]
                lines.append(f"- **Thin community `{label}`** ({len(nodes)} nodes): {', '.join(f'`{l}`' for l in node_labels)}")
                lines.append("  Too small to be a meaningful cluster - may be noise or needs more connections extracted.")
        if amb_pct > 20:
            lines.append(f"- **High ambiguity: {amb_pct}% of edges are AMBIGUOUS.** Review the Ambiguous Edges section above.")

    if suggested_questions:
        lines += ["", "## Suggested Questions"]
        no_signal = len(suggested_questions) == 1 and suggested_questions[0].get("type") == "no_signal"
        if no_signal:
            lines.append(f"_{suggested_questions[0]['why']}_")
        else:
            lines.append("_Questions this graph is uniquely positioned to answer:_")
            lines.append("")
            for q in suggested_questions:
                if q.get("question"):
                    lines.append(f"- **{q['question']}**")
                    lines.append(f"  _{q['why']}_")

    return "\n".join(lines)
