# Swift coding-standards advisor — a detector registry over graph.json.
#
# HARD SCOPE RULE: every detector is gated on is_swift_node(). Python tooling
# scripts, docs, observations, and any other non-Swift node in the same graph
# must never produce a finding, no matter how large or connected they are.
from __future__ import annotations

import json
import sys
from pathlib import Path

from paragraph.layers import classify_layer, is_layer_violation, is_swift_node, layer_map

STANDARDS_PACKAGE = "paragraph.standards"
STANDARDS_FILE = "swift.json"

# detector name -> rule id in the standards pack. Registry order is report order.
DETECTOR_RULES: dict[str, str] = {
    "massive_type": "SWIFT-SRP-01",
    "massive_view_model": "SWIFT-MVVM-01",
    "layering_violation": "SWIFT-ARCH-01",
    "view_skips_viewmodel": "SWIFT-MVVM-02",
    "singleton_fanin": "SWIFT-DI-01",
    "god_object": "SWIFT-SRP-02",
    "force_operations": "SWIFT-ERR-01",
    "main_queue_in_observable": "SWIFT-CONC-01",
}

MASSIVE_TYPE_THRESHOLD = 20          # outgoing contains/method edges
MASSIVE_VIEW_MODEL_THRESHOLD = 15    # method edges on a viewmodel-layer node
SINGLETON_FANIN_THRESHOLD = 10       # in-degree on a .shared/Singleton node
GOD_OBJECT_THRESHOLD = 40            # total degree
GOD_OBJECT_TOP_N = 5
FORCE_OPERATIONS_THRESHOLD = 3       # try! + as! occurrences in source_body
LAYERING_VIOLATION_CAP = 25          # max violation edges reported

_MEMBER_RELATIONS = ("contains", "method", "has_method")
_CALL_RELATIONS = ("calls", "uses", "call", "use")

_REQUIRED_RULE_FIELDS = ("id", "title", "statement", "rationale", "applies_to",
                         "snippet_bad", "snippet_good", "citations")
_REQUIRED_CITATION_FIELDS = ("title", "source", "url")


# ---------------------------------------------------------------- standards

def load_standards() -> dict[str, dict]:
    """Load and validate the Swift standards pack. Returns rule_id -> rule.

    Reads via importlib.resources so it works from wheels/zips, with a
    plain-file fallback for editable installs. Raises ValueError when the
    pack is malformed or a detector's rule id is missing.
    """
    raw: str | None = None
    try:
        from importlib import resources
        try:
            raw = (resources.files(STANDARDS_PACKAGE) / STANDARDS_FILE).read_text(encoding="utf-8")
        except Exception:
            raw = (resources.files("paragraph") / "standards" / STANDARDS_FILE).read_text(encoding="utf-8")
    except Exception:
        raw = None
    if raw is None:
        raw = (Path(__file__).parent / "standards" / STANDARDS_FILE).read_text(encoding="utf-8")

    rules = json.loads(raw).get("rules", [])
    standards: dict[str, dict] = {}
    for rule in rules:
        rid = rule.get("id", "<missing id>")
        for field in _REQUIRED_RULE_FIELDS:
            if not rule.get(field):
                raise ValueError(f"standards rule {rid}: missing required field '{field}'")
        if not isinstance(rule["citations"], list) or len(rule["citations"]) < 1:
            raise ValueError(f"standards rule {rid}: needs at least one citation")
        for cit in rule["citations"]:
            for field in _REQUIRED_CITATION_FIELDS:
                if not cit.get(field):
                    raise ValueError(f"standards rule {rid}: citation missing '{field}'")
        applies = rule["applies_to"]
        if applies != "general" and applies not in DETECTOR_RULES:
            raise ValueError(f"standards rule {rid}: unknown applies_to '{applies}'")
        if rid in standards:
            raise ValueError(f"standards pack: duplicate rule id {rid}")
        standards[rid] = rule

    for detector, rid in DETECTOR_RULES.items():
        if rid not in standards:
            raise ValueError(f"detector '{detector}' maps to rule {rid} which is not in the pack")
    return standards


