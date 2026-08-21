# enrichment pipeline: source bodies, timestamps, and embeddings for graph.json.
# Produces graphify-out/vectors.db — the store `paragraph retrieve` reads.
#
# Ported from PARA_Note scripts/graphify-enrich.py with paths parameterized
# so it runs against any project's graphify-out/.
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/embeddings"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

# Max chars of source body per node
MAX_BODY_CHARS = 2000
# Max chars sent to embedder
MAX_EMBED_CHARS = 1500


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Embedder abstraction
# ---------------------------------------------------------------------------
class Embedder:
    """Pluggable embedding interface. Default: local ollama nomic-embed-text."""

    def __init__(self, model: str = DEFAULT_EMBED_MODEL, url: str = OLLAMA_URL):
        self.model = model
        self.url = url
        self._dims: int | None = None

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def dims(self) -> int:
        if self._dims is None:
            test = self.embed("test")
            self._dims = len(test) if test else 768
        return self._dims

    def embed(self, text: str) -> list[float] | None:
        payload = json.dumps({
            "model": self.model,
            "prompt": text[:MAX_EMBED_CHARS],
        }).encode()
        req = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read()).get("embedding")
        except Exception as e:
            print(f"    Embed error: {e}", file=sys.stderr)
            return None

    def healthy(self) -> bool:
        try:
            return self.embed("health check") is not None
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Step 1: Source body extraction
# ---------------------------------------------------------------------------

def _read_file_lines(path: str) -> list[str] | None:
    try:
        return Path(path).read_text(errors="replace").splitlines()
    except (OSError, PermissionError):
        return None


def _parse_start_line(loc: str | None) -> int | None:
    m = re.match(r"L(\d+)", loc or "")
    return int(m.group(1)) - 1 if m else None


def _is_container_node(node_id: str, edges: list[dict]) -> bool:
    """Check if this node contains other nodes (class/struct/enum)."""
    return any(
        e.get("relation") in ("contains", "method")
        and (e.get("source") == node_id or e.get("_src") == node_id)
        for e in edges
    )


def _extract_structural_body(lines: list[str], start: int, end: int) -> str:
    """Extract declaration + docstring + method/property signatures for a container.

    Keeps the declaration line and its attributes, doc comments, property
    declarations, method signatures without their bodies, and nested type
    declarations. Tuned for brace-delimited languages (Swift/C-family) but
    degrades to keeping declaration-shaped lines elsewhere.
    """
    result = []
    indent = None
    skip_body = False
    body_depth = 0

    for i in range(start, min(end, len(lines))):
        line = lines[i]
        stripped = line.lstrip()

        if indent is None and stripped and not stripped.startswith("//"):
            indent = len(line) - len(stripped)

        current_indent = len(line) - len(stripped) if stripped else 999

        # If we're skipping a method body, track braces
        if skip_body:
            for ch in line:
                if ch == "{":
                    body_depth += 1
                elif ch == "}":
                    body_depth -= 1
            if body_depth <= 0:
                skip_body = False
            continue

        if not stripped:
            result.append(line)
            continue
        if stripped.startswith("///") or stripped.startswith("//"):
            result.append(line)
            continue
        if stripped.startswith("@"):
            result.append(line)
            continue

        # Keep declarations at container indent level (+0 or +4)
        if indent is not None and current_indent <= indent + 4:
            if re.match(r"\s*(public |private |internal |fileprivate )?(static )?(var|let) ", line):
                result.append(line)
                continue
            if re.match(r"\s*(public |private |internal |fileprivate )?(enum|struct|class|protocol) ", line):
                result.append(line)
                continue
            if re.match(r"\s*(public |private |internal |fileprivate |@\w+ )*(final )?(class|struct|enum|protocol|actor|extension) ", line):
                result.append(line)
                continue
            # Method signatures — keep the signature, skip the body
            if re.match(r"\s*(public |private |internal |fileprivate |@\w+ )*(static |class )?(func |def |init[?(]|deinit)", line):
                result.append(line)
                if "{" in line:
                    body_depth = line.count("{") - line.count("}")
                    if body_depth > 0:
                        skip_body = True
                continue
            if stripped.startswith("case "):
                result.append(line)
                continue

        if stripped == "}" and current_indent == indent:
            result.append(line)

    return "\n".join(result)


