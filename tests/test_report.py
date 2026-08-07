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
