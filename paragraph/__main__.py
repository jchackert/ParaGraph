"""paragraph CLI - `paragraph install` sets up the Claude Code skill."""
from __future__ import annotations
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


def main() -> None:
    # Check the Claude skill install location for a stale version stamp.
    # Skip during install/uninstall (hook writes trigger a fresh check anyway).
    if not any(arg in ("install", "uninstall") for arg in sys.argv):
        _check_skill_version(Path.home() / _PLATFORM_CONFIG["claude"]["skill_dst"])

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: paragraph <command>")
        print()
        print("Commands:")
        print("  install                 copy skill to ~/.claude/skills/paragraph/ and register in CLAUDE.md")
        print("  claude install          write paragraph section to CLAUDE.md + PreToolUse hook")
        print("  claude uninstall        remove paragraph section from CLAUDE.md + PreToolUse hook")
        print("  path \"A\" \"B\"            shortest path between two nodes in graph.json")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("  explain \"X\"             plain-language explanation of a node and its neighbors")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("  clone <github-url>      clone a GitHub repo locally and print its path for /paragraph")
        print("    --branch <branch>       checkout a specific branch (default: repo default)")
        print("    --out <dir>             clone to a custom directory (default: ~/.paragraph/repos/<owner>/<repo>)")
        print("  merge-graphs <g1> <g2>  merge two or more graph.json files into one cross-repo graph")
        print("    --out <path>            output path (default: graphify-out/merged-graph.json)")
        print("  add <url>               fetch a URL and save it to ./raw, then update the graph")
        print("    --author \"Name\"         tag the author of the content")
        print("    --contributor \"Name\"    tag who added it to the corpus")
        print("    --dir <path>            target directory (default: ./raw)")
        print("  watch <path>            watch a folder and rebuild the graph on code changes")
        print("  update <path>           re-extract code files and update the graph (no LLM needed)")
        print("  ingest-claude-mem <path> inject claude-mem observations into the graph (idempotent)")
        print("    --db <path>             claude-mem SQLite DB (default ~/.claude-mem/claude-mem.db)")
        print("    --graph <path>          path to graph.json (default <path>/graphify-out/graph.json)")
        print("    --project <name>        claude-mem project name (default: basename of <path>)")
        print("    --config <path>         filtering vocabulary JSON (default: graphify-out/ingest-config.json")
        print("                            or ~/.paragraph/ingest-config.json; see docs/examples/paranote-ingest.json)")
        print("  cluster-only <path>     rerun clustering on an existing graph.json and regenerate report")
        print("  analyze [path]          architectural analysis: community summaries, hubs/bridges/orphans,")
        print("                          cross-community dependency cycles -> graphify-out/GRAPH_INSIGHTS.md")
        print("  enrich [path]           add source bodies + timestamps to graph.json and build vectors.db")
        print("    --bodies-only           skip the embedding step (no ollama needed)")
        print("    --embed-only            skip bodies/timestamps, just (re)embed")
        print("    --stats                 report current enrichment state")
        print("    --model <name>          embedding model (default nomic-embed-text)")
        print("  query \"<question>\"       BFS traversal of graph.json for a question")
        print("    --dfs                   use depth-first instead of breadth-first")
        print("    --budget N              cap output at N tokens (default 2000)")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("  retrieve \"<question>\"    diversity-aware embedding retrieval (the evaluated read path)")
        print("    --top-k N               ranked results to keep (default 10)")
        print("    --budget N              token budget for packed chunks (default 8000)")
        print("    --json                  output JSON with chunks + provenance")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("    --vectors <path>        path to vectors.db (default: next to graph.json)")
        print("    --eval <eval.json>      run the labeled retrieval eval instead of a single query")
        print("  save-result             save a Q&A result to graphify-out/memory/ for graph feedback loop")
        print("    --question Q            the question asked")
        print("    --answer A              the answer to save")
        print("    --type T                query type: query|path_query|explain (default: query)")
        print("    --nodes N1 N2 ...       source node labels cited in the answer")
        print("    --memory-dir DIR        memory directory (default: graphify-out/memory)")
        print("  check-update <path>     check needs_update flag and notify if semantic re-extraction is pending (cron-safe)")
        print("  benchmark [graph.json]  measure token reduction vs naive full-corpus approach")
        print("  hook install            install post-commit/post-checkout git hooks")
        print("  hook uninstall          remove git hooks")
        print("  hook status             check if git hooks are installed")
        print()
        return

    cmd = sys.argv[1]
    if cmd == "install":
        install()
    elif cmd == "claude":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            claude_install()
        elif subcmd == "uninstall":
            claude_uninstall()
        else:
            print("Usage: paragraph claude [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "hook":
        from paragraph.hooks import install as hook_install, uninstall as hook_uninstall, status as hook_status
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            print(hook_install(Path(".")))
        elif subcmd == "uninstall":
            print(hook_uninstall(Path(".")))
        elif subcmd == "status":
            print(hook_status(Path(".")))
        else:
            print("Usage: paragraph hook [install|uninstall|status]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "query":
        if len(sys.argv) < 3:
            print("Usage: paragraph query \"<question>\" [--dfs] [--budget N] [--graph path]", file=sys.stderr)
            sys.exit(1)
        from paragraph.serve import _score_nodes, _bfs, _dfs, _subgraph_to_text
        from paragraph.security import sanitize_label
        from networkx.readwrite import json_graph
        question = sys.argv[2]
        use_dfs = "--dfs" in sys.argv
        budget = 2000
        graph_path = "graphify-out/graph.json"
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--budget" and i + 1 < len(args):
                try:
                    budget = int(args[i + 1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 2
            elif args[i].startswith("--budget="):
                try:
                    budget = int(args[i].split("=", 1)[1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 1
            elif args[i] == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            else:
                i += 1
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
    elif cmd == "retrieve":
        from paragraph.retrieve import main as _retrieve_main
        sys.exit(_retrieve_main(sys.argv[2:]))
    elif cmd == "save-result":
        # paragraph save-result --question Q --answer A --type T [--nodes N1 N2 ...]
        import argparse as _ap
        p = _ap.ArgumentParser(prog="paragraph save-result")
        p.add_argument("--question", required=True)
        p.add_argument("--answer", required=True)
        p.add_argument("--type", dest="query_type", default="query")
        p.add_argument("--nodes", nargs="*", default=[])
        p.add_argument("--memory-dir", default="graphify-out/memory")
        opts = p.parse_args(sys.argv[2:])
        from paragraph.ingest import save_query_result as _sqr
        out = _sqr(
            question=opts.question,
            answer=opts.answer,
            memory_dir=Path(opts.memory_dir),
            query_type=opts.query_type,
            source_nodes=opts.nodes or None,
        )
        print(f"Saved to {out}")
    elif cmd == "path":
        if len(sys.argv) < 4:
            print("Usage: paragraph path \"<source>\" \"<target>\" [--graph path]", file=sys.stderr)
            sys.exit(1)
        from paragraph.serve import _score_nodes
        from networkx.readwrite import json_graph
        import networkx as _nx
        source_label = sys.argv[2]
        target_label = sys.argv[3]
        graph_path = "graphify-out/graph.json"
        args = sys.argv[4:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
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

    elif cmd == "explain":
        if len(sys.argv) < 3:
            print("Usage: paragraph explain \"<node>\" [--graph path]", file=sys.stderr)
            sys.exit(1)
        from paragraph.serve import _find_node
        from networkx.readwrite import json_graph
        label = sys.argv[2]
        graph_path = "graphify-out/graph.json"
        args = sys.argv[3:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
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

    elif cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: paragraph add <url> [--author Name] [--contributor Name] [--dir ./raw]", file=sys.stderr)
            sys.exit(1)
        from paragraph.ingest import ingest as _ingest
        url = sys.argv[2]
        author: str | None = None
        contributor: str | None = None
        target_dir = Path("raw")
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--author" and i + 1 < len(args):
                author = args[i + 1]; i += 2
            elif args[i] == "--contributor" and i + 1 < len(args):
                contributor = args[i + 1]; i += 2
            elif args[i] == "--dir" and i + 1 < len(args):
                target_dir = Path(args[i + 1]); i += 2
            else:
                i += 1
        try:
            saved = _ingest(url, target_dir, author=author, contributor=contributor)
            print(f"Saved to {saved}")
            print("Run /paragraph --update in your AI assistant to update the graph.")
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "watch":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
        if not watch_path.exists():
            print(f"error: path not found: {watch_path}", file=sys.stderr)
            sys.exit(1)
        from paragraph.watch import watch as _watch
        try:
            _watch(watch_path)
        except ImportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "analyze":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
        graph_json = watch_path / "graphify-out" / "graph.json"
        if not graph_json.exists():
            print(f"error: no graph found at {graph_json} — run /paragraph first", file=sys.stderr)
            sys.exit(1)
        from networkx.readwrite import json_graph as _jg
        from paragraph.cluster import score_all
        from paragraph.insights import insights_markdown
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
        md = insights_markdown(G, communities, cohesion, labels or None)
        out_path = watch_path / "graphify-out" / "GRAPH_INSIGHTS.md"
        out_path.write_text(md, encoding="utf-8")
        print(md)
        print(f"Written to {out_path}")

    elif cmd == "enrich":
        args = sys.argv[2:]
        target = Path(".")
        bodies_only = embed_only = stats_only = False
        model = "nomic-embed-text"
        i = 0
        while i < len(args):
            if args[i] == "--bodies-only":
                bodies_only = True; i += 1
            elif args[i] == "--embed-only":
                embed_only = True; i += 1
            elif args[i] == "--stats":
                stats_only = True; i += 1
            elif args[i] == "--model" and i + 1 < len(args):
                model = args[i + 1]; i += 2
            elif not args[i].startswith("--"):
                target = Path(args[i]); i += 1
            else:
                i += 1
        from paragraph.enrich import run as _run_enrich
        sys.exit(_run_enrich(target, bodies_only=bodies_only, embed_only=embed_only,
                             stats_only=stats_only, model=model))

    elif cmd == "cluster-only":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
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
        viz = to_html_auto(G, communities, str(out / "graph.html"), community_labels=labels or None)
        if viz == "aggregated":
            print("Graph too large for full viz — graph.html shows the aggregated community view.")
        html_part = " graph.json and graph.html" if viz != "skipped" else " and graph.json"
        print(f"Done — {len(communities)} communities. GRAPH_REPORT.md,{html_part} updated.")

    elif cmd == "update":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
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

    elif cmd == "ingest-claude-mem":
        if len(sys.argv) < 3:
            print("Usage: paragraph ingest-claude-mem <project-path> [--db path] [--graph path] [--project name] [--config path]", file=sys.stderr)
            sys.exit(1)
        project_path = Path(sys.argv[2])
        if not project_path.exists():
            print(f"error: path not found: {project_path}", file=sys.stderr)
            sys.exit(1)
        db_path: Path | None = None
        cm_graph_path: Path | None = None
        cm_project: str | None = None
        cm_config: Path | None = None
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--db" and i + 1 < len(args):
                db_path = Path(args[i + 1]); i += 2
            elif args[i] == "--graph" and i + 1 < len(args):
                cm_graph_path = Path(args[i + 1]); i += 2
            elif args[i] == "--project" and i + 1 < len(args):
                cm_project = args[i + 1]; i += 2
            elif args[i] == "--config" and i + 1 < len(args):
                cm_config = Path(args[i + 1]); i += 2
            else:
                i += 1
        from paragraph.ingest_claudemem import run as _run_claudemem
        sys.exit(_run_claudemem(project_path, db_path=db_path, graph_path=cm_graph_path,
                                project=cm_project, config_path=cm_config))

    elif cmd == "check-update":
        if len(sys.argv) < 3:
            print("Usage: paragraph check-update <path>", file=sys.stderr)
            sys.exit(1)
        from paragraph.watch import check_update
        check_update(Path(sys.argv[2]).resolve())
        sys.exit(0)
    elif cmd == "merge-graphs":
        # paragraph merge-graphs graph1.json graph2.json ... --out merged.json
        args = sys.argv[2:]
        graph_paths: list[Path] = []
        out_path = Path("graphify-out/merged-graph.json")
        i = 0
        while i < len(args):
            if args[i] == "--out" and i + 1 < len(args):
                out_path = Path(args[i + 1]); i += 2
            else:
                graph_paths.append(Path(args[i])); i += 1
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

    elif cmd == "clone":
        if len(sys.argv) < 3:
            print("Usage: paragraph clone <github-url> [--branch <branch>] [--out <dir>]", file=sys.stderr)
            sys.exit(1)
        url = sys.argv[2]
        branch: str | None = None
        out_dir: Path | None = None
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--branch" and i + 1 < len(args):
                branch = args[i + 1]; i += 2
            elif args[i] == "--out" and i + 1 < len(args):
                out_dir = Path(args[i + 1]); i += 2
            else:
                i += 1
        local_path = _clone_repo(url, branch=branch, out_dir=out_dir)
        print(local_path)

    elif cmd == "benchmark":
        from paragraph.benchmark import run_benchmark, print_benchmark
        graph_path = sys.argv[2] if len(sys.argv) > 2 else "graphify-out/graph.json"
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
    else:
        print(f"error: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'paragraph --help' for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
