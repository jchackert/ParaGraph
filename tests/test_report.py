import json
from pathlib import Path
from paragraph.build import build_from_json
from paragraph.cluster import cluster, score_all
from paragraph.analyze import god_nodes, surprising_connections
from paragraph.report import generate

FIXTURES = Path(__file__).parent / "fixtures"

def make_inputs():
    extraction = json.loads((FIXTURES / "extraction.json").read_text())
    G = build_from_json(extraction)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    labels = {cid: f"Community {cid}" for cid in communities}
    gods = god_nodes(G)
    surprises = surprising_connections(G)
    detection = {"total_files": 4, "total_words": 62400, "needs_graph": True, "warning": None}
    tokens = {"input": extraction["input_tokens"], "output": extraction["output_tokens"]}
    return G, communities, cohesion, labels, gods, surprises, detection, tokens

def test_report_contains_header():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "# Graph Report" in report

def test_report_contains_corpus_check():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Corpus Check" in report

def test_report_contains_god_nodes():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## God Nodes" in report

def test_report_contains_surprising_connections():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Surprising Connections" in report

def test_report_contains_communities():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Communities" in report

def test_report_contains_ambiguous_section():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Ambiguous Edges" in report

def test_report_shows_token_cost():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "Token cost" in report
    assert "1,200" in report

def test_report_shows_raw_cohesion_scores():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "Cohesion:" in report
    assert "✓" not in report
    assert "⚠" not in report


def test_report_upgrades_bare_default_labels():
    """Bare "Community N" labels get deterministic member-based names in headings."""
    from paragraph.cluster import label_communities
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    auto = label_communities(G, communities)
    upgraded = [
        cid for cid in communities
        if auto[cid] != f"Community {cid}"
        and f'### Community {cid} - "{auto[cid]}"' in report
    ]
    assert upgraded, "no community heading used the deterministic label"
    # No community with a usable auto label should render as a bare default
    for cid in communities:
        if auto[cid] != f"Community {cid}":
            assert f'### Community {cid} - "Community {cid}"' not in report


def test_report_keeps_caller_provided_labels():
    """Real (e.g. LLM-generated) labels are never overwritten by auto labels."""
    G, communities, cohesion, _, gods, surprises, detection, tokens = make_inputs()
    labels = {cid: f"Topic {cid}" for cid in communities}
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert any(f'### Community {cid} - "Topic {cid}"' in report for cid in communities)


def test_report_labels_deterministic_across_calls():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    r1 = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    r2 = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert r1 == r2


def test_report_freshness_no_record(tmp_path):
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens,
                      "./project", out_dir=tmp_path / "graphify-out")
    assert "## Extraction Freshness" in report
    assert "Last full semantic extraction: unknown — no record" in report


def test_report_freshness_with_manifest(tmp_path):
    from datetime import datetime
    out = tmp_path / "graphify-out"
    out.mkdir()
    src = tmp_path / "a.py"
    src.write_text("x = 1")
    manifest = {
        "a.py": src.stat().st_mtime - 100,   # modified since last extraction
        "gone.py": 1000.0,                   # deleted since last extraction
        "b.py": 0.0,
    }
    (tmp_path / "b.py").write_text("y = 2")
    manifest["b.py"] = (tmp_path / "b.py").stat().st_mtime  # unchanged
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    expected_date = datetime.fromtimestamp(manifest_path.stat().st_mtime).date().isoformat()

    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens,
                      "./project", out_dir=out)
    assert f"Last full semantic extraction: {expected_date} (manifest.json last written)" in report
    assert "1 modified · 1 deleted (of 3 tracked" in report


def test_report_freshness_needs_update_flag(tmp_path):
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "manifest.json").write_text("{}")
    (out / "needs_update").write_text("1")
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens,
                      "./project", out_dir=out)
    assert "needs_update flag is set" in report


# --- stable mode (PARAGRAPH_STABLE_REPORT) -----------------------------------
# Stable mode omits every wall-clock-varying field from GRAPH_REPORT.md so a
# consumer that commits the file only sees a diff when the graph itself changed.
# The omitted values move to the freshness sidecar, never dropped.

def _stable_report():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    return generate(G, communities, cohesion, labels, gods, surprises, detection,
                    tokens, "./project", stable=True)


