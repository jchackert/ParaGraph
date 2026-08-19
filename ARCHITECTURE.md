# Architecture

paragraph is a Claude Code skill backed by a Python library. The skill orchestrates the library; the library can be used standalone.

## Pipeline

```
detect()  →  extract()  →  build_graph()  →  cluster()  →  analyze()  →  report()  →  export()
```

Each stage is a single function in its own module. They communicate through plain Python dicts and NetworkX graphs - no shared state, no side effects outside `graphify-out/`.

## Module responsibilities

| Module | Function | Input → Output |
|--------|----------|----------------|
| `detect.py` | `collect_files(root)` | directory → `[Path]` filtered list |
| `extract.py` | `extract(path)` | file path → `{nodes, edges}` dict |
| `build.py` | `build_graph(extractions)` | list of extraction dicts → `nx.Graph` |
| `cluster.py` | `cluster / label_communities / carry_over_labels` | graph → communities + labels (deterministic member-based; prior labels carried over by member overlap) |
| `analyze.py` | `god_nodes / surprising_connections / suggest_questions / graph_diff` | graph → analysis dict (god nodes, surprises, questions) |
| `insights.py` | `insights_markdown(G, communities, ...)` | graph → GRAPH_INSIGHTS.md (community summaries, hubs/bridges/orphans, dependency cycles) |
| `report.py` | `render_report(G, analysis)` | graph + analysis → GRAPH_REPORT.md string |
| `export.py` | `to_json / to_html_auto / to_graphml / to_cypher` | graph → graph.json, graph.html (aggregated community view above 5,000 nodes), graph.graphml, cypher.txt |
| `ingest.py` | `ingest(url, ...)` | URL → file saved to corpus dir |
| `cache.py` | `check_semantic_cache / save_semantic_cache` | files → (cached, uncached) split |
| `security.py` | validation helpers | URL / path / label → validated or raises |
| `validate.py` | `validate_extraction(data)` | extraction dict → raises on schema errors |
| `serve.py` | `start_server(graph_path)` | graph file path → MCP stdio server |
| `watch.py` | `watch / _rebuild_code` | directory → LLM-free rebuild of graph.json/graph.html/GRAPH_REPORT.md on code changes |
| `enrich.py` | `run(project_path, ...)` | graph.json → source bodies + timestamps + `vectors.db` (ollama embeddings) |
| `retrieve.py` | `main` (`paragraph retrieve`) | question + vectors.db → diversity-aware ranked context (eval-tuned) |
| `ingest_claudemem.py` | `run(project_path, ...)` | claude-mem DB → observation nodes injected into graph.json (config-driven filtering) |
| `hooks.py` | `install_hooks / uninstall_hooks` | git repo → post-commit/post-checkout rebuild hooks |
| `benchmark.py` | `run_benchmark(graph_path)` | graph file → corpus vs subgraph token comparison |

## Extraction output schema

Every extractor returns:

```json
{
  "nodes": [
    {"id": "unique_string", "label": "human name", "source_file": "path", "source_location": "L42"}
  ],
  "edges": [
    {"source": "id_a", "target": "id_b", "relation": "calls|imports|uses|...", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
  ]
}
```

`validate.py` enforces this schema before `build_graph()` consumes it.

## Confidence labels

| Label | Meaning |
|-------|---------|
| `EXTRACTED` | Relationship is explicitly stated in the source (e.g., an import statement, a direct call) |
| `INFERRED` | Relationship is a reasonable deduction (e.g., call-graph second pass, co-occurrence in context) |
| `AMBIGUOUS` | Relationship is uncertain; flagged for human review in GRAPH_REPORT.md |

## Adding a new language extractor

1. Add a `extract_<lang>(path: Path) -> dict` function in `extract.py` following the existing pattern (tree-sitter parse → walk nodes → collect `nodes` and `edges` → call-graph second pass for INFERRED `calls` edges).
2. Register the file suffix in `extract()` dispatch and `collect_files()`.
3. Add the suffix to `CODE_EXTENSIONS` in `detect.py` and `_WATCHED_EXTENSIONS` in `watch.py`.
4. Add the tree-sitter package to `pyproject.toml` dependencies.
5. Add a fixture file to `tests/fixtures/` and tests to `tests/test_languages.py`.

## Security

All external input passes through `paragraph/security.py` before use:

- URLs → `validate_url()` (http/https only) + `_NoFileRedirectHandler` (blocks file:// redirects)
- Fetched content → `safe_fetch()` / `safe_fetch_text()` (size cap, timeout)
- Graph file paths → `validate_graph_path()` (must resolve inside `graphify-out/`)
- Node labels → `sanitize_label()` (strips control chars, caps 256 chars, HTML-escapes)

See `SECURITY.md` for the full threat model.

## Testing

One test file per module under `tests/`. Run with:

```bash
pytest tests/ -q
```

All tests are pure unit tests - no network calls, no file system side effects outside `tmp_path`.