def enrich_bodies(graph: dict) -> int:
    """Add source_body (and truncated flag) to code nodes. Returns count enriched."""
    nodes = graph.get("nodes", [])
    edges = graph.get("links", [])
    code_nodes = [n for n in nodes if n.get("file_type") == "code"]
    enriched = 0

    # Pre-sort nodes by file and line for boundary detection
    by_file: dict[str, list[tuple[int, str]]] = {}
    for n in code_nodes:
        sf = n.get("source_file")
        start = _parse_start_line(n.get("source_location"))
        if sf and start is not None:
            by_file.setdefault(sf, []).append((start, n["id"]))
    for v in by_file.values():
        v.sort()

    file_cache: dict[str, list[str]] = {}

    for node in code_nodes:
        sf = node.get("source_file")
        loc = node.get("source_location")
        if not sf or not loc:
            continue

        start = _parse_start_line(loc)
        if start is None:
            continue

        if sf not in file_cache:
            lines = _read_file_lines(sf)
            if lines is None:
                continue
            file_cache[sf] = lines
        lines = file_cache[sf]

        if start >= len(lines):
            continue

        # Find end boundary: next node's start in same file
        file_nodes = by_file.get(sf, [])
        end_line = len(lines)
        for fstart, _fid in file_nodes:
            if fstart > start:
                end_line = fstart
                break

        is_container = _is_container_node(node["id"], edges)

        if start == 0:
            body = "\n".join(lines[:60])  # file-level node: first 60 lines
        elif is_container:
            body = _extract_structural_body(lines, start, end_line)
        else:
            body = "\n".join(lines[start:end_line])  # leaf: full body

        if body and len(body.strip()) > 10:
            truncated = len(body) > MAX_BODY_CHARS
            node["source_body"] = body[:MAX_BODY_CHARS]
            if truncated:
                node["source_body_truncated"] = True
            enriched += 1

    return enriched


# ---------------------------------------------------------------------------
# Step 2: Timestamps
# ---------------------------------------------------------------------------
def enrich_timestamps(graph: dict, manifest_path: Path | None = None) -> int:
    """Add captured_at and last_touched_at to all nodes. Returns count enriched."""
    nodes = graph.get("nodes", [])
    enriched = 0

    manifest: dict[str, float] = {}
    if manifest_path and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for node in nodes:
        ft = node.get("file_type", "")
        sf = node.get("source_file", "")

        if ft == "code":
            mtime = manifest.get(sf)
            if not mtime:
                p = Path(sf) if sf else None
                if p and p.exists():
                    mtime = p.stat().st_mtime
            if mtime:
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))
                node["last_touched_at"] = ts
                if "captured_at" not in node:
                    node["captured_at"] = ts

        elif ft == "observation":
            created = node.get("created_at")
            if created:
                node["captured_at"] = created
                node["last_touched_at"] = created

        elif ft == "document":
            if "captured_at" not in node:
                node["captured_at"] = now_iso
            node["last_touched_at"] = node.get("last_touched_at") or node["captured_at"]

        else:
            node.setdefault("captured_at", now_iso)
            node.setdefault("last_touched_at", now_iso)

        enriched += 1

    return enriched


# ---------------------------------------------------------------------------
# Step 3: Embeddings
# ---------------------------------------------------------------------------

def build_children_index(graph: dict) -> dict[str, list[str]]:
    """Build parent_id -> [child_labels] from contains/method edges."""
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    children: dict[str, list[str]] = {}
    for e in graph.get("links", []):
        if e.get("relation", "") in ("contains", "method"):
            src = e.get("source") or e.get("_src")
            tgt = e.get("target") or e.get("_tgt")
            if src and tgt and tgt in nodes:
                children.setdefault(src, []).append(nodes[tgt].get("label", tgt))
    return children


