import json

from paragraph.rebuild import recluster, run


def _make_project(tmp_path):
    (tmp_path / "app.py").write_text(
        "class AuthManager:\n"
        "    def login(self):\n"
        "        return validate()\n\n"
        "def validate():\n"
        "    return True\n"
    )
    from paragraph.watch import _rebuild_code
    assert _rebuild_code(tmp_path) is True
    return tmp_path / "graphify-out" / "graph.json"


def test_recluster_regenerates_outputs(tmp_path):
    graph_json = _make_project(tmp_path)
    report = tmp_path / "graphify-out" / "GRAPH_REPORT.md"
    report.unlink()
    assert recluster(tmp_path) == 0
    assert report.exists()
    data = json.loads(graph_json.read_text())
    assert data["nodes"]


def test_recluster_missing_graph_errors(tmp_path, capsys):
    assert recluster(tmp_path) == 1
    assert "no graph found" in capsys.readouterr().err


def test_run_missing_graph_errors(tmp_path, capsys):
    assert run(tmp_path) == 1
    assert "no graph found" in capsys.readouterr().err


def test_run_full_pipeline_with_optional_steps_skipped(tmp_path, capsys):
    _make_project(tmp_path)
    rc = run(tmp_path, skip_ingest=True, skip_enrich=True, skip_link=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "rebuild complete" in out
    assert "skipped (--skip-ingest)" in out
    assert "skipped (--skip-enrich)" in out
    assert "skipped (--skip-link)" in out
    assert (tmp_path / "graphify-out" / "GRAPH_REPORT.md").exists()


def test_run_ingest_skips_when_db_missing(tmp_path, capsys):
    _make_project(tmp_path)
    rc = run(tmp_path, db=tmp_path / "nope.db", skip_enrich=True, skip_link=True)
    assert rc == 0
    assert "no claude-mem DB" in capsys.readouterr().out


def test_cli_has_rebuild_subcommand():
    from paragraph.__main__ import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["rebuild", ".", "--skip-enrich", "--link-threshold", "0.7"])
    assert ns.skip_enrich is True
    assert ns.link_threshold == 0.7
