# one-command code-only rebuild — the canonical pipeline order
#
# Before this module, the step ordering and its safety rules (prune before
# connect-chunks? enrich before link? re-cluster when?) lived in consumer
# shell scripts as comments, and every project re-derived them. This is the
# authoritative order:
#
#   1. update          AST re-extraction (no LLM), carries over semantic nodes
#   2. ingest-claude-mem  observation nodes from claude-mem.db (optional)
#   3. connect-chunks  orphaned doc/rationale chunks -> per-file parents
#   4. prune-generic   shadow-node merge + generic-symbol drop
#   5. recluster       communities, report, graph.json, graph.html
#   6. enrich          source bodies + timestamps + vectors.db (needs ollama)
#   7. link            embedding doc<->code bridges (needs vectors.db)
#   8. recluster       so communities and the viz reflect the link edges
#
# Steps 2, 6, 7 are skipped gracefully when their prerequisites are missing;
# everything else is fatal. A full semantic (LLM) rebuild is still the
# /paragraph skill's job — this command never spends tokens.
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_CLAUDE_MEM_DB = Path.home() / ".claude-mem" / "claude-mem.db"


def recluster(watch_path: Path) -> int:
    """Re-cluster an existing graph.json and regenerate report/json/html.

    This is the body of `paragraph cluster-only`, shared so the rebuild
    pipeline and the standalone command cannot drift apart.
    """
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        return 1
    from paragraph.build import build_from_json
    from paragraph.cluster import cluster, score_all, carry_over_labels
    from paragraph.analyze import god_nodes, surprising_connections, suggest_questions
    from paragraph.report import (generate, freshness_report, root_label,
                                  stable_mode_default, FRESHNESS_FILENAME)
    from paragraph.export import to_json, to_html_auto
    print("Loading existing graph...")
    _raw = json.loads(graph_json.read_text(encoding="utf-8"))
    G = build_from_json(_raw)
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print("Re-clustering...")
    communities = cluster(G)
    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    old_labels = {
        int(k): v
        for k, v in (_raw.get("graph", {}).get("community_labels") or {}).items()
        if str(k).lstrip("-").isdigit()
    }
    old_node_communities = {
        n["id"]: n["community"] for n in _raw.get("nodes", [])
        if n.get("community") is not None
    }
    labels = carry_over_labels(G, communities, old_node_communities, old_labels)
    questions = suggest_questions(G, communities, labels)
    tokens = {"input": 0, "output": 0}
    report = generate(G, communities, cohesion, labels, gods, surprises,
                      {"warning": "cluster-only mode — file stats not available"},
                      tokens, root_label(watch_path), suggested_questions=questions)
    out = watch_path / "graphify-out"
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    if stable_mode_default(out, str(watch_path)):
        (out / FRESHNESS_FILENAME).write_text(
            freshness_report(
                {"warning": "cluster-only mode — file stats not available"},
                root_label(watch_path), out_dir=out),
            encoding="utf-8")
    to_json(G, communities, str(out / "graph.json"), community_labels=labels)
    from paragraph.insights import record_history
    record_history(out, G, communities)
    viz = to_html_auto(G, communities, str(out / "graph.html"), community_labels=labels or None)
    if viz == "aggregated":
        print("Graph too large for full viz — graph.html shows the aggregated community view.")
    html_part = " graph.json and graph.html" if viz != "skipped" else " and graph.json"
    print(f"Done — {len(communities)} communities. GRAPH_REPORT.md,{html_part} updated.")
    return 0


def _ollama_reachable() -> bool:
    import urllib.request
    from paragraph.enrich import OLLAMA_URL
    base = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/tags"
    try:
        with urllib.request.urlopen(base, timeout=3):
            return True
    except Exception:
        return False


def run(watch_path: Path, *,
        db: Path | None = None,
        project: str | None = None,
        skip_ingest: bool = False,
        skip_enrich: bool = False,
        skip_link: bool = False,
        link_threshold: float | None = None,
        link_top_k: int | None = None) -> int:
    """Run the full code-only rebuild pipeline. Returns 0 on success."""
    watch_path = Path(watch_path)
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first for the "
              f"initial build; rebuild only refreshes an existing graph.", file=sys.stderr)
        return 1

    print("=== Step 1/8: update (AST re-extraction) ===")
    from paragraph.watch import _rebuild_code
    if not _rebuild_code(watch_path):
        print("error: AST rebuild failed — aborting before any further step.", file=sys.stderr)
        return 1

    print("\n=== Step 2/8: ingest-claude-mem ===")
    mem_db = db or DEFAULT_CLAUDE_MEM_DB
    if skip_ingest:
        print("skipped (--skip-ingest)")
    elif not mem_db.exists():
        print(f"skipped — no claude-mem DB at {mem_db}")
    else:
        from paragraph.ingest_claudemem import run as ingest_run
        rc = ingest_run(watch_path, db_path=mem_db, project=project)
        if rc != 0:
            # The ingest guard refuses 0-match wipes with rc=1; the graph is
            # intact either way, so the rebuild continues.
            print("ingest-claude-mem failed — continuing; existing observation "
                  "nodes are untouched.", file=sys.stderr)

    print("\n=== Step 3/8: connect-chunks ===")
    from paragraph.build import connect_orphan_chunks
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    data, stats = connect_orphan_chunks(data)
    if stats["linked"]:
        graph_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Linked {stats['linked']} orphaned chunk(s) across {stats['files']} file(s).")
    else:
        print("No orphaned document/rationale chunks found.")

    print("\n=== Step 4/8: prune-generic ===")
    from paragraph.stoplist import prune_generic
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    data, pstats = prune_generic(data)
    if pstats["merged"] or pstats["dropped"]:
        graph_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Merged {pstats['merged']} shadow node(s), dropped {pstats['dropped']} generic symbol(s).")
    else:
        print("Nothing to prune.")

    print("\n=== Step 5/8: recluster ===")
    if recluster(watch_path) != 0:
        return 1

    print("\n=== Step 6/8: enrich ===")
    ran_enrich = False
    if skip_enrich:
        print("skipped (--skip-enrich)")
    elif not _ollama_reachable():
        print("skipped — ollama not reachable; retrieval/link will lag until the next run.")
    else:
        from paragraph.enrich import run as enrich_run
        if enrich_run(watch_path) == 0:
            ran_enrich = True
        else:
            print("enrich failed — graph is intact; continuing without fresh embeddings.",
                  file=sys.stderr)

    print("\n=== Step 7/8: link ===")
    vectors_db = watch_path / "graphify-out" / "vectors.db"
    if skip_link:
        print("skipped (--skip-link)")
    elif not vectors_db.exists():
        print("skipped — no vectors.db (enrich must succeed at least once first).")
    else:
        from paragraph import link as link_mod
        kwargs = {}
        if link_threshold is not None:
            kwargs["threshold"] = link_threshold
        if link_top_k is not None:
            kwargs["top_k"] = link_top_k
        if link_mod.run(watch_path, **kwargs) == 0:
            print("\n=== Step 8/8: recluster (post-link) ===")
            if recluster(watch_path) != 0:
                return 1
        else:
            print("link failed — graph is intact; doc<->code bridges will lag "
                  "until the next run.", file=sys.stderr)

    if not ran_enrich and not skip_enrich:
        print("\nNote: embeddings were not refreshed this run.")
    print("\n==> rebuild complete.")
    return 0