def build_embed_text(node: dict, children_index: dict[str, list[str]] | None = None,
                     project_root: Path | None = None) -> str:
    """Build the text to embed for a node based on its type.

    For container nodes (classes/structs with children), includes child names
    so the embedding carries the container's semantic identity, not just its
    type declaration.
    """
    ft = node.get("file_type", "")
    parts = []

    if ft == "code":
        parts.append(f"Code: {node.get('label', '')}")
        sf = node.get("source_file", "")
        if sf:
            rel = sf.replace(str(project_root) + "/", "") if project_root else sf
            parts.append(f"File: {rel}")
        body = node.get("source_body", "")
        if body:
            parts.append(body)
        child_labels = (children_index or {}).get(node["id"], [])
        if child_labels:
            parts.append("Members: " + ", ".join(child_labels))

    elif ft == "observation":
        parts.append(f"Observation ({node.get('observation_type', '')}): {node.get('label', '')}")
        narrative = node.get("narrative", "")
        if narrative:
            parts.append(narrative)

    elif ft == "document":
        parts.append(f"Document: {node.get('label', '')}")
        # Chunk-ingested documents carry their chunk text in source_body.
        # Without this, re-embedding here CLOBBERED the rich chunk embeddings
        # an ingester had stored, silently degrading document retrieval to
        # label-only matching.
        body = node.get("source_body", "")
        if body:
            parts.append(body)

    elif ft == "rationale":
        parts.append(f"Rationale: {node.get('label', '')}")
        body = node.get("source_body", "")
        if body:
            parts.append(body)

    else:
        parts.append(node.get("label", ""))

    return "\n".join(parts)


def embed_nodes(graph: dict, embedder: Embedder,
                project_root: Path | None = None,
                existing_hashes: dict[str, str] | None = None) -> tuple[int, int, list[dict]]:
    """Embed nodes. Returns (embedded, skipped, results).

    existing_hashes maps node_id -> text_hash of the stored embedding (for the
    current model); nodes whose embed text is unchanged are skipped, which
    makes re-running enrich after a rebuild cheap.
    """
    nodes = graph.get("nodes", [])
    children_index = build_children_index(graph)
    results = []
    embedded = 0
    skipped = 0
    total = len(nodes)
    t0 = time.time()

    for i, node in enumerate(nodes):
        text = build_embed_text(node, children_index, project_root)
        if len(text.strip()) < 5:
            continue

        th = _text_hash(text)
        if existing_hashes and existing_hashes.get(node["id"]) == th:
            skipped += 1
            continue

        emb = embedder.embed(text)
        if emb:
            results.append({
                "node_id": node["id"],
                "embedding": emb,
                "text": text[:500],
                "text_hash": th,
                "embed_model": embedder.model_name,
                "metadata": {
                    "file_type": node.get("file_type", "unknown"),
                    "label": node.get("label", ""),
                    "source_file": node.get("source_file", ""),
                    "community": node.get("community", -1),
                    "observation_type": node.get("observation_type", ""),
                    "captured_at": node.get("captured_at", ""),
                    "last_touched_at": node.get("last_touched_at", ""),
                },
            })
            embedded += 1

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (total - i - 1) / rate
            print(f"    Embedded {i+1}/{total} ({rate:.0f}/s, ETA {eta:.0f}s)")

    return embedded, skipped, results


