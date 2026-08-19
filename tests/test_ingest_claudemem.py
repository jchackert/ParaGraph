"""Tests for paragraph.ingest_claudemem — claude-mem observation injection."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from paragraph.ingest_claudemem import run


PROJECT = "SampleProject"


def _make_db(path: Path, rows: list[dict]) -> Path:
    """Create a claude-mem-shaped SQLite DB with the given observation rows.

    Schema derived from the columns the injection SQL selects/filters on.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            project TEXT,
            title TEXT,
            subtitle TEXT,
            narrative TEXT,
            facts TEXT,
            concepts TEXT,
            files_read TEXT,
            files_modified TEXT,
            type TEXT,
            agent_id TEXT,
            agent_type TEXT,
            created_at TEXT
        )
        """
    )
    for r in rows:
        conn.execute(
            """
            INSERT INTO observations
                (id, project, title, subtitle, narrative, facts, concepts,
                 files_read, files_modified, type, agent_id, agent_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["id"], r.get("project", PROJECT), r.get("title", ""),
                r.get("subtitle", ""), r.get("narrative", ""), r.get("facts", "[]"),
                r.get("concepts", "[]"), r.get("files_read", "[]"),
                r.get("files_modified", "[]"), r.get("type", "decision"),
                r.get("agent_id", "main"), r.get("agent_type", "main"),
                r.get("created_at", "2026-08-01T00:00:00Z"),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _make_graph(path: Path) -> Path:
    """Write a minimal graph.json with one code node for edge matching."""
    graph = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {
                "id": "capturestore_swift_code",
                "label": "CaptureStore",
                "file_type": "code",
                "source_file": "Sources/CaptureStore.swift",
            },
            {
                "id": "boidsim_swift_code",
                "label": "BoidSim",
                "file_type": "code",
                "source_file": "Sources/BoidSim.swift",
            },
        ],
        "links": [
            {
                "source": "capturestore_swift_code",
                "target": "boidsim_swift_code",
                "relation": "imports",
                "confidence": "EXTRACTED",
                "source_file": "Sources/CaptureStore.swift",
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph))
    return path


def _default_rows() -> list[dict]:
    """Two observations that pass the score filter, with distinct titles."""
    return [
        {
            "id": 1,
            "title": "Offline capture queue architecture chosen",
            "narrative": (
                "Architectural decision: the capture queue persists locally and "
                "syncs later. Rationale: offline-first is a hard constraint."
            ),
            "type": "decision",
            "files_modified": json.dumps(["Sources/CaptureStore.swift"]),
        },
        {
            "id": 2,
            "title": "Boid separation weighting rebalanced for calm motion",
            "narrative": (
                "Design change to the boid simulation: separation weight raised, "
                "cohesion lowered, following the calm-motion design principle."
            ),
            "type": "feature",
            "files_read": json.dumps(["Sources/BoidSim.swift"]),
        },
    ]


def _setup(tmp_path: Path, rows: list[dict] | None = None):
    project = tmp_path / PROJECT
    project.mkdir()
    graph_path = _make_graph(project / "graphify-out" / "graph.json")
    db_path = _make_db(tmp_path / "claude-mem.db", rows if rows is not None else _default_rows())
    return project, db_path, graph_path


def _claudemem_nodes(graph_path: Path) -> list[dict]:
    graph = json.loads(graph_path.read_text())
    return [n for n in graph["nodes"] if n["id"].startswith("claudemem_")]


def test_nodes_created_with_observation_file_type(tmp_path):
    project, db, graph_path = _setup(tmp_path)
    rc = run(project, db_path=db)
    assert rc == 0
    nodes = _claudemem_nodes(graph_path)
    assert {n["id"] for n in nodes} == {"claudemem_1", "claudemem_2"}
    assert all(n["file_type"] == "observation" for n in nodes)


