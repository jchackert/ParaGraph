import json

from paragraph.advise import (
    DETECTOR_RULES,
    LAYERING_VIOLATION_CAP,
    advice_markdown,
    load_standards,
    run,
    run_detectors,
)


def node(nid, label=None, sf="App/Thing.swift", ft="code", **kw):
    n = {"id": nid, "label": label or nid, "source_file": sf, "file_type": ft}
    n.update(kw)
    return n


def edge(src, tgt, relation="calls", confidence="EXTRACTED"):
    return {"source": src, "target": tgt, "relation": relation,
            "confidence": confidence, "_src": src, "_tgt": tgt}


def graph(nodes, links=()):
    return {"nodes": list(nodes), "links": list(links)}


def by_detector(findings, name):
    return [f for f in findings if f["detector"] == name]


# ---------------------------------------------------------------- standards


def test_standards_pack_validates():
    standards = load_standards()
    assert 15 <= len(standards) <= 18
    for rid, rule in standards.items():
        assert rule["id"] == rid
        for field in ("title", "statement", "rationale", "applies_to",
                      "snippet_bad", "snippet_good"):
            assert rule[field], f"{rid} missing {field}"
        assert rule["citations"], f"{rid} has no citations"
        for cit in rule["citations"]:
            assert cit["title"] and cit["source"]
            assert cit["url"].startswith("http")
        assert rule["applies_to"] == "general" or rule["applies_to"] in DETECTOR_RULES


def test_every_detector_rule_exists_in_pack():
    standards = load_standards()
    for detector, rid in DETECTOR_RULES.items():
        assert rid in standards, f"{detector} -> {rid} missing from pack"


# ---------------------------------------------------------------- Swift gate


def test_swift_gate_python_god_node_yields_zero_findings():
    # A Python tooling main() with 100 edges (50 contains + 50 calls),
    # a huge-fan-in Python ".shared" impostor, and a force-heavy body:
    # none of it may ever produce a finding.
    nodes = [node("main", "main()", "scripts/graph-topology.py")]
    links = []
    for i in range(100):
        nid = f"py{i}"
        nodes.append(node(nid, f"py{i}()", "scripts/graph-topology.py"))
        links.append(edge("main", nid,
                          relation="contains" if i < 50 else "calls"))
    nodes.append(node("pyshared", "Registry.shared", "scripts/registry.py",
                      source_body="try! try! as! DispatchQueue.main @Observable"))
    for i in range(15):
        links.append(edge(f"py{i}", "pyshared", relation="uses"))
    findings, stats = run_detectors(graph(nodes, links))
    assert findings == []
    assert stats["swift_nodes"] == 0
    assert stats["excluded_non_swift_code"] >= 1


# ---------------------------------------------------------------- structural


def _member_graph(n_members, label="BigType", sf="App/Big.swift", relation="contains"):
    nodes = [node("big", label, sf)]
    links = []
    for i in range(n_members):
        m = f"m{i}"
        nodes.append(node(m, f"m{i}()", sf))
        links.append(edge("big", m, relation=relation))
    return graph(nodes, links)


def test_massive_type_positive():
    findings, stats = run_detectors(_member_graph(21))
    hits = by_detector(findings, "massive_type")
    assert len(hits) == 1
    f = hits[0]
    assert f["rule_id"] == "SWIFT-SRP-01"
    assert f["confidence"] == "EXTRACTED"
    assert f["nodes"] == ["big"]
    assert "21" in f["evidence"] and "BigType" in f["evidence"]
    assert f["source_file"] == "App/Big.swift"
    assert stats["detector_counts"]["massive_type"] == 1


def test_massive_type_negative_at_threshold():
    findings, _ = run_detectors(_member_graph(20))
    assert by_detector(findings, "massive_type") == []


def test_massive_view_model_positive():
    g = _member_graph(16, label="CaptureViewModel",
                      sf="App/Capture/CaptureViewModel.swift", relation="method")
    findings, _ = run_detectors(g)
    hits = by_detector(findings, "massive_view_model")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "SWIFT-MVVM-01"
    assert "CaptureViewModel" in hits[0]["evidence"]
    # 16 <= 20, so massive_type must not co-fire
    assert by_detector(findings, "massive_type") == []


def test_massive_view_model_negative():
    g = _member_graph(15, label="CaptureViewModel",
                      sf="App/Capture/CaptureViewModel.swift", relation="method")
    findings, _ = run_detectors(g)
    assert by_detector(findings, "massive_view_model") == []
    # Same 16 method edges on a NON-viewmodel node: no finding either
    g = _member_graph(16, label="BigType", sf="App/Big.swift", relation="method")
    findings, _ = run_detectors(g)
    assert by_detector(findings, "massive_view_model") == []


