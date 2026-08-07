# inject claude-mem observations (decision/bugfix/feature/change/refactor)
# into an existing graphify-out/graph.json as first-class observation nodes.
#
# Ported from PARA_Note scripts/claude-mem-to-graphify.py — scoring-based
# filtering, trigram deduplication, and idempotent re-injection (existing
# claudemem_* nodes and their links are stripped before each run).
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".claude-mem" / "claude-mem.db"
TARGET_TYPES = ("decision", "bugfix", "feature", "change", "refactor")

# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------

# Signal keywords: +2.0 each, capped at +4.0 total
_SIGNAL_KEYWORDS = [
    "architectural", "design", "decision", "pattern", "constraint", "principle",
    "binding", "ruling", "rationale", "root cause", "must not", "never", "always",
]

# Clinical/product keywords: +2.0 each, capped at +4.0 total
_CLINICAL_KEYWORDS = [
    "carol", "clinical", "adhd", "para state", "ubiquitous language",
    "scope decision", "byok", "subscription", "layer 1", "layer 2", "layer 3",
    "apple intelligence",
]

# Noise title patterns: -2.0 each
_NOISE_TITLE_PATTERNS = [
    re.compile(r"\btest suite\b", re.IGNORECASE),
    re.compile(r"\bpassing\b", re.IGNORECASE),
    re.compile(r"\btests pass\b", re.IGNORECASE),
    re.compile(r"\bgreen\b", re.IGNORECASE),
    re.compile(r"\bstandup dispatched\b", re.IGNORECASE),
    re.compile(r"\bstandup orchestration\b", re.IGNORECASE),
    re.compile(r"\bstandup guardrail\b", re.IGNORECASE),
    re.compile(r"\bstandup guard\b", re.IGNORECASE),
    re.compile(r"\bstandup skill\b", re.IGNORECASE),
    re.compile(r"\bstandup discipline\b", re.IGNORECASE),
    re.compile(r"\bassigned to\b", re.IGNORECASE),
    re.compile(r"\btypo fixed\b", re.IGNORECASE),
    re.compile(r"\bmoved to\b", re.IGNORECASE),
    re.compile(r"\bmarked complete\b", re.IGNORECASE),
    re.compile(r"^\[\*\*title\*\*", re.IGNORECASE),
    re.compile(r"\breference sweep\b", re.IGNORECASE),
]

# Noise narrative prefixes
_NOISE_NARRATIVE_PREFIXES = [
    "During standup",
    "While waiting",
]

# Score threshold for inclusion
_SCORE_THRESHOLD = 4.0