# ---------------------------------------------------------------------------
# Vector store (SQLite) — schema shared with retrieve.py
# ---------------------------------------------------------------------------
def init_vector_store(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            node_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            embed_model TEXT NOT NULL,
            text TEXT,
            file_type TEXT,
            label TEXT,
            source_file TEXT,
            community INTEGER,
            observation_type TEXT,
            captured_at TEXT,
            last_touched_at TEXT,
            text_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:  # migrate pre-0.2 stores that lack the incremental-skip column
        conn.execute("ALTER TABLE embeddings ADD COLUMN text_hash TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_type ON embeddings(file_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_community ON embeddings(community)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_model ON embeddings(embed_model)")
    conn.commit()
    return conn


def store_embeddings(conn: sqlite3.Connection, results: list[dict]) -> int:
    stored = 0
    for r in results:
        meta = r["metadata"]
        conn.execute(
            """INSERT OR REPLACE INTO embeddings
               (node_id, embedding, embed_model, text, file_type, label,
                source_file, community, observation_type, captured_at,
                last_touched_at, text_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["node_id"],
                json.dumps(r["embedding"]).encode(),
                r["embed_model"],
                r["text"],
                meta.get("file_type"),
                meta.get("label"),
                meta.get("source_file"),
                meta.get("community"),
                meta.get("observation_type"),
                meta.get("captured_at"),
                meta.get("last_touched_at"),
                r.get("text_hash"),
            ),
        )
        stored += 1
    conn.commit()
    return stored


def load_existing_hashes(conn: sqlite3.Connection, model: str) -> dict[str, str]:
    """node_id -> text_hash for embeddings stored with this model."""
    try:
        rows = conn.execute(
            "SELECT node_id, text_hash FROM embeddings WHERE embed_model = ? AND text_hash IS NOT NULL",
            (model,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return dict(rows)


def prune_deleted_nodes(conn: sqlite3.Connection, graph: dict) -> int:
    """Remove embeddings for nodes no longer present in the graph."""
    live = {n["id"] for n in graph.get("nodes", [])}
    rows = conn.execute("SELECT node_id FROM embeddings").fetchall()
    stale = [r[0] for r in rows if r[0] not in live]
    for nid in stale:
        conn.execute("DELETE FROM embeddings WHERE node_id = ?", (nid,))
    conn.commit()
    return len(stale)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def print_stats(graph: dict, vectors_db: Path) -> None:
    nodes = graph.get("nodes", [])
    code_nodes = [n for n in nodes if n.get("file_type") == "code"]
    with_body = [n for n in code_nodes if n.get("source_body")]
    truncated = [n for n in code_nodes if n.get("source_body_truncated")]
    with_ts = [n for n in nodes if n.get("captured_at")]

    print(f"Graph nodes: {len(nodes)}")
    print(f"  Code: {len(code_nodes)} ({len(with_body)} with body, {len(truncated)} truncated)")
    print(f"  Observations: {len([n for n in nodes if n.get('file_type') == 'observation'])}")
    print(f"  Documents: {len([n for n in nodes if n.get('file_type') == 'document'])}")
    print(f"  With timestamps: {len(with_ts)}")

    if vectors_db.exists():
        conn = sqlite3.connect(str(vectors_db))
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        types = conn.execute("SELECT file_type, COUNT(*) FROM embeddings GROUP BY file_type").fetchall()
        models = conn.execute("SELECT embed_model, COUNT(*) FROM embeddings GROUP BY embed_model").fetchall()
        conn.close()
        print(f"\nVector store: {vectors_db} ({vectors_db.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"  Total embeddings: {count}")
        for t, c in types:
            print(f"    {t}: {c}")
        for m, c in models:
            print(f"  Model: {m} ({c} embeddings)")
    else:
        print("\nVector store: not yet created")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(
    project_path: Path,
    *,
    bodies_only: bool = False,
    embed_only: bool = False,
    stats_only: bool = False,
    full: bool = False,
    model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = OLLAMA_URL,
) -> int:
    """Run the enrichment pipeline against <project_path>/graphify-out/.

    Steps: source bodies -> timestamps -> embeddings into vectors.db.
    Embedding is incremental: nodes whose embed text is unchanged since the
    last run are skipped (pass full=True to re-embed everything); embeddings
    for deleted nodes are pruned.
    Embeddings need a running ollama with the model pulled; the first two
    steps are pure-local and run without it.
    """
    project_path = Path(project_path).resolve()
    out = project_path / "graphify-out"
    graph_path = out / "graph.json"
    vectors_db = out / "vectors.db"
    manifest_path = out / "manifest.json"

    if not graph_path.exists():
        print(f"error: graph file not found: {graph_path} — run /paragraph first", file=sys.stderr)
        return 1

    print(f"Loading graph from {graph_path} ...")
    graph = json.loads(graph_path.read_text())

    if stats_only:
        print_stats(graph, vectors_db)
        return 0

    if not embed_only:
        print("\n--- Step 1: Extracting source bodies ---")
        enriched = enrich_bodies(graph)
        print(f"  Enriched {enriched} code nodes with source bodies")

        print("\n--- Step 2: Adding timestamps ---")
        ts_count = enrich_timestamps(graph, manifest_path)
        print(f"  Timestamped {ts_count} nodes")

        graph_path.write_text(json.dumps(graph, indent=2))
        print(f"  Saved to {graph_path}")

    if not bodies_only:
        print("\n--- Step 3: Embedding nodes ---")
        embedder = Embedder(model=model, url=ollama_url)
        if not embedder.healthy():
            print("  ERROR: embedder not responding. Start ollama: ollama serve", file=sys.stderr)
            return 1
        print(f"  Model: {embedder.model_name} ({embedder.dims}d)")

        conn = init_vector_store(vectors_db)
        existing = {} if full else load_existing_hashes(conn, embedder.model_name)
        embedded, skipped, results = embed_nodes(
            graph, embedder, project_root=project_path, existing_hashes=existing)
        print(f"  Embedded {embedded} nodes ({skipped} unchanged, skipped)")

        stored = store_embeddings(conn, results)
        pruned = prune_deleted_nodes(conn, graph)
        conn.close()
        print(f"  Stored {stored} embeddings" + (f", pruned {pruned} stale" if pruned else ""))

    print()
    print_stats(graph, vectors_db)
    return 0
