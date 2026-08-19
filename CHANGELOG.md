# Changelog

## 0.2.0

### Swift architecture visualization + standards advisor

- **Shared layer classifier** (`paragraph/layers.py`) -- classifies nodes into view/viewmodel/service/model/core by directory and name signals; Python/shell tooling and docs are classified OUT of the Swift stack and exempt from all Swift analysis
- **Layered architecture view** -- graph.html "Layers" toggle arranges nodes in swimlanes by layer with dependency direction flowing down; upward edges (a Model reaching into a View) render red with a violation count
- **Lenses** -- live filters on edge relation (calls/imports/contains), confidence tier, and node file type
- **Blast radius** -- arm the button, click a node: transitive dependents highlight by hop distance, everything else dims
- **`paragraph advise`** -- Swift coding-standards advisor: 16-rule curated pack (`paragraph/standards/swift.json`) with paraphrased statements, original Avoid/Prefer snippets, and citations to Apple (Swift API Design Guidelines, Swift book, WWDC 2015/21/23) and community canon (Swift by Sundell, Hacking with Swift, SwiftLee, Donny Wals, Point-Free, objc.io; all 25 URLs link-checked). Eight detectors (massive types, god objects, lean-view-model, view-skips-viewmodel, layering violations, singleton fan-in, force operations, DispatchQueue.main in observables) run over the graph and emit `ADVICE.md` with EXTRACTED/INFERRED confidence per finding. Strictly Swift-gated: tooling code produces zero findings and the excluded count is reported. Also exposed as an `advise` MCP tool

### Graph hygiene (pollution cleanup)

- **Fix: label dedup no longer collapses code across files** -- `deduplicate_by_label` scoped code nodes (and bare-callable labels like `main()`) to their `source_file`; merging every script's `main()`/`run()` into one node had corrupted god nodes and surprising-connection analysis (389 bad merges observed in a real corpus). Concept/document dedup across chunks unchanged
- **`paragraph connect-chunks`** -- links orphaned document/rationale chunks to a per-file parent node with `part_of` edges, so a chunk-ingested file clusters as one community instead of hundreds of singletons; RAG content untouched
- **Aggregated viz collapses orphan noise** -- communities whose members are all disconnected render as one grey "Unconnected content (N nodes)" meta-node instead of a ring of dots
- **Ingest: `drop_unlinked` + `resolution_keywords`** -- observations whose file references resolve to nothing can be dropped at the door (they cannot aid traversal), and ticket-resolution passthrough words are configurable; keyword matching is now word-boundary based ("plan" no longer fires on "Plane")

### Added

- **Path-based observation linking** -- `ingest-claude-mem` matches observation file paths against node `source_file` (exact, then unambiguous suffix) before falling back to filename stems; same-named files in different directories no longer mis-link
- **Incremental enrich** -- unchanged nodes are skipped on re-embed (content hash per row), embeddings for deleted nodes are pruned; `--full` forces a complete re-embed. Cheap enough to run after every rebuild
- **Viz drill-down** -- the aggregated overview (>5,000 nodes) links one full-featured subgraph page per community under `graph_communities/`; double-click a community to open it. When a graph drops back under the ceiling, the full/skipped viz paths clear stale drill-down pages
- **`paragraph serve`** -- proper CLI entry for the MCP server, which gains `retrieve` (semantic, via vectors.db + ollama) and `insights` (architectural analysis) tools; `mcp` dependency pinned `<2` (2.0 removed the decorator API)
- **Trends** -- every rebuild records a structural snapshot to `graphify-out/.paragraph_history.jsonl`; `paragraph analyze` renders deltas (nodes/edges/communities/cycles/orphans) with warnings when cycles or orphans grow. Snapshots within a 30-minute window coalesce into one row, so multi-step pipelines (update -> ingest -> cluster-only) record only the settled final state
- **Retrieval eval kit** -- `docs/RETRIEVAL_EVAL.md` (protocol + labeling discipline) and `docs/examples/retrieval_eval.json` (template) so `paragraph retrieve --eval` is usable outside ParaNote
- **CI** -- GitHub Actions test matrix on Python 3.10-3.13