def test_node_carries_observation_metadata(tmp_path):
    project, db, graph_path = _setup(tmp_path)
    run(project, db_path=db)
    node = next(n for n in _claudemem_nodes(graph_path) if n["id"] == "claudemem_1")
    assert node["label"] == "Offline capture queue architecture chosen"
    assert node["observation_type"] == "decision"
    assert node["agent"] == "main"
    assert node["source_file"] is None


def test_edges_link_to_matching_code_nodes(tmp_path):
    project, db, graph_path = _setup(tmp_path)
    run(project, db_path=db)
    graph = json.loads(graph_path.read_text())
    cm_links = [l for l in graph["links"] if str(l["source"]).startswith("claudemem_")]
    by_pair = {(l["source"], l["target"]): l for l in cm_links}
    # files_modified -> "modified", files_read -> "investigated"
    assert by_pair[("claudemem_1", "capturestore_swift_code")]["relation"] == "modified"
    assert by_pair[("claudemem_2", "boidsim_swift_code")]["relation"] == "investigated"
    assert all(l["confidence"] == "EXTRACTED" for l in cm_links)


def test_idempotent_rerun(tmp_path):
    project, db, graph_path = _setup(tmp_path)
    run(project, db_path=db)
    first = json.loads(graph_path.read_text())
    rc = run(project, db_path=db)
    assert rc == 0
    second = json.loads(graph_path.read_text())
    assert len(second["nodes"]) == len(first["nodes"])
    assert len(second["links"]) == len(first["links"])
    ids = [n["id"] for n in second["nodes"]]
    assert len(ids) == len(set(ids)), "re-run must not duplicate nodes"


def test_rerun_preserves_non_claudemem_content(tmp_path):
    project, db, graph_path = _setup(tmp_path)
    run(project, db_path=db)
    run(project, db_path=db)
    graph = json.loads(graph_path.read_text())
    code_ids = {n["id"] for n in graph["nodes"] if not n["id"].startswith("claudemem_")}
    assert code_ids == {"capturestore_swift_code", "boidsim_swift_code"}
    code_links = [l for l in graph["links"] if not str(l["source"]).startswith("claudemem_")]
    assert len(code_links) == 1


def test_missing_db_skips_gracefully(tmp_path, capsys):
    project = tmp_path / PROJECT
    project.mkdir()
    graph_path = _make_graph(project / "graphify-out" / "graph.json")
    before = graph_path.read_text()
    rc = run(project, db_path=tmp_path / "nope.db")
    assert rc == 0, "missing DB must be non-fatal"
    out = capsys.readouterr().out
    assert "skipping" in out.lower()
    assert graph_path.read_text() == before, "graph must be untouched"


def test_missing_graph_is_error(tmp_path, capsys):
    project = tmp_path / PROJECT
    project.mkdir()
    db = _make_db(tmp_path / "claude-mem.db", _default_rows())
    rc = run(project, db_path=db)
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_noise_observation_filtered_out(tmp_path):
    rows = _default_rows() + [
        {
            "id": 3,
            "title": "Test suite passing after merge",
            "narrative": "Ran the full test suite and everything is passing now, all green across the board.",
            "type": "bugfix",
        }
    ]
    project, db, graph_path = _setup(tmp_path, rows)
    run(project, db_path=db)
    ids = {n["id"] for n in _claudemem_nodes(graph_path)}
    assert "claudemem_3" not in ids


def test_other_project_observations_excluded(tmp_path):
    rows = _default_rows() + [
        {
            "id": 4,
            "project": "OtherProject",
            "title": "Architectural decision in some other repo entirely",
            "narrative": "A binding architectural design decision with plenty of rationale and constraint discussion.",
            "type": "decision",
        }
    ]
    project, db, graph_path = _setup(tmp_path, rows)
    run(project, db_path=db)
    ids = {n["id"] for n in _claudemem_nodes(graph_path)}
    assert ids == {"claudemem_1", "claudemem_2"}


