"""paragraph CLI - `paragraph install` sets up the Claude Code skill."""
from __future__ import annotations
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("paragraph")
except Exception:
    __version__ = "unknown"


def _check_skill_version(skill_dst: Path) -> None:
    """Warn if the installed skill is from an older paragraph version."""
    version_file = skill_dst.parent / ".paragraph_version"
    if not version_file.exists():
        return
    installed = version_file.read_text(encoding="utf-8").strip()
    if installed != __version__:
        print(f"  warning: skill is from paragraph {installed}, package is {__version__}. Run 'paragraph install' to update.")


def _refresh_all_version_stamps() -> None:
    """Update .paragraph_version in the Claude skill dir after a successful install."""
    skill_dst = Path.home() / _PLATFORM_CONFIG["claude"]["skill_dst"]
    vf = skill_dst.parent / ".paragraph_version"
    if vf.exists():
        vf.write_text(__version__, encoding="utf-8")

_SETTINGS_HOOK = {
    "matcher": "Glob|Grep",
    "hooks": [
        {
            "type": "command",
            "command": (
                "[ -f graphify-out/graph.json ] && "
                r"""echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"graphify: Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md for god nodes and community structure before searching raw files."}}' """
                "|| true"
            ),
        }
    ],
}

_SKILL_REGISTRATION = (
    "\n# paragraph\n"
    "- **paragraph** (`~/.claude/skills/paragraph/SKILL.md`) "
    "- any input to knowledge graph. Trigger: `/paragraph`\n"
    "When the user types `/paragraph`, invoke the Skill tool "
    "with `skill: \"paragraph\"` before doing anything else.\n"
)


_PLATFORM_CONFIG: dict[str, dict] = {
    "claude": {
        "skill_file": "skill.md",
        "skill_dst": Path(".claude") / "skills" / "paragraph" / "SKILL.md",
        "claude_md": True,
    },
}


def install() -> None:
    cfg = _PLATFORM_CONFIG["claude"]
    skill_src = Path(__file__).parent / cfg["skill_file"]
    if not skill_src.exists():
        print(f"error: {cfg['skill_file']} not found in package - reinstall paragraph", file=sys.stderr)
        sys.exit(1)

    import os as _os
    if _os.environ.get("CLAUDE_CONFIG_DIR"):
        _claude_base = Path(_os.environ["CLAUDE_CONFIG_DIR"])
        skill_dst = _claude_base / "skills" / "paragraph" / "SKILL.md"
    else:
        skill_dst = Path.home() / cfg["skill_dst"]
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(skill_src, skill_dst)
    (skill_dst.parent / ".paragraph_version").write_text(__version__, encoding="utf-8")
    print(f"  skill installed  ->  {skill_dst}")

    # Register in ~/.claude/CLAUDE.md
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "paragraph" in content:
            print(f"  CLAUDE.md        ->  already registered (no change)")
        else:
            claude_md.write_text(content.rstrip() + _SKILL_REGISTRATION, encoding="utf-8")
            print(f"  CLAUDE.md        ->  skill registered in {claude_md}")
    else:
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(_SKILL_REGISTRATION.lstrip(), encoding="utf-8")
        print(f"  CLAUDE.md        ->  created at {claude_md}")

    print()
    print("Done. Open Claude Code and type:")
    print()
    print("  /paragraph .")
    print()


_CLAUDE_MD_SECTION = """\
## paragraph

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- For cross-module "how does X relate to Y" questions, prefer `paragraph query "<question>"`, `paragraph path "<A>" "<B>"`, or `paragraph explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- For architecture reviews, run `paragraph analyze .` — community summaries, hubs/bridges/orphans, and cross-community dependency cycles land in graphify-out/GRAPH_INSIGHTS.md
- After modifying code files in this session, run `paragraph update .` to keep the graph current (AST-only, no API cost)
"""

_CLAUDE_MD_MARKER = "## paragraph"