- **Persistent community labels** -- Claude-written labels are stored in `graph.json` (`graph.community_labels`) and survive the skill's temp-file cleanup; LLM-free rebuilds (`update`, `watch`, `cluster-only`) carry labels across re-clustering by member overlap (`cluster.carry_over_labels`) instead of resetting to "Community N", falling back to deterministic member-based names
- **`paragraph analyze`** -- architectural analysis to `graphify-out/GRAPH_INSIGHTS.md`: per-community summaries (size, cohesion, isolation, dominant directories), hub/bridge/orphan node classification, cross-community dependency cycles (new `paragraph/insights.py`)
- **`paragraph enrich`** -- the vectors.db producer, ported from PARA_Note's `graphify-enrich.py`: source bodies on code nodes, timestamps, embeddings via local ollama (new `paragraph/enrich.py`). `paragraph retrieve` now works end-to-end
- **Config-driven claude-mem ingest** -- filtering vocabulary (domain keywords, reviewer names, ticket patterns) moved from hardcoded constants to `IngestConfig`, loaded from `graphify-out/ingest-config.json`, `~/.paragraph/ingest-config.json`, or `--config`; ParaNote's vocabulary ships as `docs/examples/paranote-ingest.json`
- **Oversized-graph viz fallback** -- graphs over 5,000 nodes get an aggregated community-level `graph.html` (`to_html_auto`) instead of none
- **Chunk dedup wired in** -- `deduplicate_by_label` now runs in the skill's semantic merge step (it was documented as automatic but never called)

### Fixed

- `skill.md` invoked `python -m graphify save-result` (3x) and emitted a `graphify.serve` MCP config -- both failed with `No module named graphify`
- `paragraph clone` wrote `~/.graphify/repos/` while skill.md read `~/.paragraph/repos/` -- multi-repo merge could not find its inputs (legacy clones still found)
- MCP server now actually calls `security.validate_graph_path()` as SECURITY.md claimed
- Whisper env vars renamed to `PARAGRAPH_WHISPER_MODEL`/`PARAGRAPH_WHISPER_PROMPT` (old `GRAPHIFY_*` names still honored)

### Removed

- Long-tail hand-rolled extractors: Julia, Verilog, Zig, PowerShell, Elixir, Dart, Blade (~950 lines); their grammars dropped from the `[languages]` extra. Those files still get semantic extraction
- Obsidian vault + Canvas, SVG, and wiki exports (~800 lines incl. `wiki.py`); the `svg` extra
- Dead code: `manifest.py`, `build()`, `build_merge`, `prune_dangling_edges`, `generate_html` alias, tests-only helpers
- Shared tree-sitter preamble and `add_node`/`add_edge` boilerplate hoisted out of the remaining hand-rolled extractors (Go, Rust, Objective-C)

## 0.1.0 (fork)

Forked from [graphify 0.5.0](https://github.com/safishamsi/graphify) by Safi Shamsi.

### Changes from upstream

- **Claude Code only** -- removed support for Codex, Cursor, Gemini, Aider, Kiro, OpenCode, Claw, Droid, Trae, Hermes, Copilot, Antigravity, VS Code
- **Renamed** -- package `graphifyy` to `paragraph`, CLI `graphify` to `paragraph`
- **Fix: LLM field normalization** -- `build.py` normalizes wrong field names from LLM extraction (`type` vs `file_type`, `file` vs `source_file`, `type` vs `confidence` on edges) before validation
- **Fix: code-only rebuild** -- `watch.py` passes `force=True` to `to_json` in `_rebuild_code` (semantic nodes preserved internally; node count drop is code ID churn, not data loss)
- **Simplified CLI** -- 1507 lines to ~680, single-platform install
- **Removed** -- 10 platform skill files (~460KB), `worked/` examples, `docs/translations/` (27 files)

### Upstream changelog

See [graphify releases](https://github.com/safishamsi/graphify/releases) for pre-fork history.