# ---------------------------------------------------------------- detectors

def _endpoints(link: dict) -> tuple[str | None, str | None]:
    """Directed endpoints of an edge — _src/_tgt preserve direction on
    undirected graphs; fall back to source/target."""
    return (link.get("_src") or link.get("source"),
            link.get("_tgt") or link.get("target"))


def _is_code_node(node: dict) -> bool:
    return node.get("file_type") in (None, "code")


def run_detectors(graph: dict) -> tuple[list[dict], dict]:
    """Run every detector over a raw graph.json dict.

    Returns (findings, stats). Each finding:
      {"rule_id", "detector", "confidence", "evidence", "nodes": [ids], "source_file"}
    Stats include swift_nodes, excluded_non_swift_code, and per-detector counts.
    """
    nodes = graph.get("nodes", [])
    links = graph.get("links", graph.get("edges", []))
    by_id = {n["id"]: n for n in nodes if "id" in n}
    swift_ids = {nid for nid, n in by_id.items() if is_swift_node(n)}
    excluded_non_swift_code = sum(
        1 for nid, n in by_id.items() if _is_code_node(n) and nid not in swift_ids
    )
    layers = layer_map(nodes)

    # One pass over the edges to build degree tables (directed via _src/_tgt).
    member_out: dict[str, int] = {}   # outgoing contains/method edges
    in_degree: dict[str, int] = {}
    total_degree: dict[str, int] = {}
    for link in links:
        src, tgt = _endpoints(link)
        if not src or not tgt:
            continue
        rel = str(link.get("relation") or "").lower()
        if rel in _MEMBER_RELATIONS:
            member_out[src] = member_out.get(src, 0) + 1
        in_degree[tgt] = in_degree.get(tgt, 0) + 1
        total_degree[src] = total_degree.get(src, 0) + 1
        total_degree[tgt] = total_degree.get(tgt, 0) + 1

    def label(nid: str) -> str:
        return str(by_id.get(nid, {}).get("label") or nid)

    def source_file(nid: str) -> str:
        return str(by_id.get(nid, {}).get("source_file") or "")

    findings: list[dict] = []
    counts: dict[str, int] = {name: 0 for name in DETECTOR_RULES}

    def add(detector: str, confidence: str, evidence: str,
            node_ids: list[str], sf: str) -> None:
        counts[detector] += 1
        findings.append({
            "rule_id": DETECTOR_RULES[detector],
            "detector": detector,
            "confidence": confidence,
            "evidence": evidence,
            "nodes": node_ids,
            "source_file": sf,
        })

    swift_sorted = sorted(swift_ids)

    # massive_type — Swift node with > 20 outgoing contains/method edges
    for nid in swift_sorted:
        n_members = member_out.get(nid, 0)
        if n_members > MASSIVE_TYPE_THRESHOLD:
            add("massive_type", "EXTRACTED",
                f"'{label(nid)}' contains {n_members} members "
                f"(contains/method edges) — over the {MASSIVE_TYPE_THRESHOLD}-member threshold.",
                [nid], source_file(nid))

    # massive_view_model — viewmodel-layer node with > 15 method edges
    for nid in swift_sorted:
        n_methods = member_out.get(nid, 0)
        if layers.get(nid) == "viewmodel" and n_methods > MASSIVE_VIEW_MODEL_THRESHOLD:
            add("massive_view_model", "EXTRACTED",
                f"View model '{label(nid)}' has {n_methods} method edges — "
                f"over the {MASSIVE_VIEW_MODEL_THRESHOLD}-method threshold for view models.",
                [nid], source_file(nid))

    # layering_violation — directed edge pointing up the Swift stack
    violations: list[tuple[str, str, str]] = []
    for link in links:
        src, tgt = _endpoints(link)
        if not src or not tgt or src not in swift_ids or tgt not in swift_ids:
            continue
        if is_layer_violation(layers.get(src, "other"), layers.get(tgt, "other")):
            violations.append((src, tgt, str(link.get("relation") or "depends_on")))
    violations.sort()
    overflow = max(0, len(violations) - LAYERING_VIOLATION_CAP)
    for src, tgt, rel in violations[:LAYERING_VIOLATION_CAP]:
        add("layering_violation", "EXTRACTED",
            f"'{label(src)}' ({layers[src]}) depends on '{label(tgt)}' ({layers[tgt]}) "
            f"via '{rel}' — dependency points UP the layer stack.",
            [src, tgt], source_file(src))

    # view_skips_viewmodel — view calls/uses a service directly
    seen_pairs: set[tuple[str, str]] = set()
    for link in links:
        src, tgt = _endpoints(link)
        if not src or not tgt or src not in swift_ids or tgt not in swift_ids:
            continue
        rel = str(link.get("relation") or "").lower()
        if rel not in _CALL_RELATIONS:
            continue
        if layers.get(src) == "view" and layers.get(tgt) == "service" \
                and (src, tgt) not in seen_pairs:
            seen_pairs.add((src, tgt))
            add("view_skips_viewmodel", "EXTRACTED",
                f"View '{label(src)}' calls service '{label(tgt)}' directly, "
                f"bypassing the viewmodel layer.",
                [src, tgt], source_file(src))

    # singleton_fanin — .shared / Singleton node with in-degree > 10
    for nid in swift_sorted:
        lbl = label(nid)
        fan_in = in_degree.get(nid, 0)
        if (lbl.endswith(".shared") or "Singleton" in lbl) and fan_in > SINGLETON_FANIN_THRESHOLD:
            add("singleton_fanin", "EXTRACTED",
                f"Singleton '{lbl}' is referenced by {fan_in} incoming edges — "
                f"over the fan-in threshold of {SINGLETON_FANIN_THRESHOLD}.",
                [nid], source_file(nid))

    # god_object — Swift nodes with total degree > 40 (top 5)
    gods = sorted(
        ((total_degree.get(nid, 0), nid) for nid in swift_ids
         if total_degree.get(nid, 0) > GOD_OBJECT_THRESHOLD),
        key=lambda t: (-t[0], t[1]),
    )[:GOD_OBJECT_TOP_N]
    for degree, nid in gods:
        add("god_object", "EXTRACTED",
            f"'{label(nid)}' touches {degree} edges — a god object "
            f"(threshold {GOD_OBJECT_THRESHOLD}, top {GOD_OBJECT_TOP_N} reported).",
            [nid], source_file(nid))

    # force_operations — INFERRED, needs source_body
    for nid in swift_sorted:
        body = by_id[nid].get("source_body")
        if not body:
            continue
        n_try = body.count("try!")
        n_as = body.count("as!")
        if n_try + n_as >= FORCE_OPERATIONS_THRESHOLD:
            add("force_operations", "INFERRED",
                f"'{label(nid)}' uses {n_try} 'try!' and {n_as} 'as!' force "
                f"operation(s) in its source body — each is a potential crash.",
                [nid], source_file(nid))

    # main_queue_in_observable — INFERRED, needs source_body
    for nid in swift_sorted:
        body = by_id[nid].get("source_body")
        if not body:
            continue
        if ("ObservableObject" in body or "@Observable" in body) \
                and "DispatchQueue.main" in body:
            add("main_queue_in_observable", "INFERRED",
                f"Observable type '{label(nid)}' hops to DispatchQueue.main by hand — "
                f"annotate it @MainActor for compiler-checked isolation instead.",
                [nid], source_file(nid))

    stats = {
        "total_nodes": len(nodes),
        "swift_nodes": len(swift_ids),
        "excluded_non_swift_code": excluded_non_swift_code,
        "detector_counts": counts,
        "layering_violation_overflow": overflow,
        "total_findings": len(findings),
    }
    return findings, stats


