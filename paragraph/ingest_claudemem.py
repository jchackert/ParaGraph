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
# Filtering configuration
#
# All project-specific vocabulary (domain keywords, reviewer names, ticket
# prefixes) lives in an IngestConfig, loadable from JSON. Defaults are
# generic; see docs/examples/paranote-ingest.json for a fully-tuned example.
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field


@dataclass
class IngestConfig:
    # Signal keywords: +2.0 each, capped at +4.0 total
    signal_keywords: list[str] = field(default_factory=lambda: [
        "architectural", "design", "decision", "pattern", "constraint", "principle",
        "binding", "ruling", "rationale", "root cause", "must not", "never", "always",
    ])
    # Domain/product keywords: +2.0 each, capped at +4.0 total (project-specific)
    domain_keywords: list[str] = field(default_factory=list)
    # Noise title patterns (regex strings): -2.0 each
    noise_title_patterns: list[str] = field(default_factory=lambda: [
        r"\btest suite\b", r"\bpassing\b", r"\btests pass\b", r"\bgreen\b",
        r"\bstandup dispatched\b", r"\bstandup orchestration\b",
        r"\bstandup guardrail\b", r"\bstandup guard\b", r"\bstandup skill\b",
        r"\bstandup discipline\b", r"\bassigned to\b", r"\btypo fixed\b",
        r"\bmoved to\b", r"\bmarked complete\b", r"^\[\*\*title\*\*",
        r"\breference sweep\b",
    ])
    noise_narrative_prefixes: list[str] = field(default_factory=lambda: [
        "During standup", "While waiting",
    ])
    score_threshold: float = 4.0
    dedup_jaccard_threshold: float = 0.5
    # Passthrough vocabulary (always inject regardless of score)
    reviewers: list[str] = field(default_factory=list)
    passthrough_phrases: list[str] = field(default_factory=lambda: ["code review ruling"])
    ticket_patterns: list[str] = field(default_factory=list)
    ticket_keywords: list[str] = field(default_factory=lambda: ["ticket"])
    passthrough_file_substrings: list[str] = field(default_factory=list)
    decision_keywords: list[str] = field(default_factory=lambda: [
        "architecture", "design pattern",
    ])
    # Names stripped from titles before trigram dedup (agent/reviewer names)
    dedup_ignore_names: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "IngestConfig":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            print(f"warning: ignoring unknown ingest-config keys: {sorted(unknown)}",
                  file=sys.stderr)
        return cls(**{k: v for k, v in data.items() if k in known})

    def compiled_noise_patterns(self) -> list:
        return [re.compile(p, re.IGNORECASE) for p in self.noise_title_patterns]

    def compiled_ticket_patterns(self) -> list:
        return [re.compile(p, re.IGNORECASE) for p in self.ticket_patterns]


DEFAULT_CONFIG = IngestConfig()


