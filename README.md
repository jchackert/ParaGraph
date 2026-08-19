# ParaGraph

A Claude Code skill that turns any folder of code, docs, papers, images, or videos into a queryable knowledge graph.

Fork of [graphify](https://github.com/safishamsi/graphify) by Safi Shamsi, purpose-built for Claude Code.

## What it does

Type `/paragraph` in Claude Code. It reads your files, builds a knowledge graph, and gives you structure you didn't know was there. Understand a codebase faster. Find the "why" behind architectural decisions.

- **Persistent graph** -- relationships stored in `graphify-out/graph.json` survive across sessions
- **Multimodal** -- code, PDFs, markdown, screenshots, diagrams, video, audio
- **16 languages** via tree-sitter AST (Python, JS, TS, Go, Rust, Java, C, C++, Ruby, C#, Kotlin, Scala, PHP, Swift, Lua, Objective-C); other languages still get semantic extraction
- **Community detection** -- Leiden/Louvain clustering identifies module boundaries, with meaningful names that survive rebuilds (Claude-written labels persist in graph.json; LLM-free rebuilds carry them over by member overlap)
- **Architectural analysis** -- `paragraph analyze` reports community summaries, hubs/bridges/orphans, and cross-community dependency cycles; graph.html adds a layered architecture view (dependency-direction swimlanes with violation highlighting), relation/confidence lenses, and blast-radius mode
- **Swift standards advisor** -- `paragraph advise` checks Swift code (only Swift -- tooling scripts are excluded) against a 16-rule pack citing Apple's guidelines, WWDC sessions, and the Swift community canon, with Avoid/Prefer snippets in `ADVICE.md`
- **Semantic retrieval** -- `paragraph enrich` builds a local vector store (ollama embeddings); `paragraph retrieve` is the eval-tuned read path
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
paragraph analyze [path]       # architectural analysis -> GRAPH_INSIGHTS.md
paragraph enrich [path]        # source bodies + timestamps + vectors.db (ollama)
paragraph retrieve "question"  # diversity-aware embedding retrieval
paragraph ingest-claude-mem .  # inject claude-mem observations (config-driven)
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

- `graph.json` -- the knowledge graph (nodes, edges, communities, community labels)
- `graph.html` -- interactive visualization (aggregated community view above 5,000 nodes)
- `GRAPH_REPORT.md` -- god nodes, community structure, suggested questions
- `GRAPH_INSIGHTS.md` -- architectural analysis (from `paragraph analyze`)
- `vectors.db` -- embedding store for `paragraph retrieve` (from `paragraph enrich`)

## Fork changes from graphify

- **Claude Code only** -- removed support for Codex, Cursor, Gemini, Aider, Kiro, and 8 other platforms
- **LLM field normalization** -- fixes graphify 0.5.0 bugs where LLM extraction produces wrong field names (`type` vs `file_type`, `file` vs `source_file`, `type` vs `confidence` on edges)
- **Safe code-only rebuilds** -- `watch.py` passes `force=True` to `to_json` because code-only rebuilds legitimately produce fewer nodes than enriched graphs (semantic nodes are preserved internally)
- **Simplified CLI** -- no multi-platform install/uninstall logic
- **Slimmed export surface** -- Obsidian/Canvas, SVG, and wiki exports removed; HTML, JSON, GraphML, and Neo4j/Cypher remain
- **Persistent community labels** -- graph.json is the canonical label store; re-clustering carries labels over by member overlap instead of resetting to "Community N"

## License

MIT -- see [LICENSE](LICENSE).

Original work by [Safi Shamsi](https://github.com/safishamsi/graphify).