def test_explicit_project_override(tmp_path):
    rows = [
        {
            "id": 5,
            "project": "CustomName",
            "title": "Offline capture queue architecture chosen for custom",
            "narrative": (
                "Architectural decision: the capture queue persists locally and "
                "syncs later. Rationale: offline-first is a hard constraint."
            ),
            "type": "decision",
        }
    ]
    project, db, graph_path = _setup(tmp_path, rows)
    rc = run(project, db_path=db, project="CustomName")
    assert rc == 0
    assert {n["id"] for n in _claudemem_nodes(graph_path)} == {"claudemem_5"}


def test_ingest_config_domain_keywords_boost_score():
    from paragraph.ingest_claudemem import IngestConfig, filter_observation
    obs = {
        "id": 1, "type": "change", "title": "Adjusted para state handling",
        "narrative": "Reworked para state transitions for the capture flow. " * 3,
        "files_modified": "[]",
    }
    keep_default, score_default, _ = filter_observation(obs)
    cfg = IngestConfig(domain_keywords=["para state", "capture flow"])
    keep_cfg, score_cfg, reason = filter_observation(obs, cfg)
    assert score_cfg > score_default
    assert "domain+" in reason


def test_ingest_config_reviewer_passthrough():
    from paragraph.ingest_claudemem import IngestConfig, filter_observation
    obs = {
        "id": 2, "type": "change", "title": "Carol ruling on consent copy",
        "narrative": "Carol ruling applied: consent screen copy must name the data recipient. " * 2,
        "files_modified": "[]",
    }
    keep_default, _, _ = filter_observation(obs)
    assert keep_default is False
    cfg = IngestConfig(reviewers=["Carol"])
    keep_cfg, score, reason = filter_observation(obs, cfg)
    assert keep_cfg is True and reason == "passthrough"


def test_ingest_config_loads_from_graphify_out(tmp_path, capsys):
    import json as _json
    from paragraph.ingest_claudemem import load_ingest_config
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "ingest-config.json").write_text(_json.dumps({"reviewers": ["Dana"], "score_threshold": 2.0}))
    cfg = load_ingest_config(tmp_path)
    assert cfg.reviewers == ["Dana"]
    assert cfg.score_threshold == 2.0


def test_paranote_example_config_parses():
    import json as _json
    from pathlib import Path as _P
    from paragraph.ingest_claudemem import IngestConfig
    example = _P(__file__).parent.parent / "docs" / "examples" / "paranote-ingest.json"
    cfg = IngestConfig.from_dict(_json.loads(example.read_text()))
    assert "carol" in cfg.domain_keywords
    assert cfg.reviewers == ["Carol", "Sam"]
    assert cfg.ticket_patterns and cfg.dedup_ignore_names


def test_path_match_beats_stem_collision():
    from paragraph.ingest_claudemem import (
        build_path_index, build_stem_index, file_path_to_node_id,
    )
    nodes = [
        {"id": "users_auth_utils_swift", "source_file": "auth/utils.swift"},
        {"id": "net_utils_swift", "source_file": "network/utils.swift"},
    ]
    path_index = build_path_index(nodes)
    stem_index = build_stem_index(nodes)
    assert file_path_to_node_id("auth/utils.swift", stem_index, path_index) == "users_auth_utils_swift"
    assert file_path_to_node_id("network/utils.swift", stem_index, path_index) == "net_utils_swift"


def test_ambiguous_suffix_does_not_guess():
    from paragraph.ingest_claudemem import (
        build_path_index, build_stem_index, file_path_to_node_id,
    )
    nodes = [
        {"id": "a", "source_file": "auth/utils.swift"},
        {"id": "b", "source_file": "network/utils.swift"},
    ]
    path_index = build_path_index(nodes)
    # bare filename matches both directories at a suffix boundary -> refuse
    assert file_path_to_node_id("utils.swift", {}, path_index) is None


def test_stem_fallback_still_works():
    from paragraph.ingest_claudemem import (
        build_path_index, build_stem_index, file_path_to_node_id,
    )
    nodes = [{"id": "session_manager", "source_file": None}]
    path_index = build_path_index(nodes)  # empty — no source paths
    stem_index = build_stem_index(nodes)
    assert file_path_to_node_id("lib/session.py", stem_index, path_index) == "session_manager"