def test_layering_violation_positive_and_negative():
    nodes = [
        node("inv", "Invoice", "App/Models/Invoice.swift"),
        node("invview", "InvoiceView", "App/Views/InvoiceView.swift"),
        node("invvm", "InvoiceViewModel", "App/ViewModels/InvoiceViewModel.swift"),
    ]
    # model -> view points UP the stack; view -> viewmodel is a normal dep
    links = [edge("inv", "invview", relation="uses"),
             edge("invview", "invvm", relation="uses")]
    findings, _ = run_detectors(graph(nodes, links))
    hits = by_detector(findings, "layering_violation")
    assert len(hits) == 1
    f = hits[0]
    assert f["rule_id"] == "SWIFT-ARCH-01"
    assert f["nodes"] == ["inv", "invview"]
    assert "model" in f["evidence"] and "view" in f["evidence"]


def test_layering_violation_capped_at_25_with_overflow():
    nodes = [node("theview", "MainView", "App/Views/MainView.swift")]
    links = []
    for i in range(30):
        nid = f"mod{i}"
        nodes.append(node(nid, f"Mod{i}", f"App/Models/Mod{i}.swift"))
        links.append(edge(nid, "theview", relation="uses"))
    findings, stats = run_detectors(graph(nodes, links))
    hits = by_detector(findings, "layering_violation")
    assert len(hits) == LAYERING_VIOLATION_CAP == 25
    assert stats["layering_violation_overflow"] == 5


def test_view_skips_viewmodel_positive():
    nodes = [
        node("v", "SettingsView", "App/Views/SettingsView.swift"),
        node("s", "SyncService", "App/Services/SyncService.swift"),
    ]
    findings, _ = run_detectors(graph(nodes, [edge("v", "s", relation="calls")]))
    hits = by_detector(findings, "view_skips_viewmodel")
    assert len(hits) == 1
    f = hits[0]
    assert f["rule_id"] == "SWIFT-MVVM-02"
    assert f["nodes"] == ["v", "s"]
    assert "SettingsView" in f["evidence"] and "SyncService" in f["evidence"]


def test_view_skips_viewmodel_negative():
    nodes = [
        node("v", "SettingsView", "App/Views/SettingsView.swift"),
        node("vm", "SettingsViewModel", "App/ViewModels/SettingsViewModel.swift"),
        node("s", "SyncService", "App/Services/SyncService.swift"),
    ]
    # view -> viewmodel (fine) and viewmodel -> service (fine)
    links = [edge("v", "vm", relation="calls"), edge("vm", "s", relation="calls")]
    findings, _ = run_detectors(graph(nodes, links))
    assert by_detector(findings, "view_skips_viewmodel") == []
    # A non-calls/uses relation between view and service does not fire either
    findings, _ = run_detectors(graph(nodes, [edge("v", "s", relation="references")]))
    assert by_detector(findings, "view_skips_viewmodel") == []


def _singleton_graph(fan_in, label="APIClient.shared"):
    nodes = [node("api", label, "App/Services/APIClient.swift")]
    links = []
    for i in range(fan_in):
        nid = f"c{i}"
        nodes.append(node(nid, f"Caller{i}Service", f"App/Services/Caller{i}.swift"))
        links.append(edge(nid, "api", relation="uses"))
    return graph(nodes, links)


def test_singleton_fanin_positive():
    findings, _ = run_detectors(_singleton_graph(11))
    hits = by_detector(findings, "singleton_fanin")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "SWIFT-DI-01"
    assert "APIClient.shared" in hits[0]["evidence"] and "11" in hits[0]["evidence"]
    # "Singleton" in the label also qualifies
    findings, _ = run_detectors(_singleton_graph(11, label="AnalyticsSingleton"))
    assert len(by_detector(findings, "singleton_fanin")) == 1


def test_singleton_fanin_negative():
    # At the threshold: no finding
    findings, _ = run_detectors(_singleton_graph(10))
    assert by_detector(findings, "singleton_fanin") == []
    # High fan-in but a normal label: no finding
    findings, _ = run_detectors(_singleton_graph(11, label="APIClient"))
    assert by_detector(findings, "singleton_fanin") == []


def _hub_graph(n_hubs, n_spokes):
    nodes = [node(f"spoke{i}", f"Spoke{i}Service", f"App/Services/Spoke{i}.swift")
             for i in range(n_spokes)]
    links = []
    for h in range(n_hubs):
        hid = f"hub{h}"
        nodes.append(node(hid, f"Mega{h}Service", f"App/Services/Mega{h}.swift"))
        links.extend(edge(hid, f"spoke{i}", relation="uses") for i in range(n_spokes))
    return graph(nodes, links)


def test_god_object_positive_and_negative():
    findings, _ = run_detectors(_hub_graph(1, 41))
    hits = by_detector(findings, "god_object")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "SWIFT-SRP-02"
    assert "41" in hits[0]["evidence"]
    findings, _ = run_detectors(_hub_graph(1, 40))
    assert by_detector(findings, "god_object") == []


def test_god_object_top_five_cap():
    findings, _ = run_detectors(_hub_graph(7, 41))
    assert len(by_detector(findings, "god_object")) == 5


