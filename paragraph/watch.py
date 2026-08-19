# monitor a folder and auto-trigger --update when files change
from __future__ import annotations
import json
import sys
import time
from pathlib import Path


from paragraph.detect import CODE_EXTENSIONS, DOC_EXTENSIONS, PAPER_EXTENSIONS, IMAGE_EXTENSIONS

_WATCHED_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | PAPER_EXTENSIONS | IMAGE_EXTENSIONS
_CODE_EXTENSIONS = CODE_EXTENSIONS


def _report_root_label(watch_path: Path) -> str:
    """Delegates to report.root_label so every writer of GRAPH_REPORT.md agrees."""
    from paragraph.report import root_label
    return root_label(watch_path)


def _relativize_source_files(payload: dict, root: Path) -> None:
    for bucket in ("nodes", "edges", "hyperedges"):
        for item in payload.get(bucket, []):
            source = item.get("source_file")
            if not source:
                continue
            source_path = Path(source)
            if not source_path.is_absolute():
                continue
            try:
                item["source_file"] = str(source_path.resolve().relative_to(root))
            except ValueError:
                continue


def _rebuild_code(watch_path: Path, *, follow_symlinks: bool = False) -> bool:
    """Re-run AST extraction + build + cluster + report for code files. No LLM needed.

    Returns True on success, False on error.
    """
    watch_root = watch_path.resolve()
    project_root = Path.cwd().resolve() if not watch_path.is_absolute() else watch_root
    report_root = _report_root_label(watch_path)
    try:
        from paragraph.extract import extract
        from paragraph.detect import detect
        from paragraph.build import build_from_json
        from paragraph.cluster import cluster, score_all, carry_over_labels
        from paragraph.analyze import god_nodes, surprising_connections, suggest_questions
        from paragraph.report import (generate, freshness_report,
                                      stable_mode_default, FRESHNESS_FILENAME)
        from paragraph.export import to_json, to_html_auto

        detected = detect(watch_path, follow_symlinks=follow_symlinks)
        code_files = [Path(f) for f in detected['files']['code']]

        if not code_files:
            print("[paragraph watch] No code files found - nothing to rebuild.")
            return False

        result = extract(code_files, cache_root=watch_root)

        # Preserve semantic nodes/edges from a previous full run.
        # AST-only rebuild replaces code nodes; doc/paper/image nodes are kept.
        out = watch_path / "graphify-out"
        existing_graph = out / "graph.json"
        old_labels: dict[int, str] = {}
        old_node_communities: dict[str, int] = {}
        if existing_graph.exists():
            try:
                existing = json.loads(existing_graph.read_text(encoding="utf-8"))
                old_labels = {
                    int(k): v
                    for k, v in (existing.get("graph", {}).get("community_labels") or {}).items()
                    if str(k).lstrip("-").isdigit()
                }
                old_node_communities = {
                    n["id"]: n["community"] for n in existing.get("nodes", [])
                    if n.get("community") is not None
                }
                code_ids = {n["id"] for n in existing.get("nodes", []) if n.get("file_type") == "code"}
                sem_nodes = [n for n in existing.get("nodes", []) if n.get("file_type") != "code"]
                sem_edges = [e for e in existing.get("links", existing.get("edges", []))
                             if e.get("confidence") in ("INFERRED", "AMBIGUOUS")
                             or (e.get("source") not in code_ids and e.get("target") not in code_ids)]
                result = {
                    "nodes": result["nodes"] + sem_nodes,
                    "edges": result["edges"] + sem_edges,
                    "hyperedges": existing.get("hyperedges", []),
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            except Exception:
                pass  # corrupt graph.json - proceed with AST-only

        _relativize_source_files(result, project_root)

        detection = {
            "files": {"code": [str(f) for f in code_files], "document": [], "paper": [], "image": []},
            "total_files": len(code_files),
            "total_words": detected.get("total_words", 0),
        }

        G = build_from_json(result)
        communities = cluster(G)
        cohesion = score_all(G, communities)
        gods = god_nodes(G)
        surprises = surprising_connections(G, communities)
        # Deterministic member-based labels, upgraded with labels carried over
        # from the previous graph.json (including Claude-written ones).
        labels = carry_over_labels(G, communities, old_node_communities, old_labels)
        questions = suggest_questions(G, communities, labels)

        out.mkdir(exist_ok=True)

        report = generate(G, communities, cohesion, labels, gods, surprises, detection,
                          {"input": 0, "output": 0}, report_root, suggested_questions=questions,
                          out_dir=out)
        (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
        if stable_mode_default(out, report_root):
            (out / FRESHNESS_FILENAME).write_text(
                freshness_report(detection, report_root, out_dir=out), encoding="utf-8")
        # force=True: code-only rebuilds preserve semantic nodes internally
        # (lines 62-82) but produce fewer total nodes than the enriched graph
        # due to code-node ID churn from AST re-extraction. The safety check
        # in to_json would refuse to write, causing silent rebuild failures.
        to_json(G, communities, str(out / "graph.json"), community_labels=labels, force=True)
        from paragraph.insights import record_history
        record_history(out, G, communities)

        # Graphs over MAX_NODES_FOR_VIZ get an aggregated community-level
        # graph.html instead of none; only a single oversized community skips.
        viz = to_html_auto(G, communities, str(out / "graph.html"), community_labels=labels or None)
        html_written = viz != "skipped"
        if viz == "aggregated":
            print(f"[paragraph watch] graph.html rendered as aggregated community view "
                  f"({G.number_of_nodes()} nodes exceed the full-viz limit); "
                  f"double-click a community for its full subgraph")
        elif viz == "skipped":
            print("[paragraph watch] Skipped graph.html: graph too large for full viz "
                  "and community structure cannot be aggregated into a useful view")

        # clear stale needs_update flag if present
        flag = out / "needs_update"
        if flag.exists():
            flag.unlink()

        print(f"[paragraph watch] Rebuilt: {G.number_of_nodes()} nodes, "
              f"{G.number_of_edges()} edges, {len(communities)} communities")
        products = "graph.json" + (", graph.html" if html_written else "") + " and GRAPH_REPORT.md"
        print(f"[paragraph watch] {products} updated in {out}")
        return True

    except Exception as exc:
        print(f"[paragraph watch] Rebuild failed: {exc}")
        return False


def check_update(watch_path: Path) -> bool:
    """Check for pending semantic update flag and notify the user if set.

    Cron-safe: always returns True so cron jobs do not alarm.
    Non-code file changes (docs, papers, images) require LLM-backed
    re-extraction via `/paragraph --update` — this function only signals
    that the update is needed.
    """
    flag = Path(watch_path) / "graphify-out" / "needs_update"
    if flag.exists():
        print(f"[paragraph check-update] Pending non-code changes in {watch_path}.")
        print("[paragraph check-update] Run `/paragraph --update` to apply semantic re-extraction.")
    return True


def _notify_only(watch_path: Path) -> None:
    """Write a flag file and print a notification (fallback for non-code-only corpora)."""
    flag = watch_path / "graphify-out" / "needs_update"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    print(f"\n[paragraph watch] New or changed files detected in {watch_path}")
    print("[paragraph watch] Non-code files changed - semantic re-extraction requires LLM.")
    print("[paragraph watch] Run `/paragraph --update` in Claude Code to update the graph.")
    print(f"[paragraph watch] Flag written to {flag}")


def _has_non_code(changed_paths: list[Path]) -> bool:
    return any(p.suffix.lower() not in _CODE_EXTENSIONS for p in changed_paths)


def watch(watch_path: Path, debounce: float = 3.0) -> None:
    """
    Watch watch_path for new or modified files and auto-update the graph.

    For code-only changes: re-runs AST extraction + rebuild immediately (no LLM).
    For doc/paper/image changes: writes a needs_update flag and notifies the user
    to run /paragraph --update (LLM extraction required).

    debounce: seconds to wait after the last change before triggering (avoids
    running on every keystroke when many files are saved at once).
    """
    try:
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver
        from watchdog.events import FileSystemEventHandler
    except ImportError as e:
        raise ImportError("watchdog not installed. Run: pip install watchdog") from e

    last_trigger: float = 0.0
    pending: bool = False
    changed: set[Path] = set()

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            nonlocal last_trigger, pending
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() not in _WATCHED_EXTENSIONS:
                return
            if any(part.startswith(".") for part in path.parts):
                return
            if "graphify-out" in path.parts:
                return
            last_trigger = time.monotonic()
            pending = True
            changed.add(path)

    handler = Handler()
    # Use polling observer on macOS — FSEvents can miss rapid saves in some editors
    observer = PollingObserver() if sys.platform == "darwin" else Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()

    print(f"[paragraph watch] Watching {watch_path.resolve()} - press Ctrl+C to stop")
    print(f"[paragraph watch] Code changes rebuild graph automatically. "
          f"Doc/image changes require /paragraph --update.")
    print(f"[paragraph watch] Debounce: {debounce}s")

    try:
        while True:
            time.sleep(0.5)
            if pending and (time.monotonic() - last_trigger) >= debounce:
                pending = False
                batch = list(changed)
                changed.clear()
                print(f"\n[paragraph watch] {len(batch)} file(s) changed")
                if _has_non_code(batch):
                    _notify_only(watch_path)
                else:
                    _rebuild_code(watch_path)
    except KeyboardInterrupt:
        print("\n[paragraph watch] Stopped.")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Watch a folder and auto-update the graphify graph")
    parser.add_argument("path", nargs="?", default=".", help="Folder to watch (default: .)")
    parser.add_argument("--debounce", type=float, default=3.0,
                        help="Seconds to wait after last change before updating (default: 3)")
    args = parser.parse_args()
    watch(Path(args.path), debounce=args.debounce)