def claude_install(project_dir: Path | None = None) -> None:
    """Write the paragraph section to the local CLAUDE.md."""
    target = (project_dir or Path(".")) / "CLAUDE.md"

    if target.exists():
        content = target.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER in content:
            print("paragraph already configured in CLAUDE.md")
            return
        new_content = content.rstrip() + "\n\n" + _CLAUDE_MD_SECTION
    else:
        new_content = _CLAUDE_MD_SECTION

    target.write_text(new_content, encoding="utf-8")
    print(f"paragraph section written to {target.resolve()}")

    # Also write Claude Code PreToolUse hook to .claude/settings.json
    _install_claude_hook(project_dir or Path("."))

    print()
    print("Claude Code will now check the knowledge graph before answering")
    print("codebase questions and rebuild it after code changes.")


def _is_paragraph_hook(hook: dict) -> bool:
    """True if a settings.json PreToolUse entry is ours.

    Matches on either 'paragraph' or 'graphify' in the payload — the hook
    command text still says 'graphify' (legacy name), and settings written
    by older versions must also be recognised for dedup and uninstall.
    """
    return hook.get("matcher") == "Glob|Grep" and (
        "paragraph" in str(hook) or "graphify" in str(hook)
    )


def _install_claude_hook(project_dir: Path) -> None:
    """Add paragraph PreToolUse hook to .claude/settings.json."""
    settings_path = project_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    pre_tool = hooks.setdefault("PreToolUse", [])

    hooks["PreToolUse"] = [h for h in pre_tool if not _is_paragraph_hook(h)]
    hooks["PreToolUse"].append(_SETTINGS_HOOK)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  .claude/settings.json  ->  PreToolUse hook registered")


def _uninstall_claude_hook(project_dir: Path) -> None:
    """Remove paragraph PreToolUse hook from .claude/settings.json."""
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    pre_tool = settings.get("hooks", {}).get("PreToolUse", [])
    filtered = [h for h in pre_tool if not _is_paragraph_hook(h)]
    if len(filtered) == len(pre_tool):
        return
    settings["hooks"]["PreToolUse"] = filtered
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  .claude/settings.json  ->  PreToolUse hook removed")


def claude_uninstall(project_dir: Path | None = None) -> None:
    """Remove the paragraph section from the local CLAUDE.md."""
    target = (project_dir or Path(".")) / "CLAUDE.md"

    if not target.exists():
        print("No CLAUDE.md found in current directory - nothing to do")
        return

    content = target.read_text(encoding="utf-8")
    if _CLAUDE_MD_MARKER not in content:
        print("paragraph section not found in CLAUDE.md - nothing to do")
        return

    # Remove the ## paragraph section: from the marker to the next ## heading or EOF
    cleaned = re.sub(
        r"\n*## paragraph\n.*?(?=\n## |\Z)",
        "",
        content,
        flags=re.DOTALL,
    ).rstrip()
    if cleaned:
        target.write_text(cleaned + "\n", encoding="utf-8")
        print(f"paragraph section removed from {target.resolve()}")
    else:
        target.unlink()
        print(f"CLAUDE.md was empty after removal - deleted {target.resolve()}")

    _uninstall_claude_hook(project_dir or Path("."))


def _clone_repo(url: str, branch: str | None = None, out_dir: Path | None = None) -> Path:
    """Clone a GitHub repo to a local cache dir and return the path.

    Clones into ~/.paragraph/repos/<owner>/<repo> by default so repeated
    runs on the same URL reuse the existing clone (git pull instead of clone).
    Falls back to a pre-rename ~/.graphify/repos clone if one already exists.
    """
    import subprocess as _sp
    import re as _re

    # Normalise URL — strip trailing .git if present
    url = url.rstrip("/")
    if not url.endswith(".git"):
        git_url = url + ".git"
    else:
        git_url = url
        url = url[:-4]

    # Extract owner/repo from URL
    m = _re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        print(f"error: not a recognised GitHub URL: {url}", file=sys.stderr)
        sys.exit(1)
    owner, repo = m.group(1), m.group(2)

    if out_dir:
        dest = out_dir
    else:
        dest = Path.home() / ".paragraph" / "repos" / owner / repo
        legacy = Path.home() / ".graphify" / "repos" / owner / repo
        if not dest.exists() and legacy.exists():
            dest = legacy

    if dest.exists():
        print(f"Repo already cloned at {dest} — pulling latest...", flush=True)
        cmd = ["git", "-C", str(dest), "pull"]
        if branch:
            cmd += ["origin", branch]
        result = _sp.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"warning: git pull failed:\n{result.stderr}", file=sys.stderr)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning {url} → {dest} ...", flush=True)
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [git_url, str(dest)]
        result = _sp.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"error: git clone failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

    print(f"Ready at: {dest}", flush=True)
    return dest


