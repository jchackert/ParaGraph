from paragraph.stoplist import (
    DEFAULT_STOPLIST, is_stoplisted, resolve_shadow_nodes, prune_generic,
)


def _node(nid, label, source_file="src/a.swift", file_type="code"):
    return {"id": nid, "label": label, "file_type": file_type,
            "source_file": source_file, "source_location": "L1"}


def _edge(src, tgt, relation="inherits"):
    return {"source": src, "target": tgt, "relation": relation,
            "confidence": "EXTRACTED", "weight": 1.0}


def test_is_stoplisted_case_insensitive():
    assert is_stoplisted("Sendable")
    assert is_stoplisted("sendable")
    assert is_stoplisted("View")
    assert is_stoplisted("str")
    assert not is_stoplisted("CaptureViewModel")


def test_is_stoplisted_strips_call_decoration():
    assert is_stoplisted(".filter()") is False  # filter isn't in the list
    assert is_stoplisted("String")


def test_is_stoplisted_extra():
    assert not is_stoplisted("MyThing")
    assert is_stoplisted("MyThing", {"mything"})


def test_drop_generic_shadow():
    nodes = [_node("a_foo", "Foo"), _node("sendable", "Sendable", source_file="")]
    edges = [_edge("a_foo", "sendable")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges)
    assert [n["id"] for n in n2] == ["a_foo"]
    assert e2 == []
    assert stats["dropped"] == 1
    assert stats["edges_removed"] == 1


def test_merge_shadow_into_real_definition():
    # class Bar: BaseService, where BaseService lives in another file.
    nodes = [
        _node("b_bar", "Bar", source_file="src/b.swift"),
        _node("baseservice", "BaseService", source_file=""),        # shadow
        _node("base_baseservice", "BaseService", source_file="src/base.swift"),
    ]
    edges = [_edge("b_bar", "baseservice")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges)
    assert stats["merged"] == 1
    ids = {n["id"] for n in n2}
    assert "baseservice" not in ids
    assert e2[0]["source"] == "b_bar"
    assert e2[0]["target"] == "base_baseservice"


def test_stoplist_wins_over_merge():
    # `extension View { }` produces a real node labeled View; merging every
    # `: View` conformance into it would recreate the god node. The shadow
    # drops; the extension node itself survives untouched.
    nodes = [
        _node("b_bar", "Bar", source_file="src/b.swift"),
        _node("view", "View", source_file=""),                      # shadow
        _node("myviews_view", "View", source_file="src/MyViews.swift"),
    ]
    edges = [_edge("b_bar", "view")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges)
    assert stats["merged"] == 0
    assert stats["dropped"] == 1
    assert {n["id"] for n in n2} == {"b_bar", "myviews_view"}
    assert e2 == []


def test_keep_restores_merge_for_project_owned_name():
    nodes = [
        _node("b_bar", "Bar", source_file="src/b.swift"),
        _node("view", "View", source_file=""),                      # shadow
        _node("myviews_view", "View", source_file="src/MyViews.swift"),
    ]
    edges = [_edge("b_bar", "view")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges, keep={"view"})
    assert stats["merged"] == 1
    assert e2[0]["target"] == "myviews_view"


def test_keep_exempts_from_stoplist():
    nodes = [_node("a_foo", "Foo"), _node("view", "View", source_file="")]
    edges = [_edge("a_foo", "view")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges, keep={"view"})
    assert stats["dropped"] == 0
    assert len(n2) == 2 and len(e2) == 1


def test_merge_dedupes_resulting_edges_and_self_loops():
    nodes = [
        _node("b_bar", "Bar", source_file="src/b.swift"),
        _node("foo", "Foo", source_file=""),                        # shadow
        _node("a_foo", "Foo", source_file="src/a.swift"),
    ]
    edges = [
        _edge("b_bar", "foo"),
        _edge("b_bar", "a_foo"),   # already links to the real node
        _edge("a_foo", "foo"),     # becomes a self-loop after merge
    ]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges)
    assert len(e2) == 1
    assert (e2[0]["source"], e2[0]["target"]) == ("b_bar", "a_foo")


def test_non_generic_external_symbol_kept():
    # A third-party type not in the stoplist survives as an external node.
    nodes = [_node("a_foo", "Foo"), _node("alamofire", "Alamofire", source_file="")]
    edges = [_edge("a_foo", "alamofire")]
    n2, e2, stats = resolve_shadow_nodes(nodes, edges)
    assert len(n2) == 2 and len(e2) == 1
    assert stats == {"merged": 0, "dropped": 0, "edges_removed": 0, "edges_remapped": 0}


def test_prune_generic_links_schema():
    data = {
        "nodes": [_node("a_foo", "Foo"),
                  _node("sendable", "Sendable", source_file="")],
        "links": [{**_edge("a_foo", "sendable"), "_src": "a_foo", "_tgt": "sendable"}],
        "graph": {},
    }
    out, stats = prune_generic(data)
    assert stats["dropped"] == 1
    assert len(out["nodes"]) == 1
    assert out["links"] == []
    assert "graph" in out  # untouched metadata preserved


def test_prune_generic_remaps_src_tgt_fields():
    data = {
        "nodes": [_node("b_bar", "Bar", source_file="src/b.swift"),
                  _node("foo", "Foo", source_file=""),
                  _node("a_foo", "Foo", source_file="src/a.swift")],
        "links": [{**_edge("b_bar", "foo"), "_src": "b_bar", "_tgt": "foo"}],
    }
    out, stats = prune_generic(data)
    assert stats["merged"] == 1
    link = out["links"][0]
    assert link["target"] == "a_foo"
    assert link["_tgt"] == "a_foo"


def test_stoplist_is_lowercase():
    assert all(s == s.lower() for s in DEFAULT_STOPLIST)