# ---------------------------------------------------------------- rendering

def advice_markdown(findings: list[dict], stats: dict, standards: dict[str, dict]) -> str:
    """Render findings + standards pack into ADVICE.md content."""
    lines: list[str] = []
    lines.append("# Swift Coding-Standards Advice")
    lines.append("")
    lines.append("**Scope: Swift code only.** Every detector is gated on Swift source nodes "
                 "(`.swift` files). Python scripts and other non-Swift tooling code in this "
                 "corpus were excluded from analysis and can never produce a finding.")
    lines.append("")
    lines.append(f"- Swift code nodes analyzed: {stats.get('swift_nodes', 0)}")
    lines.append(f"- Non-Swift code nodes excluded from analysis: "
                 f"{stats.get('excluded_non_swift_code', 0)}")
    lines.append(f"- Findings: {len(findings)}")
    lines.append("")

    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["rule_id"], []).append(f)

    overflow = stats.get("layering_violation_overflow", 0)
    layering_rule = DETECTOR_RULES["layering_violation"]

    for rid in sorted(by_rule):
        rule = standards.get(rid)
        if rule is None:
            continue
        rule_findings = by_rule[rid]
        lines.append(f"## {rid} — {rule['title']}")
        lines.append("")
        lines.append(f"Findings ({len(rule_findings)}):")
        for f in rule_findings:
            lines.append(f"- [{f['confidence']}] {f['evidence']}")
        if rid == layering_rule and overflow:
            lines.append(f"- …and {overflow} more layering violation(s) not listed "
                         f"(report capped at {LAYERING_VIOLATION_CAP} edges).")
        lines.append("")
        lines.append(rule["statement"])
        lines.append("")
        lines.append(f"*Why it matters:* {rule['rationale']}")
        lines.append("")
        lines.append("**Avoid:**")
        lines.append("```swift")
        lines.append(rule["snippet_bad"])
        lines.append("```")
        lines.append("")
        lines.append("**Prefer:**")
        lines.append("```swift")
        lines.append(rule["snippet_good"])
        lines.append("```")
        lines.append("")
        lines.append("Sources:")
        for cit in rule["citations"]:
            lines.append(f"- {cit['title']} — {cit['source']} ({cit['url']})")
        lines.append("")

    lines.append("## Clean")
    lines.append("")
    clean = [rid for rid in sorted(standards) if rid not in by_rule]
    if clean:
        lines.append("Rules from the pack with no findings in this graph:")
        for rid in clean:
            lines.append(f"- {rid} — {standards[rid]['title']}")
    else:
        lines.append("Every rule in the pack produced at least one finding.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI entry

def run(target: Path) -> int:
    """CLI body for `paragraph advise [path]` — returns an exit code."""
    graph_json = target / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        return 1
    try:
        graph = json.loads(graph_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: graph.json is corrupted ({exc}). Re-run /paragraph to rebuild.",
              file=sys.stderr)
        return 1
    standards = load_standards()
    findings, stats = run_detectors(graph)
    md = advice_markdown(findings, stats, standards)
    out_path = target / "graphify-out" / "ADVICE.md"
    out_path.write_text(md, encoding="utf-8")

    triggered = {f["rule_id"] for f in findings}
    print(f"Swift advice: {len(findings)} finding(s) across {len(triggered)} rule(s) "
          f"({stats['swift_nodes']} Swift nodes analyzed)")
    for detector, rid in DETECTOR_RULES.items():
        count = stats["detector_counts"].get(detector, 0)
        if count:
            print(f"  {rid}  {detector}: {count}")
    if stats.get("layering_violation_overflow"):
        print(f"  (+{stats['layering_violation_overflow']} layering violation(s) over the "
              f"{LAYERING_VIOLATION_CAP}-edge report cap)")
    print(f"Excluded non-Swift code nodes (tooling): {stats['excluded_non_swift_code']}")
    print(f"Written to {out_path}")
    return 0
