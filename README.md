# ParaGraph

A Claude Code skill that turns any folder of code, docs, papers, images, or videos into a queryable knowledge graph.

Fork of [graphify](https://github.com/safishamsi/graphify) by Safi Shamsi, purpose-built for Claude Code.

## What it does

Type `/paragraph` in Claude Code. It reads your files, builds a knowledge graph, and gives you structure you didn't know was there. Understand a codebase faster. Find the "why" behind architectural decisions.

- **Persistent graph** -- relationships stored in `graphify-out/graph.json` survive across sessions
- **Multimodal** -- code, PDFs, markdown, screenshots, diagrams, video, audio
- **25 languages** via tree-sitter AST (Python, JS, TS, Go, Rust, Java, C, C++, Ruby, C#, Kotlin, Scala, PHP, Swift, Lua, Zig, PowerShell, Elixir, Objective-C, Julia, Verilog, and more)
- **Community detection** -- Leiden/Louvain clustering identifies module boundaries
- **71.5x token reduction** vs reading raw files

## Install

```bash
uv tool install paragraph
paragraph install
```

This copies the skill to `~/.claude/skills/paragraph/SKILL.md` and registers it in `~/.claude/CLAUDE.md`.

## Usage

In Claude Code:

```
/paragraph .                    # build graph from current directory
/paragraph path "A" "B"        # shortest path between two nodes
/paragraph explain "X"         # explain a node and its neighbors
/paragraph query "question"    # BFS/DFS traversal for a question
```

### CLI commands

```
paragraph install              # install skill for Claude Code
paragraph update <path>        # re-extract code (no LLM needed)
paragraph cluster-only <path>  # rerun clustering on existing graph
paragraph watch <path>         # watch folder, auto-rebuild on changes
paragraph add <url>            # fetch URL content into ./raw
paragraph clone <github-url>   # clone repo for analysis
paragraph merge-graphs g1 g2   # merge multiple graphs
paragraph hook install         # install post-commit hook
paragraph benchmark            # measure token reduction
```

### Post-commit hook

```bash
paragraph hook install    # auto-rebuild graph on every commit
paragraph hook status     # check if installed
paragraph hook uninstall  # remove
```

## Output

All output goes to `graphify-out/` (kept for backward compatibility):

- `graph.json` -- the knowledge graph (nodes, edges, communities)
- `graph.html` -- interactive visualization
- `GRAPH_REPORT.md` -- god nodes, community structure, suggested questions

## Fork changes from graphify

- **Claude Code only** -- removed support for Codex, Cursor, Gemini, Aider, Kiro, and 8 other platforms
- **LLM field normalization** -- fixes graphify 0.5.0 bugs where LLM extraction produces wrong field names (`type` vs `file_type`, `file` vs `source_file`, `type` vs `confidence` on edges)
- **Safe code-only rebuilds** -- `watch.py` passes `force=True` to `to_json` because code-only rebuilds legitimately produce fewer nodes than enriched graphs (semantic nodes are preserved internally)
- **Simplified CLI** -- 1507 lines down to ~680, no multi-platform install/uninstall logic

## License

MIT -- see [LICENSE](LICENSE).

Original work by [Safi Shamsi](https://github.com/safishamsi/graphify).