# Jaccard deduplication threshold on title 3-grams
_DEDUP_JACCARD_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Pass-through rules (always inject regardless of score)
# ---------------------------------------------------------------------------
def _is_passthrough(obs: dict) -> bool:
    """Return True if this observation must always be injected."""
    title = (obs.get("title") or "").lower()
    narrative = (obs.get("narrative") or "").lower()
    files_modified = obs.get("files_modified") or ""
    text_lower = title + " " + narrative

    # Carol ruling, veto, or clinical feedback that was accepted/applied
    has_carol_signal = "carol" in text_lower and any(
        phrase in text_lower for phrase in (
            "ruling", "carol requires", "carol must", "binding",
            "carol veto", "carol signed off", "carol approved",
            "carol's review", "carol's feedback", "carol's fix",
            "clinical ruling",
        )
    )
    # CA-N ticket format (clinical audit tickets, not substring matches)
    has_ca_ticket = bool(re.search(r"\bca-\d+\b", text_lower))
    if has_carol_signal or has_ca_ticket:
        return True

    # Sam's code review feedback that was accepted/applied
    if any(
        phrase in text_lower for phrase in (
            "sam's review", "sam's feedback", "sam review",
            "sam approved", "sam accepted", "sam signed off",
            "code review ruling", "sam's code review",
        )
    ):
        return True

    # Clinical file modification
    modified_list = _parse_json_list(files_modified)
    if any("docs/clinical/" in f for f in modified_list):
        return True

    # Ticket work with resolution context (preserves what was decided/planned and why)
    if any(word in text_lower for word in ("ticket", "cw-", "paranote-")) and any(
        word in text_lower for word in (
            "created", "resolved", "closed", "fixed", "shipped",
            "plan", "approved", "marked done", "completed",
        )
    ):
        return True

    # Architecture, design, or pattern decisions with codebase impact
    if obs.get("type") == "decision" and any(
        word in text_lower for word in (
            "architecture", "design pattern", "layer 1", "layer 2", "layer 3",
            "swiftdata", "intelligence stack", "classification",
            "capture flow", "consent flow", "offline",
        )
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Hard stop rules (never inject regardless of score)
# ---------------------------------------------------------------------------
def _is_hard_stop(obs: dict) -> bool:
    """Return True if this observation must never be injected."""
    title = obs.get("title") or ""
    obs_type = obs.get("type") or ""
    narrative = obs.get("narrative") or ""

    # Template artifact pattern
    if re.match(r"^\[\*\*title\*\*", title, re.IGNORECASE):
        return True

    # Bugfix about test suite passing (no code changes)
    if obs_type == "bugfix":
        if (
            re.search(r"\btest suite\b", title, re.IGNORECASE)
            and re.search(r"\bpassing\b", title, re.IGNORECASE)
        ):
            return True

    # Narrative too short
    if len(narrative.strip()) < 50:
        return True

    return False


# ---------------------------------------------------------------------------
# Scoring function
# ---------------------------------------------------------------------------
def filter_observation(obs: dict) -> tuple[bool, float, str]:
    """
    Evaluate one observation.

    Returns (keep: bool, score: float, reason: str).
    """
    title = obs.get("title") or ""
    obs_type = obs.get("type") or ""
    narrative = obs.get("narrative") or ""
    files_modified = _parse_json_list(obs.get("files_modified"))

    # Hard stops first
    if _is_hard_stop(obs):
        return False, 0.0, "hard_stop"

    # Pass-throughs bypass scoring
    if _is_passthrough(obs):
        return True, 99.0, "passthrough"

    score = 0.0
    reason_parts = []

    # --- Signal keywords (max +4.0) ---
    text_lower = (title + " " + narrative).lower()
    signal_hits = sum(1 for kw in _SIGNAL_KEYWORDS if kw in text_lower)
    signal_bonus = min(signal_hits * 2.0, 4.0)
    if signal_bonus:
        score += signal_bonus
        reason_parts.append(f"signal+{signal_bonus:.1f}({signal_hits}hits)")

    # --- Clinical/product keywords (max +4.0) ---
    clinical_hits = sum(1 for kw in _CLINICAL_KEYWORDS if kw in text_lower)
    clinical_bonus = min(clinical_hits * 2.0, 4.0)
    if clinical_bonus:
        score += clinical_bonus
        reason_parts.append(f"clinical+{clinical_bonus:.1f}({clinical_hits}hits)")

    # --- Bug quality (+1.5 if "fixed"/"root cause" AND files_modified non-empty) ---
    if obs_type == "bugfix" and files_modified:
        if "fixed" in text_lower or "root cause" in text_lower:
            score += 1.5
            reason_parts.append("bugquality+1.5")

    # --- Type bonus ---
    if obs_type == "decision":
        score += 3.0
        reason_parts.append("decision+3.0")
    elif obs_type == "feature":
        score += 2.0
        reason_parts.append("feature+2.0")
    elif obs_type == "refactor":
        if files_modified:
            score += 1.5
            reason_parts.append("refactor_with_code+1.5")
        else:
            score += 0.5
            reason_parts.append("refactor_no_code+0.5")
    elif obs_type == "change":
        if files_modified:
            score += 1.0
            reason_parts.append("change_with_code+1.0")
        else:
            score += 0.0
            reason_parts.append("change_no_code+0.0")
    elif obs_type == "bugfix":
        if files_modified:
            score += 1.5
            reason_parts.append("bugfix_with_code+1.5")
        else:
            score += 0.5
            reason_parts.append("bugfix_no_code+0.5")

    # --- Noise penalties (-2.0 each) ---
    title_penalties = sum(1 for p in _NOISE_TITLE_PATTERNS if p.search(title))
    noise_from_title = title_penalties * 2.0
    if noise_from_title:
        score -= noise_from_title
        reason_parts.append(f"title_noise-{noise_from_title:.1f}({title_penalties}hits)")

    narrative_stripped = narrative.lstrip()
    narrative_penalties = sum(
        1 for prefix in _NOISE_NARRATIVE_PREFIXES
        if narrative_stripped.startswith(prefix)
    )
    noise_from_narrative = narrative_penalties * 2.0
    if noise_from_narrative:
        score -= noise_from_narrative
        reason_parts.append(f"narrative_noise-{noise_from_narrative:.1f}")

    # --- Length bonus (+0.5 if narrative > 200 chars) ---
    if len(narrative) > 200:
        score += 0.5
        reason_parts.append("length+0.5")

    keep = score >= _SCORE_THRESHOLD
    reason = ",".join(reason_parts) if reason_parts else "no_signal"
    return keep, score, reason


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def _normalize_title(title: str) -> str:
    """Lowercase, remove punctuation, remove known agent names."""
    agent_names = {
        "sam", "greg", "peter", "alice", "tiger", "cindy", "bobby",
        "mike", "carol", "jordan",
    }
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    words = [w for w in t.split() if w not in agent_names]
    return " ".join(words)


def _trigrams(text: str) -> set[str]:
    """Return the set of character 3-grams for a string."""
    if len(text) < 3:
        return {text}
    return {text[i:i+3] for i in range(len(text) - 2)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def deduplicate(
    observations: list[dict],
    scores: dict[str, float],
) -> tuple[list[dict], list[str]]:
    """
    Given observations and their scores, remove duplicates.

    Returns (kept_list, dedup_log_lines).
    """
    dedup_log: list[str] = []
    # Build trigram sets for each obs
    tg: dict[str, set[str]] = {}
    for obs in observations:
        oid = str(obs["id"])
        tg[oid] = _trigrams(_normalize_title(obs.get("title") or ""))

    ids = [str(o["id"]) for o in observations]
    dropped: set[str] = set()

    for i, oid_a in enumerate(ids):
        if oid_a in dropped:
            continue
        for oid_b in ids[i+1:]:
            if oid_b in dropped:
                continue
            sim = _jaccard(tg[oid_a], tg[oid_b])
            if sim > _DEDUP_JACCARD_THRESHOLD:
                # Keep the higher-scored one
                score_a = scores.get(oid_a, 0.0)
                score_b = scores.get(oid_b, 0.0)
                if score_a >= score_b:
                    loser = oid_b
                    winner = oid_a
                else:
                    loser = oid_a
                    winner = oid_b
                dropped.add(loser)
                dedup_log.append(
                    f"  dedup: dropped {loser} (score={scores.get(loser,0):.1f}) "
                    f"in favor of {winner} (score={scores.get(winner,0):.1f}), "
                    f"jaccard={sim:.2f}"
                )

    kept = [o for o in observations if str(o["id"]) not in dropped]
    return kept, dedup_log


# ---------------------------------------------------------------------------
# Load existing graph
# ---------------------------------------------------------------------------
def load_graph(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Build a fast stem → [node_id] lookup from the existing graph
# ---------------------------------------------------------------------------
def build_stem_index(nodes: list[dict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for node in nodes:
        nid = node["id"]
        stem = nid.split("_")[0].lower()
        index.setdefault(stem, []).append(nid)
    return index


def shortest_match(candidates: list[str]) -> str:
    return min(candidates, key=len)


# ---------------------------------------------------------------------------
# Map a relative file path to the best-matching graph node ID
# ---------------------------------------------------------------------------
def file_path_to_node_id(
    file_path: str, stem_index: dict[str, list[str]]
) -> str | None:
    stem = Path(file_path).stem.lower()
    candidates = stem_index.get(stem)
    if not candidates:
        return None
    return shortest_match(candidates)


# ---------------------------------------------------------------------------
# Fetch observations from claude-mem
# ---------------------------------------------------------------------------
def fetch_observations(db_path: Path, project: str, types: tuple) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"""
        SELECT id, title, subtitle, narrative, facts, concepts,
               files_read, files_modified, type, agent_id, agent_type, created_at
        FROM observations
        WHERE project = ? AND type IN ({placeholders})
        ORDER BY created_at ASC
        """,
        (project, *types),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Parse JSON array fields safely
# ---------------------------------------------------------------------------
def _parse_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Build node + edges for one observation
# ---------------------------------------------------------------------------
def observation_to_node(obs: dict) -> dict:
    narrative = obs.get("narrative") or ""
    return {
        "id": f"claudemem_{obs['id']}",
        "label": obs.get("title") or f"Observation {obs['id']}",
        "file_type": "observation",
        "source_file": None,
        "source_location": None,
        "observation_type": obs["type"],
        "narrative": narrative[:200],
        "agent": obs.get("agent_type") or obs.get("agent_id") or "unknown",
        "created_at": obs.get("created_at"),
    }


def observation_to_edges(
    obs: dict, stem_index: dict[str, list[str]]
) -> tuple[list[dict], int]:
    files_read = _parse_json_list(obs.get("files_read"))
    files_modified = _parse_json_list(obs.get("files_modified"))

    node_id = f"claudemem_{obs['id']}"
    edges: list[dict] = []
    skipped = 0

    seen: dict[str, str] = {}
    for f in files_read:
        seen[f] = "investigated"
    for f in files_modified:
        seen[f] = "modified"

    for file_path, relation in seen.items():
        target = file_path_to_node_id(file_path, stem_index)
        if target is None:
            skipped += 1
            continue
        edges.append(
            {
                "source": node_id,
                "target": target,
                "relation": relation,
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": None,
                "weight": 1.0,
            }
        )

    return edges, skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(
    project_path: Path,
    db_path: Path | None = None,
    graph_path: Path | None = None,
    project: str | None = None,
) -> int:
    """
    Inject claude-mem observations into a graph.json.

    - project_path: repo root; its basename is the claude-mem project name
      unless `project` is given explicitly.
    - db_path: claude-mem SQLite DB (default ~/.claude-mem/claude-mem.db).
    - graph_path: graph to update (default <project_path>/graphify-out/graph.json).

    Returns an exit code. A missing DB is a graceful skip (0, matching
    graphify-rebuild.sh behavior); a missing graph is an error (1).
    Safe to run repeatedly: existing claudemem_* nodes and their links
    are removed before re-injection.
    """
    project_path = Path(project_path).resolve()
    db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    graph_path = Path(graph_path) if graph_path else project_path / "graphify-out" / "graph.json"
    target_project = project or project_path.name

    if not db_path.exists():
        print(f"claude-mem DB not found at {db_path} — skipping injection.")
        return 0

    if not graph_path.exists():
        print(f"error: graph file not found: {graph_path} — run /paragraph first", file=sys.stderr)
        return 1

    print(f"Loading graph from {graph_path} ...")
    graph = load_graph(graph_path)
    existing_nodes: list[dict] = graph.get("nodes", [])
    existing_links: list[dict] = graph.get("links", [])

    # Remove stale claudemem nodes/links so the injection is idempotent
    stale_nodes = {n["id"] for n in existing_nodes if n["id"].startswith("claudemem_")}
    if stale_nodes:
        print(f"Removing {len(stale_nodes)} stale claudemem nodes before re-injection.")
    existing_nodes = [n for n in existing_nodes if n["id"] not in stale_nodes]
    existing_links = [
        lnk for lnk in existing_links
        if lnk.get("source") not in stale_nodes and lnk.get("target") not in stale_nodes
    ]

    stem_index = build_stem_index(existing_nodes)
    print(f"Stem index built: {len(stem_index)} unique stems across {len(existing_nodes)} nodes.")

    print(f"Fetching observations from {db_path} ...")
    raw_observations = fetch_observations(db_path, target_project, TARGET_TYPES)
    print(f"Found {len(raw_observations)} raw observations (types: {TARGET_TYPES}, project: {target_project}).")

    type_counts = Counter(o["type"] for o in raw_observations)
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

    # -----------------------------------------------------------------------
    # Filtering pass
    # -----------------------------------------------------------------------
    print()
    print("--- Filtering ---")
    kept_obs: list[dict] = []
    scores: dict[str, float] = {}
    filter_reasons: Counter = Counter()

    for obs in raw_observations:
        keep, score, reason = filter_observation(obs)
        oid = str(obs["id"])
        scores[oid] = score
        if keep:
            kept_obs.append(obs)
            filter_reasons["kept"] += 1
            if reason == "passthrough":
                filter_reasons["kept_passthrough"] += 1
        else:
            filter_reasons["filtered"] += 1
            # Bin the reason for reporting
            first_reason = reason.split(",")[0] if reason else "unknown"
            filter_reasons[f"filtered:{first_reason}"] += 1
            print(
                f"  FILTERED id={oid} score={score:.1f} reason={reason} "
                f"title={repr((obs.get('title') or '')[:60])}",
                file=sys.stderr,
            )

    print(f"  Raw observations   : {len(raw_observations)}")
    print(f"  Passed filter      : {len(kept_obs)}")
    print(f"  Filtered out       : {filter_reasons['filtered']}")
    print(f"  Passthroughs       : {filter_reasons['kept_passthrough']}")

    # -----------------------------------------------------------------------
    # Deduplication pass
    # -----------------------------------------------------------------------
    print()
    print("--- Deduplication ---")
    deduped_obs, dedup_log = deduplicate(kept_obs, scores)
    deduped_count = len(kept_obs) - len(deduped_obs)

    for line in dedup_log:
        print(line, file=sys.stderr)

    print(f"  After dedup        : {len(deduped_obs)} (removed {deduped_count} duplicates)")

    # -----------------------------------------------------------------------
    # Injection pass
    # -----------------------------------------------------------------------
    new_nodes: list[dict] = []
    new_edges: list[dict] = []
    total_skipped = 0

    for obs in deduped_obs:
        node = observation_to_node(obs)
        new_nodes.append(node)
        edges, skipped = observation_to_edges(obs, stem_index)
        new_edges.extend(edges)
        total_skipped += skipped

    # Merge into graph
    graph["nodes"] = existing_nodes + new_nodes
    graph["links"] = existing_links + new_edges

    graph_path.write_text(json.dumps(graph, indent=2))

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    print("Injection complete.")
    print(f"  Observations injected    : {len(new_nodes)}")
    print(f"  Edges created            : {len(new_edges)}")
    print(f"  Edges skipped (no match) : {total_skipped}")
    print(f"  Total nodes now          : {len(graph['nodes'])}")
    print(f"  Total links now          : {len(graph['links'])}")
    print()
    print("Filter stats by reason:")
    for key in sorted(filter_reasons.keys()):
        if key.startswith("filtered:"):
            print(f"  {key}: {filter_reasons[key]}")

    return 0