def test_stable_report_omits_generation_date():
    from datetime import date
    report = _stable_report()
    assert "# Graph Report" in report
    assert date.today().isoformat() not in report.split("\n")[0]


def test_stable_report_omits_volatile_corpus_counts():
    report = _stable_report()
    assert "## Corpus Check" in report          # section kept
    assert "62,400 words" not in report         # the drifting number is gone
    assert "Verdict:" in report                 # the stable judgement stays


def test_stable_report_omits_freshness_counters():
    report = _stable_report()
    assert "Source files changed since" not in report
    assert "GRAPH_FRESHNESS.md" in report       # points at where they went


def test_stable_report_keeps_structural_sections():
    report = _stable_report()
    for section in ("## God Nodes", "## Surprising Connections", "## Communities"):
        assert section in report


def test_stable_report_is_byte_identical_across_calls():
    assert _stable_report() == _stable_report()


def test_default_is_unchanged_when_env_unset(monkeypatch):
    monkeypatch.delenv("PARAGRAPH_STABLE_REPORT", raising=False)
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection,
                      tokens, "./project")
    assert "62,400 words" in report             # volatile fields still present


def test_env_var_enables_stable_mode(monkeypatch):
    monkeypatch.setenv("PARAGRAPH_STABLE_REPORT", "1")
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection,
                      tokens, "./project")
    assert "62,400 words" not in report


def test_freshness_sidecar_carries_the_volatile_values():
    from paragraph.report import freshness_report
    _, _, _, _, _, _, detection, _ = make_inputs()
    side = freshness_report(detection, "./project")
    assert "62,400 words" in side
    assert "## Corpus Check" in side
    assert "## Extraction Freshness" in side


# --- marker file (graphify-out/.stable_report) --------------------------------
# The marker makes stable mode a property of the corpus rather than of one
# command, so it survives any invocation path. An env var exported by a single
# script is silently bypassed by every other caller.

def test_marker_file_enables_stable_mode(tmp_path, monkeypatch):
    from paragraph.report import stable_mode_default, STABLE_MARKER_FILENAME
    monkeypatch.delenv("PARAGRAPH_STABLE_REPORT", raising=False)
    out = tmp_path / "graphify-out"
    out.mkdir()
    assert stable_mode_default(out) is False
    (out / STABLE_MARKER_FILENAME).write_text("")
    assert stable_mode_default(out) is True


def test_marker_found_via_root_when_out_dir_not_given(tmp_path, monkeypatch):
    from paragraph.report import stable_mode_default, STABLE_MARKER_FILENAME
    monkeypatch.delenv("PARAGRAPH_STABLE_REPORT", raising=False)
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / STABLE_MARKER_FILENAME).write_text("")
    assert stable_mode_default(None, str(tmp_path)) is True


def test_env_var_overrides_marker_in_both_directions(tmp_path, monkeypatch):
    from paragraph.report import stable_mode_default, STABLE_MARKER_FILENAME
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / STABLE_MARKER_FILENAME).write_text("")
    monkeypatch.setenv("PARAGRAPH_STABLE_REPORT", "0")
    assert stable_mode_default(out) is False      # explicit off beats the marker
    monkeypatch.setenv("PARAGRAPH_STABLE_REPORT", "1")
    assert stable_mode_default(out) is True


def test_empty_env_var_defers_to_marker(tmp_path, monkeypatch):
    from paragraph.report import stable_mode_default, STABLE_MARKER_FILENAME
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / STABLE_MARKER_FILENAME).write_text("")
    monkeypatch.setenv("PARAGRAPH_STABLE_REPORT", "")
    assert stable_mode_default(out) is True


def test_no_marker_and_no_env_is_off(tmp_path, monkeypatch):
    from paragraph.report import stable_mode_default
    monkeypatch.delenv("PARAGRAPH_STABLE_REPORT", raising=False)
    out = tmp_path / "graphify-out"
    out.mkdir()
    assert stable_mode_default(out) is False


def test_generate_picks_up_marker_without_env(tmp_path, monkeypatch):
    from paragraph.report import STABLE_MARKER_FILENAME
    monkeypatch.delenv("PARAGRAPH_STABLE_REPORT", raising=False)
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / STABLE_MARKER_FILENAME).write_text("")
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection,
                      tokens, str(tmp_path), out_dir=out)
    assert "62,400 words" not in report
    assert "GRAPH_FRESHNESS.md" in report