# ------------------------------------------------------------- source_body


def test_force_operations_fires_only_with_body():
    body = "let a = try! decode()\nlet b = x as! Y\nlet c = try! parse()"
    nodes = [node("f", "Loader", "App/Loader.swift", source_body=body)]
    findings, _ = run_detectors(graph(nodes))
    hits = by_detector(findings, "force_operations")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "SWIFT-ERR-01"
    assert hits[0]["confidence"] == "INFERRED"
    # No source_body at all -> silent, even for the same node otherwise
    nodes = [node("f", "Loader", "App/Loader.swift")]
    findings, _ = run_detectors(graph(nodes))
    assert by_detector(findings, "force_operations") == []


def test_force_operations_below_threshold():
    body = "let a = try! decode()\nlet b = x as! Y"  # only 2 occurrences
    nodes = [node("f", "Loader", "App/Loader.swift", source_body=body)]
    findings, _ = run_detectors(graph(nodes))
    assert by_detector(findings, "force_operations") == []


def test_main_queue_in_observable_fires_only_with_body():
    body = ("@Observable final class FeedModel {\n"
            "  func refresh() { DispatchQueue.main.async { } }\n}")
    nodes = [node("m", "FeedModel", "App/FeedModel.swift", source_body=body)]
    findings, _ = run_detectors(graph(nodes))
    hits = by_detector(findings, "main_queue_in_observable")
    assert len(hits) == 1
    assert hits[0]["rule_id"] == "SWIFT-CONC-01"
    assert hits[0]["confidence"] == "INFERRED"
    # ObservableObject spelling also qualifies
    body2 = body.replace("@Observable final class", "final class FeedModel: ObservableObject //")
    nodes = [node("m", "FeedModel", "App/FeedModel.swift", source_body=body2)]
    findings, _ = run_detectors(graph(nodes))
    assert len(by_detector(findings, "main_queue_in_observable")) == 1


def test_main_queue_in_observable_negative():
    # DispatchQueue.main without an observable type: no finding
    nodes = [node("m", "Helper", "App/Helper.swift",
                  source_body="DispatchQueue.main.async { }")]
    findings, _ = run_detectors(graph(nodes))
    assert by_detector(findings, "main_queue_in_observable") == []
    # Observable type without main-queue hops: no finding
    nodes = [node("m", "FeedModel", "App/FeedModel.swift",
                  source_body="@Observable final class FeedModel { }")]
    findings, _ = run_detectors(graph(nodes))
    assert by_detector(findings, "main_queue_in_observable") == []
    # No body: no finding
    nodes = [node("m", "FeedModel", "App/FeedModel.swift")]
    findings, _ = run_detectors(graph(nodes))
    assert by_detector(findings, "main_queue_in_observable") == []


# ---------------------------------------------------------------- markdown


def _violating_graph():
    nodes = [
        node("inv", "Invoice", "App/Models/Invoice.swift"),
        node("invview", "InvoiceView", "App/Views/InvoiceView.swift"),
        node("tool", "main()", "scripts/build.py"),
    ]
    return graph(nodes, [edge("inv", "invview", relation="uses")])


def test_advice_markdown_structure():
    standards = load_standards()
    findings, stats = run_detectors(_violating_graph())
    md = advice_markdown(findings, stats, standards)
    # scope header
    assert md.startswith("# Swift Coding-Standards Advice")
    assert "Scope: Swift code only" in md
    assert "Non-Swift code nodes excluded from analysis: 1" in md
    # triggered rule section with snippets and citations
    assert "## SWIFT-ARCH-01" in md
    assert "```swift" in md
    assert "http" in md and "Sources:" in md
    # untriggered rules land in the Clean section
    assert "## Clean" in md
    assert "SWIFT-API-01" in md


def test_advice_markdown_no_findings_lists_all_rules_clean():
    standards = load_standards()
    findings, stats = run_detectors(graph([node("a", "TidyView", "App/Views/TidyView.swift")]))
    md = advice_markdown(findings, stats, standards)
    assert "## Clean" in md
    for rid in standards:
        assert rid in md


# --------------------------------------------------------------- end-to-end


def test_run_writes_advice_md(tmp_path, capsys):
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps(_violating_graph()), encoding="utf-8")
    rc = run(tmp_path)
    assert rc == 0
    advice = out / "ADVICE.md"
    assert advice.exists()
    text = advice.read_text(encoding="utf-8")
    assert "Swift Coding-Standards Advice" in text
    summary = capsys.readouterr().out
    assert "SWIFT-ARCH-01" in summary
    assert "Excluded non-Swift code nodes" in summary


def test_run_errors_cleanly_without_graph(tmp_path, capsys):
    rc = run(tmp_path)
    assert rc == 1
    assert "no graph found" in capsys.readouterr().err
    assert not (tmp_path / "graphify-out" / "ADVICE.md").exists()
