"""Cross-file Swift reference extraction.

SwiftUI apps hold most cross-file references in places the plain call-graph
pass never looked: property initializers, computed-property bodies (`var
body: some View`), type annotations, and `X.self` metatype references.
These tests pin the extraction added for each of those sites.
"""
from pathlib import Path

from paragraph.extract import extract

DEFS = """
import Foundation

@Observable
final class BrainDumpCoordinator {
    func start() { print("go") }
}

enum AttentionZone { case home, away }

@Model
final class Person {
    var name: String = ""
}
"""

USES = """
import SwiftUI

struct ContentView: SomeProtocol {
    @State var coordinator = BrainDumpCoordinator()
    var zone: AttentionZone = .home
    let schema = [Person.self]

    var body: some CustomBody {
        CustomBody().onAppear { coordinator.start() }
    }

    func helper() {
        let c = BrainDumpCoordinator()
        c.start()
    }
}
"""


def _extract_two(tmp_path) -> dict:
    (tmp_path / "Defs.swift").write_text(DEFS)
    (tmp_path / "Uses.swift").write_text(USES)
    return extract([tmp_path / "Defs.swift", tmp_path / "Uses.swift"],
                   cache_root=tmp_path)


def _rel(result, relation):
    return {(e["source"], e["target"]) for e in result["edges"]
            if e["relation"] == relation}


def test_property_initializer_call_resolved_cross_file(tmp_path):
    result = _extract_two(tmp_path)
    assert ("uses_contentview", "defs_braindumpcoordinator") in _rel(result, "calls")


def test_computed_property_body_calls_resolved(tmp_path):
    result = _extract_two(tmp_path)
    # coordinator.start() lives inside `var body` — attributed to the type
    assert ("uses_contentview", "defs_braindumpcoordinator_start") in _rel(result, "calls")


def test_type_annotation_reference(tmp_path):
    result = _extract_two(tmp_path)
    assert ("uses_contentview", "defs_attentionzone") in _rel(result, "references")


def test_metatype_self_reference(tmp_path):
    result = _extract_two(tmp_path)
    assert ("uses_contentview", "defs_person") in _rel(result, "references")


def test_static_call_receiver_referenced(tmp_path):
    (tmp_path / "Resolver.swift").write_text(
        "enum DateResolver { static func resolve(_ s: String) -> String? { nil } }\n")
    (tmp_path / "Caller.swift").write_text(
        "struct Intent {\n    func run(_ text: String) {\n"
        "        let due = DateResolver.resolve(text)\n        _ = due\n    }\n}\n")
    result = extract([tmp_path / "Resolver.swift", tmp_path / "Caller.swift"],
                     cache_root=tmp_path)
    assert ("caller_intent", "resolver_dateresolver") in _rel(result, "references")


def test_qualified_nested_type_references_outer(tmp_path):
    (tmp_path / "Service.swift").write_text(
        "final class RefinementService { struct Result { } }\n")
    (tmp_path / "User.swift").write_text(
        "struct Wrapper {\n    let r: RefinementService.Result\n}\n")
    result = extract([tmp_path / "Service.swift", tmp_path / "User.swift"],
                     cache_root=tmp_path)
    assert ("user_wrapper", "service_refinementservice") in _rel(result, "references")


def test_generic_conformance_not_synthesized(tmp_path):
    (tmp_path / "One.swift").write_text(
        "struct Thing: Sendable, Equatable { var x: Int }\n")
    result = extract([tmp_path / "One.swift"], cache_root=tmp_path)
    labels = {n["label"] for n in result["nodes"]}
    assert "Sendable" not in labels
    assert "Equatable" not in labels
    # and no dangling inherits edges to them
    assert not [e for e in result["edges"]
                if e["relation"] == "inherits" and e["target"] in ("sendable", "equatable")]


def test_stoplisted_types_not_referenced(tmp_path):
    (tmp_path / "One.swift").write_text(
        "struct Thing { var name: String = \"\"; var when: Date? }\n")
    result = extract([tmp_path / "One.swift"], cache_root=tmp_path)
    assert _rel(result, "references") == set()


def test_cross_file_inherits_merges_into_real_definition(tmp_path):
    (tmp_path / "Base.swift").write_text("class BaseService { }\n")
    (tmp_path / "Child.swift").write_text("final class ChildService: BaseService { }\n")
    result = extract([tmp_path / "Base.swift", tmp_path / "Child.swift"],
                     cache_root=tmp_path)
    # the shadow node is merged, the edge lands on the real definition
    shadows = [n for n in result["nodes"] if not n.get("source_file")]
    assert shadows == []
    assert ("child_childservice", "base_baseservice") in _rel(result, "inherits")


def test_stale_ast_cache_invalidated_by_version(tmp_path):
    from paragraph.cache import save_cached, load_cached
    f = tmp_path / "One.swift"
    f.write_text("struct Thing { }\n")
    # simulate a pre-versioning AST cache entry
    save_cached(f, {"nodes": [{"id": "stale", "label": "Stale",
                               "file_type": "code", "source_file": str(f),
                               "source_location": "L1"}],
                    "edges": [], "raw_calls": []}, tmp_path, kind="ast")
    result = extract([f], cache_root=tmp_path)
    labels = {n["label"] for n in result["nodes"]}
    assert "Stale" not in labels
    assert "Thing" in labels
    # and the refreshed entry is version-stamped
    cached = load_cached(f, tmp_path, kind="ast")
    assert cached.get("extractor_version") is not None


def test_semantic_cache_entry_does_not_mask_ast_extraction(tmp_path):
    # Regression: AST and semantic caches shared {hash}.json, so a file with
    # a semantic (LLM) fragment lost its ENTIRE structural extraction on
    # every `update` — the fragment was returned as the file's extraction.
    from paragraph.cache import save_cached, load_cached
    f = tmp_path / "One.swift"
    f.write_text("struct Thing { }\n")
    semantic = {"nodes": [{"id": "sem", "label": "Semantic concept",
                           "file_type": "rationale", "source_file": str(f),
                           "source_location": "L1"}],
                "edges": []}
    save_cached(f, semantic, tmp_path)  # legacy/semantic namespace
    result = extract([f], cache_root=tmp_path)
    labels = {n["label"] for n in result["nodes"]}
    assert "Thing" in labels                      # AST extraction ran
    assert "Semantic concept" not in labels       # fragment not misused
    # the semantic entry survives untouched in its own namespace
    assert load_cached(f, tmp_path) == semantic