def load_ingest_config(project_path: Path, config_path: Path | None = None) -> IngestConfig:
    """Load the ingest config: explicit path, else <project>/graphify-out/ingest-config.json,
    else ~/.paragraph/ingest-config.json, else generic defaults."""
    candidates = [config_path] if config_path else [
        project_path / "graphify-out" / "ingest-config.json",
        Path.home() / ".paragraph" / "ingest-config.json",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            print(f"Using ingest config: {candidate}")
            return IngestConfig.from_dict(json.loads(candidate.read_text()))
    if config_path:
        raise FileNotFoundError(f"ingest config not found: {config_path}")
    return IngestConfig()


# ---------------------------------------------------------------------------
# Pass-through rules (always inject regardless of score)
# ---------------------------------------------------------------------------
def _reviewer_phrases(name: str) -> list[str]:
    n = name.lower()
    return [
        f"{n} ruling", f"{n} requires", f"{n} must", f"{n} veto",
        f"{n} signed off", f"{n} approved", f"{n} accepted",
        f"{n}'s review", f"{n}'s feedback", f"{n}'s fix", f"{n} review",
    ]


def _is_passthrough(obs: dict, cfg: IngestConfig) -> bool:
    """Return True if this observation must always be injected."""
    title = (obs.get("title") or "").lower()
    narrative = (obs.get("narrative") or "").lower()
    files_modified = obs.get("files_modified") or ""
    text_lower = title + " " + narrative

    # Reviewer ruling/veto/feedback that was accepted or applied
    for reviewer in cfg.reviewers:
        if reviewer.lower() in text_lower and any(
            phrase in text_lower for phrase in _reviewer_phrases(reviewer)
        ):
            return True
    if any(phrase in text_lower for phrase in cfg.passthrough_phrases):
        return True

    # Ticket-ID formats (exact patterns, not substring matches)
    if any(p.search(text_lower) for p in cfg.compiled_ticket_patterns()):
        return True

    # Modification of always-keep file paths
    modified_list = _parse_json_list(files_modified)
    if any(
        sub in f for f in modified_list for sub in cfg.passthrough_file_substrings
    ):
        return True

    # Ticket work with resolution context (preserves what was decided and why)
    if any(word in text_lower for word in cfg.ticket_keywords) and any(
        word in text_lower for word in (
            "created", "resolved", "closed", "fixed", "shipped",
            "plan", "approved", "marked done", "completed",
        )
    ):
        return True

    # Architecture, design, or pattern decisions with codebase impact
    if obs.get("type") == "decision" and any(
        word in text_lower for word in cfg.decision_keywords
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Hard stop rules (never inject regardless of score)
# ---------------------------------------------------------------------------
def _is_hard_stop(obs: dict, cfg: IngestConfig) -> bool:
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
def filter_observation(obs: dict, cfg: IngestConfig = DEFAULT_CONFIG) -> tuple[bool, float, str]:
    """
    Evaluate one observation.

    Returns (keep: bool, score: float, reason: str).
    """
    title = obs.get("title") or ""
    obs_type = obs.get("type") or ""
    narrative = obs.get("narrative") or ""
    files_modified = _parse_json_list(obs.get("files_modified"))

    # Hard stops first
    if _is_hard_stop(obs, cfg):
        return False, 0.0, "hard_stop"

    # Pass-throughs bypass scoring
    if _is_passthrough(obs, cfg):
        return True, 99.0, "passthrough"

    score = 0.0
    reason_parts = []

    # --- Signal keywords (max +4.0) ---
    text_lower = (title + " " + narrative).lower()
    signal_hits = sum(1 for kw in cfg.signal_keywords if kw in text_lower)
    signal_bonus = min(signal_hits * 2.0, 4.0)
    if signal_bonus:
        score += signal_bonus
        reason_parts.append(f"signal+{signal_bonus:.1f}({signal_hits}hits)")

    # --- Domain/product keywords (max +4.0) ---
    domain_hits = sum(1 for kw in cfg.domain_keywords if kw in text_lower)
    domain_bonus = min(domain_hits * 2.0, 4.0)
    if domain_bonus:
        score += domain_bonus
        reason_parts.append(f"domain+{domain_bonus:.1f}({domain_hits}hits)")

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
    title_penalties = sum(1 for p in cfg.compiled_noise_patterns() if p.search(title))
    noise_from_title = title_penalties * 2.0
    if noise_from_title:
        score -= noise_from_title
        reason_parts.append(f"title_noise-{noise_from_title:.1f}({title_penalties}hits)")

    narrative_stripped = narrative.lstrip()
    narrative_penalties = sum(
        1 for prefix in cfg.noise_narrative_prefixes
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

    keep = score >= cfg.score_threshold
    reason = ",".join(reason_parts) if reason_parts else "no_signal"
    return keep, score, reason


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def _normalize_title(title: str, cfg: IngestConfig = DEFAULT_CONFIG) -> str:
    """Lowercase, remove punctuation, remove configured agent/reviewer names."""
    ignore = {n.lower() for n in cfg.dedup_ignore_names}
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    words = [w for w in t.split() if w not in ignore]
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
    cfg: IngestConfig = DEFAULT_CONFIG,
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
        tg[oid] = _trigrams(_normalize_title(obs.get("title") or "", cfg))

    ids = [str(o["id"]) for o in observations]
    dropped: set[str] = set()

    for i, oid_a in enumerate(ids):
        if oid_a in dropped:
            continue
        for oid_b in ids[i+1:]:
            if oid_b in dropped:
                continue
            sim = _jaccard(tg[oid_a], tg[oid_b])
            if sim > cfg.dedup_jaccard_threshold:
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
# Node lookup indexes from the existing graph
# ---------------------------------------------------------------------------
def _norm_path(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./").lower()


def build_path_index(nodes: list[dict]) -> dict[str, list[str]]:
    """Normalized source_file path -> [node_ids] for every node with a source."""
    index: dict[str, list[str]] = {}
    for node in nodes:
        sf = node.get("source_file")
        if sf and sf != "<synthesized>":
            index.setdefault(_norm_path(sf), []).append(node["id"])
    return index


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
# Map a file path from an observation to the best-matching graph node ID
# ---------------------------------------------------------------------------
def file_path_to_node_id(
    file_path: str,
    stem_index: dict[str, list[str]],
    path_index: dict[str, list[str]] | None = None,
) -> str | None:
    """Resolve an observation's file path to a graph node.

    Tries, in order: exact normalized source_file match; unambiguous path
    suffix match (either path a suffix of the other at a '/' boundary);
    filename-stem match as the legacy fallback. Stem-only matching mis-links
    when two directories contain same-named files, so path matches win.
    """
    if path_index:
        norm = _norm_path(file_path)
        candidates = path_index.get(norm)
        if candidates:
            return shortest_match(candidates)
        suffix_hits = [
            ids for p, ids in path_index.items()
            if p.endswith("/" + norm) or norm.endswith("/" + p)
        ]
        if len(suffix_hits) == 1:
            return shortest_match(suffix_hits[0])
        if len(suffix_hits) > 1:
            return None  # ambiguous across directories — do not guess

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
    obs: dict,
    stem_index: dict[str, list[str]],
    path_index: dict[str, list[str]] | None = None,
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
        target = file_path_to_node_id(file_path, stem_index, path_index)
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
    config_path: Path | None = None,
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
    cfg = load_ingest_config(project_path, config_path)

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
    path_index = build_path_index(existing_nodes)
    print(f"Indexes built: {len(path_index)} source paths, {len(stem_index)} stems "
          f"across {len(existing_nodes)} nodes.")

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
        keep, score, reason = filter_observation(obs, cfg)
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
    deduped_obs, dedup_log = deduplicate(kept_obs, scores, cfg)
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
        edges, skipped = observation_to_edges(obs, stem_index, path_index)
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