# ---------------------------------------------------------------------------
# Command handlers (one per subcommand; imports stay lazy inside each handler)
# ---------------------------------------------------------------------------

def _cmd_install(ns: argparse.Namespace) -> None:
    install()


def _cmd_claude(ns: argparse.Namespace) -> None:
    if ns.action == "install":
        claude_install()
    else:
        claude_uninstall()


def _cmd_hook(ns: argparse.Namespace) -> None:
    from paragraph.hooks import install as hook_install, uninstall as hook_uninstall, status as hook_status
    if ns.action == "install":
        print(hook_install(Path(".")))
    elif ns.action == "uninstall":
        print(hook_uninstall(Path(".")))
    else:
        print(hook_status(Path(".")))


def _cmd_query(ns: argparse.Namespace) -> None:
    from paragraph.serve import _score_nodes, _bfs, _dfs, _subgraph_to_text
    from paragraph.security import sanitize_label
    from networkx.readwrite import json_graph
    question = ns.question
    use_dfs = ns.dfs
    budget = ns.budget
    graph_path = ns.graph
    gp = Path(graph_path).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        sys.exit(1)
    if not gp.suffix == ".json":
        print(f"error: graph file must be a .json file", file=sys.stderr)
        sys.exit(1)
    try:
        import json as _json
        import networkx as _nx
        _raw = _json.loads(gp.read_text(encoding="utf-8"))
        try:
            G = json_graph.node_link_graph(_raw, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(_raw)
    except Exception as exc:
        print(f"error: could not load graph: {exc}", file=sys.stderr)
        sys.exit(1)
    terms = [t.lower() for t in question.split() if len(t) > 2]
    scored = _score_nodes(G, terms)
    if not scored:
        print("No matching nodes found.")
        sys.exit(0)
    start = [nid for _, nid in scored[:5]]
    nodes, edges = (_dfs if use_dfs else _bfs)(G, start, depth=2)
    print(_subgraph_to_text(G, nodes, edges, token_budget=budget))


def _cmd_retrieve(ns: argparse.Namespace) -> None:
    from paragraph.retrieve import main as _retrieve_main
    sys.exit(_retrieve_main(list(ns.args)))


def _cmd_save_result(ns: argparse.Namespace) -> None:
    from paragraph.ingest import save_query_result as _sqr
    out = _sqr(
        question=ns.question,
        answer=ns.answer,
        memory_dir=Path(ns.memory_dir),
        query_type=ns.query_type,
        source_nodes=ns.nodes or None,
    )
    print(f"Saved to {out}")


def _cmd_path(ns: argparse.Namespace) -> None:
    from paragraph.serve import _score_nodes
    from networkx.readwrite import json_graph
    import networkx as _nx
    source_label = ns.source
    target_label = ns.target
    graph_path = ns.graph
    gp = Path(graph_path).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        sys.exit(1)
    _raw = json.loads(gp.read_text(encoding="utf-8"))
    try:
        G = json_graph.node_link_graph(_raw, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(_raw)
    src_scored = _score_nodes(G, [t.lower() for t in source_label.split()])
    tgt_scored = _score_nodes(G, [t.lower() for t in target_label.split()])
    if not src_scored:
        print(f"No node matching '{source_label}' found.", file=sys.stderr)
        sys.exit(1)
    if not tgt_scored:
        print(f"No node matching '{target_label}' found.", file=sys.stderr)
        sys.exit(1)
    src_nid, tgt_nid = src_scored[0][1], tgt_scored[0][1]
    try:
        path_nodes = _nx.shortest_path(G, src_nid, tgt_nid)
    except (_nx.NetworkXNoPath, _nx.NodeNotFound):
        print(f"No path found between '{source_label}' and '{target_label}'.")
        sys.exit(0)
    hops = len(path_nodes) - 1
    segments = []
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edata = G.edges[u, v]
        rel = edata.get("relation", "")
        conf = edata.get("confidence", "")
        conf_str = f" [{conf}]" if conf else ""
        if i == 0:
            segments.append(G.nodes[u].get("label", u))
        segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
    print(f"Shortest path ({hops} hops):\n  " + " ".join(segments))


def _cmd_explain(ns: argparse.Namespace) -> None:
    from paragraph.serve import _find_node
    from networkx.readwrite import json_graph
    label = ns.node
    graph_path = ns.graph
    gp = Path(graph_path).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        sys.exit(1)
    _raw = json.loads(gp.read_text(encoding="utf-8"))
    try:
        G = json_graph.node_link_graph(_raw, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(_raw)
    matches = _find_node(G, label)
    if not matches:
        print(f"No node matching '{label}' found.")
        sys.exit(0)
    nid = matches[0]
    d = G.nodes[nid]
    print(f"Node: {d.get('label', nid)}")
    print(f"  ID:        {nid}")
    print(f"  Source:    {d.get('source_file', '')} {d.get('source_location', '')}".rstrip())
    print(f"  Type:      {d.get('file_type', '')}")
    print(f"  Community: {d.get('community', '')}")
    print(f"  Degree:    {G.degree(nid)}")
    neighbors = list(G.neighbors(nid))
    if neighbors:
        print(f"\nConnections ({len(neighbors)}):")
        for nb in sorted(neighbors, key=lambda n: G.degree(n), reverse=True)[:20]:
            edata = G.edges[nid, nb]
            rel = edata.get("relation", "")
            conf = edata.get("confidence", "")
            print(f"  --> {G.nodes[nb].get('label', nb)} [{rel}] [{conf}]")
        if len(neighbors) > 20:
            print(f"  ... and {len(neighbors) - 20} more")


def _cmd_add(ns: argparse.Namespace) -> None:
    from paragraph.ingest import ingest as _ingest
    try:
        saved = _ingest(ns.url, ns.dir, author=ns.author, contributor=ns.contributor)
        print(f"Saved to {saved}")
        print("Run /paragraph --update in your AI assistant to update the graph.")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_watch(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    if not watch_path.exists():
        print(f"error: path not found: {watch_path}", file=sys.stderr)
        sys.exit(1)
    from paragraph.watch import watch as _watch
    try:
        _watch(watch_path)
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_analyze(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        sys.exit(1)
    from networkx.readwrite import json_graph as _jg
    from paragraph.cluster import score_all
    from paragraph.insights import insights_markdown, load_history, record_history
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    try:
        G = _jg.node_link_graph(data, edges="links")
    except TypeError:
        G = _jg.node_link_graph(data)
    communities: dict[int, list[str]] = {}
    for node in data.get("nodes", []):
        cid = node.get("community")
        if cid is not None:
            communities.setdefault(int(cid), []).append(node["id"])
    if not communities:
        print("error: graph.json has no community assignments — run cluster-only first", file=sys.stderr)
        sys.exit(1)
    labels = {
        int(k): v
        for k, v in (data.get("graph", {}).get("community_labels") or {}).items()
        if str(k).lstrip("-").isdigit()
    }
    cohesion = score_all(G, communities)
    out_dir = watch_path / "graphify-out"
    record_history(out_dir, G, communities)
    md = insights_markdown(G, communities, cohesion, labels or None,
                           history=load_history(out_dir))
    out_path = watch_path / "graphify-out" / "GRAPH_INSIGHTS.md"
    out_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"Written to {out_path}")


def _cmd_advise(ns: argparse.Namespace) -> None:
    from paragraph.advise import run as _run_advise
    sys.exit(_run_advise(ns.path))


def _cmd_connect_chunks(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        sys.exit(1)
    from paragraph.build import connect_orphan_chunks
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    data, stats = connect_orphan_chunks(data)
    if stats["linked"] == 0:
        print("No orphaned document/rationale chunks found — nothing to connect.")
        sys.exit(0)
    graph_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Linked {stats['linked']} orphaned chunk(s) across {stats['files']} file(s) "
          f"({stats['file_nodes_created']} file node(s) created).")
    print(f"Run `paragraph cluster-only {watch_path}` to re-cluster and refresh the report/viz.")


def _cmd_prune_generic(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    extra: set[str] = set()
    for chunk in ns.also or []:
        extra |= {s.strip().lower() for s in chunk.split(",") if s.strip()}
    keep: set[str] = set()
    for chunk in ns.keep or []:
        keep |= {s.strip().lower() for s in chunk.split(",") if s.strip()}
    dry_run = ns.dry_run
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        sys.exit(1)
    from paragraph.stoplist import prune_generic
    data = json.loads(graph_json.read_text(encoding="utf-8"))
    n_before = len(data.get("nodes", []))
    e_before = len(data.get("links", data.get("edges", [])))
    data, stats = prune_generic(data, extra_stoplist=frozenset(extra), keep=frozenset(keep))
    n_after = len(data.get("nodes", []))
    e_after = len(data.get("links", data.get("edges", [])))
    print(f"Shadow nodes merged into real definitions: {stats['merged']} "
          f"({stats['edges_remapped']} edge(s) remapped)")
    print(f"Generic symbols dropped: {stats['dropped']}")
    print(f"Nodes: {n_before} -> {n_after} | Edges: {e_before} -> {e_after}")
    if dry_run:
        print("Dry run — graph.json not modified.")
        sys.exit(0)
    if stats["merged"] == 0 and stats["dropped"] == 0:
        print("Nothing to prune.")
        sys.exit(0)
    graph_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Run `paragraph cluster-only {watch_path}` to re-cluster and refresh the report/viz.")


def _cmd_link(ns: argparse.Namespace) -> None:
    types = tuple(s.strip() for s in ns.types.split(",") if s.strip())
    from paragraph.link import run as _run_link
    sys.exit(_run_link(ns.path, threshold=ns.threshold, top_k=ns.top_k,
                       source_types=types, dry_run=ns.dry_run))


def _cmd_serve(ns: argparse.Namespace) -> None:
    graph_path = Path(ns.target)
    if graph_path.is_dir():
        graph_path = graph_path / "graphify-out" / "graph.json"
    from paragraph.serve import serve as _serve
    _serve(str(graph_path))


def _cmd_enrich(ns: argparse.Namespace) -> None:
    from paragraph.enrich import run as _run_enrich
    sys.exit(_run_enrich(ns.path, bodies_only=ns.bodies_only, embed_only=ns.embed_only,
                         stats_only=ns.stats, full=ns.full, model=ns.model))


def _cmd_cluster_only(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    graph_json = watch_path / "graphify-out" / "graph.json"
    if not graph_json.exists():
        print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
        sys.exit(1)
    from networkx.readwrite import json_graph as _jg
    from paragraph.build import build_from_json
    from paragraph.cluster import cluster, score_all
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
    from paragraph.cluster import carry_over_labels
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


def _cmd_update(ns: argparse.Namespace) -> None:
    watch_path = ns.path
    if not watch_path.exists():
        print(f"error: path not found: {watch_path}", file=sys.stderr)
        sys.exit(1)
    from paragraph.watch import _rebuild_code
    print(f"Re-extracting code files in {watch_path} (no LLM needed)...")
    ok = _rebuild_code(watch_path)
    if ok:
        print("Code graph updated. For doc/paper/image changes run /paragraph --update in your AI assistant.")
    else:
        print("Nothing to update or rebuild failed — check output above.", file=sys.stderr)
        sys.exit(1)


def _cmd_ingest_claude_mem(ns: argparse.Namespace) -> None:
    project_path = ns.path
    if not project_path.exists():
        print(f"error: path not found: {project_path}", file=sys.stderr)
        sys.exit(1)
    from paragraph.ingest_claudemem import run as _run_claudemem
    sys.exit(_run_claudemem(project_path, db_path=ns.db, graph_path=ns.graph,
                            project=ns.project, config_path=ns.config))


def _cmd_check_update(ns: argparse.Namespace) -> None:
    from paragraph.watch import check_update
    check_update(ns.path.resolve())
    sys.exit(0)


def _cmd_merge_graphs(ns: argparse.Namespace) -> None:
    graph_paths = [Path(g) for g in ns.graphs]
    out_path = ns.out
    if len(graph_paths) < 2:
        print("Usage: paragraph merge-graphs <graph1.json> <graph2.json> [...] [--out merged.json]", file=sys.stderr)
        sys.exit(1)
    import networkx as _nx
    from networkx.readwrite import json_graph as _jg
    graphs = []
    for gp in graph_paths:
        if not gp.exists():
            print(f"error: not found: {gp}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(gp.read_text(encoding="utf-8"))
        try:
            G = _jg.node_link_graph(data, edges="links")
        except TypeError:
            G = _jg.node_link_graph(data)
        # Tag every node with which repo it came from
        repo_tag = gp.parent.parent.name  # graphify-out/../ → repo dir name
        for node in G.nodes:
            G.nodes[node].setdefault("repo", repo_tag)
        graphs.append(G)
    merged = _nx.compose_all(graphs)
    try:
        out_data = _jg.node_link_data(merged, edges="links")
    except TypeError:
        out_data = _jg.node_link_data(merged)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
    print(f"Merged {len(graphs)} graphs → {merged.number_of_nodes()} nodes, {merged.number_of_edges()} edges")
    print(f"Written to: {out_path}")


def _cmd_clone(ns: argparse.Namespace) -> None:
    local_path = _clone_repo(ns.url, branch=ns.branch, out_dir=ns.out)
    print(local_path)


def _cmd_benchmark(ns: argparse.Namespace) -> None:
    from paragraph.benchmark import run_benchmark, print_benchmark
    graph_path = ns.graph
    # Try to load corpus_words from detect output
    corpus_words = None
    detect_path = Path(".graphify_detect.json")
    if detect_path.exists():
        try:
            detect_data = json.loads(detect_path.read_text(encoding="utf-8"))
            corpus_words = detect_data.get("total_words")
        except Exception:
            pass
    result = run_benchmark(graph_path, corpus_words=corpus_words)
    print_benchmark(result)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paragraph", description="paragraph CLI")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p = sub.add_parser("install",
                       help="copy skill to ~/.claude/skills/paragraph/ and register in CLAUDE.md")
    p.set_defaults(func=_cmd_install)

    p = sub.add_parser("claude",
                       help="install: write paragraph section to CLAUDE.md + PreToolUse hook; uninstall: remove them")
    p.add_argument("action", choices=("install", "uninstall"))
    p.set_defaults(func=_cmd_claude)

    p = sub.add_parser("hook",
                       help="install/uninstall/status for post-commit/post-checkout git hooks")
    p.add_argument("action", choices=("install", "uninstall", "status"))
    p.set_defaults(func=_cmd_hook)

    p = sub.add_parser("path",
                       help="shortest path between two nodes in graph.json")
    p.add_argument("source", metavar='"A"')
    p.add_argument("target", metavar='"B"')
    p.add_argument("--graph", default="graphify-out/graph.json",
                   help="path to graph.json (default graphify-out/graph.json)")
    p.set_defaults(func=_cmd_path)

    p = sub.add_parser("explain",
                       help="plain-language explanation of a node and its neighbors")
    p.add_argument("node", metavar='"X"')
    p.add_argument("--graph", default="graphify-out/graph.json",
                   help="path to graph.json (default graphify-out/graph.json)")
    p.set_defaults(func=_cmd_explain)

    p = sub.add_parser("clone",
                       help="clone a GitHub repo locally and print its path for /paragraph")
    p.add_argument("url", metavar="<github-url>")
    p.add_argument("--branch", default=None,
                   help="checkout a specific branch (default: repo default)")
    p.add_argument("--out", type=Path, default=None,
                   help="clone to a custom directory (default: ~/.paragraph/repos/<owner>/<repo>)")
    p.set_defaults(func=_cmd_clone)

    p = sub.add_parser("merge-graphs",
                       help="merge two or more graph.json files into one cross-repo graph")
    p.add_argument("graphs", nargs="*", metavar="graph.json")
    p.add_argument("--out", type=Path, default=Path("graphify-out/merged-graph.json"),
                   help="output path (default: graphify-out/merged-graph.json)")
    p.set_defaults(func=_cmd_merge_graphs)

    p = sub.add_parser("add",
                       help="fetch a URL and save it to ./raw, then update the graph")
    p.add_argument("url", metavar="<url>")
    p.add_argument("--author", default=None, metavar='"Name"',
                   help="tag the author of the content")
    p.add_argument("--contributor", default=None, metavar='"Name"',
                   help="tag who added it to the corpus")
    p.add_argument("--dir", type=Path, default=Path("raw"),
                   help="target directory (default: ./raw)")
    p.set_defaults(func=_cmd_add)

    p = sub.add_parser("watch",
                       help="watch a folder and rebuild the graph on code changes")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("update",
                       help="re-extract code files and update the graph (no LLM needed)")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_update)

    p = sub.add_parser("ingest-claude-mem",
                       help="inject claude-mem observations into the graph (idempotent)")
    p.add_argument("path", type=Path, metavar="<path>")
    p.add_argument("--db", type=Path, default=None,
                   help="claude-mem SQLite DB (default ~/.claude-mem/claude-mem.db)")
    p.add_argument("--graph", type=Path, default=None,
                   help="path to graph.json (default <path>/graphify-out/graph.json)")
    p.add_argument("--project", default=None,
                   help="claude-mem project name (default: basename of <path>)")
    p.add_argument("--config", type=Path, default=None,
                   help="filtering vocabulary JSON (default: graphify-out/ingest-config.json "
                        "or ~/.paragraph/ingest-config.json; see docs/examples/paranote-ingest.json)")
    p.set_defaults(func=_cmd_ingest_claude_mem)

    p = sub.add_parser("cluster-only",
                       help="rerun clustering on an existing graph.json and regenerate report")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_cluster_only)

    p = sub.add_parser("connect-chunks",
                       help="link orphaned doc/rationale chunks to per-file parent nodes")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_connect_chunks)

    p = sub.add_parser("prune-generic",
                       help="merge shadow nodes into real definitions and drop generic "
                            "stdlib/framework symbols (Sendable, View, str, ...) from graph.json")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.add_argument("--also", action="append", metavar="a,b,c",
                   help="additional labels to treat as generic (case-insensitive)")
    p.add_argument("--keep", action="append", metavar="a,b,c",
                   help="labels to exempt from the built-in stoplist")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change without writing")
    p.set_defaults(func=_cmd_prune_generic)

    p = sub.add_parser("link",
                       help="embedding-based doc<->code bridging: add conceptually_related_to "
                            "edges from document/rationale nodes to their most-similar code nodes")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.add_argument("--threshold", type=float, default=0.78, metavar="X",
                   help="minimum cosine similarity (default 0.78)")
    p.add_argument("--top-k", type=int, default=3, metavar="N",
                   help="max code links per source node (default 3)")
    p.add_argument("--types", default="document,rationale", metavar="a,b",
                   help="source file_types to link (default document,rationale)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be linked without writing")
    p.set_defaults(func=_cmd_link)

    p = sub.add_parser("analyze",
                       help="architectural analysis: community summaries, hubs/bridges/orphans, "
                            "cross-community dependency cycles -> graphify-out/GRAPH_INSIGHTS.md")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("advise",
                       help="Swift coding-standards advice from the graph -> graphify-out/ADVICE.md "
                            "(Swift nodes only — Python/other tooling code is excluded)")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.set_defaults(func=_cmd_advise)

    p = sub.add_parser("serve",
                       help="start the MCP stdio server (query_graph, retrieve, insights, ...)")
    p.add_argument("target", nargs="?", default="graphify-out/graph.json",
                   metavar="path|graph.json")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("enrich",
                       help="add source bodies + timestamps to graph.json and build vectors.db")
    p.add_argument("path", nargs="?", type=Path, default=Path("."))
    p.add_argument("--bodies-only", action="store_true",
                   help="skip the embedding step (no ollama needed)")
    p.add_argument("--embed-only", action="store_true",
                   help="skip bodies/timestamps, just (re)embed")
    p.add_argument("--stats", action="store_true",
                   help="report current enrichment state")
    p.add_argument("--full", action="store_true",
                   help="re-embed everything (default skips unchanged nodes)")
    p.add_argument("--model", default="nomic-embed-text", metavar="<name>",
                   help="embedding model (default nomic-embed-text)")
    p.set_defaults(func=_cmd_enrich)

    p = sub.add_parser("query",
                       help="BFS traversal of graph.json for a question")
    p.add_argument("question", metavar='"<question>"')
    p.add_argument("--dfs", action="store_true",
                   help="use depth-first instead of breadth-first")
    p.add_argument("--budget", type=int, default=2000, metavar="N",
                   help="cap output at N tokens (default 2000)")
    p.add_argument("--graph", default="graphify-out/graph.json",
                   help="path to graph.json (default graphify-out/graph.json)")
    p.set_defaults(func=_cmd_query)

    # retrieve delegates to paragraph.retrieve's own parser — all args pass
    # through untouched (main() short-circuits before parsing; the REMAINDER
    # positional keeps passthrough working if this subparser is ever hit).
    p = sub.add_parser("retrieve", add_help=False,
                       help="diversity-aware embedding retrieval (the evaluated read path)")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(func=_cmd_retrieve)

    p = sub.add_parser("save-result",
                       help="save a Q&A result to graphify-out/memory/ for graph feedback loop")
    p.add_argument("--question", required=True, metavar="Q",
                   help="the question asked")
    p.add_argument("--answer", required=True, metavar="A",
                   help="the answer to save")
    p.add_argument("--type", dest="query_type", default="query", metavar="T",
                   help="query type: query|path_query|explain (default: query)")
    p.add_argument("--nodes", nargs="*", default=[], metavar="N",
                   help="source node labels cited in the answer")
    p.add_argument("--memory-dir", default="graphify-out/memory", metavar="DIR",
                   help="memory directory (default: graphify-out/memory)")
    p.set_defaults(func=_cmd_save_result)

    p = sub.add_parser("check-update",
                       help="check needs_update flag and notify if semantic re-extraction is pending (cron-safe)")
    p.add_argument("path", type=Path, metavar="<path>")
    p.set_defaults(func=_cmd_check_update)

    p = sub.add_parser("benchmark",
                       help="measure token reduction vs naive full-corpus approach")
    p.add_argument("graph", nargs="?", default="graphify-out/graph.json",
                   metavar="graph.json")
    p.set_defaults(func=_cmd_benchmark)

    return parser


def main() -> None:
    # Check the Claude skill install location for a stale version stamp.
    # Skip during install/uninstall (hook writes trigger a fresh check anyway).
    if not any(arg in ("install", "uninstall") for arg in sys.argv):
        _check_skill_version(Path.home() / _PLATFORM_CONFIG["claude"]["skill_dst"])

    argv = sys.argv[1:]

    # retrieve owns its full flag surface in paragraph.retrieve — hand
    # everything after the command name straight through, untouched.
    if argv and argv[0] == "retrieve":
        from paragraph.retrieve import main as _retrieve_main
        sys.exit(_retrieve_main(argv[1:]))

    parser = _build_parser()
    if not argv:
        parser.print_help()
        return
    ns = parser.parse_args(argv)
    ns.func(ns)


if __name__ == "__main__":
    main()
