import re
from pathlib import Path

from paragraph.schema import (
    KNOWN_RELATIONS, SEMANTIC_RELATIONS, HYPEREDGE_RELATIONS,
    VALID_FILE_TYPES, VALID_CONFIDENCES,
    edge_endpoints, set_edge_endpoints, edge_list, edge_list_key,
    unknown_relations,
)

SKILL_MD = Path(__file__).parent.parent / "paragraph" / "skill.md"


def test_edge_endpoints_extraction_schema():
    assert edge_endpoints({"source": "a", "target": "b"}) == ("a", "b")


def test_edge_endpoints_export_schema():
    assert edge_endpoints({"_src": "a", "_tgt": "b"}) == ("a", "b")


def test_edge_endpoints_prefers_source_target():
    e = {"source": "a", "target": "b", "_src": "x", "_tgt": "y"}
    assert edge_endpoints(e) == ("a", "b")


def test_set_edge_endpoints_rewrites_both_styles():
    e = {"source": "a", "target": "b", "_src": "a", "_tgt": "b"}
    set_edge_endpoints(e, "x", "y")
    assert (e["source"], e["target"], e["_src"], e["_tgt"]) == ("x", "y", "x", "y")


def test_set_edge_endpoints_bare_edge_gets_source_target():
    e = {}
    set_edge_endpoints(e, "x", "y")
    assert e == {"source": "x", "target": "y"}


def test_edge_list_prefers_links():
    assert edge_list({"links": [1], "edges": [2]}) == [1]
    assert edge_list({"edges": [2]}) == [2]
    assert edge_list({}) == []
    assert edge_list_key({"edges": [2]}) == "edges"
    assert edge_list_key({}) == "links"


def test_unknown_relations_counts_drift():
    edges = [{"relation": "calls"}, {"relation": "zaps"}, {"relation": "zaps"}]
    assert unknown_relations(edges) == {"zaps": 2}


def test_validate_reexports_match():
    # build.py imports these names from validate; both must be the schema sets
    from paragraph import validate
    assert validate.VALID_FILE_TYPES is VALID_FILE_TYPES
    assert validate.VALID_CONFIDENCES is VALID_CONFIDENCES


def test_skill_md_edge_relations_are_known():
    """The LLM prompt's relation enum must not drift from schema.py.

    skill.md's output-schema line declares the relations the semantic pass
    may emit, as `"relation":"a|b|c"`. Every one must be in
    SEMANTIC_RELATIONS (and vice versa) so prompt prose and code agree.
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.search(r'"edges":\[\{[^]]*?"relation":"([a-z_|]+)"', text)
    assert m, "could not find the edge relation enum in skill.md's output schema"
    prose = set(m.group(1).split("|"))
    assert prose == SEMANTIC_RELATIONS, (
        f"skill.md edge relations {sorted(prose)} != "
        f"schema.SEMANTIC_RELATIONS {sorted(SEMANTIC_RELATIONS)}"
    )


def test_skill_md_hyperedge_relations_are_known():
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.search(r'"hyperedges":\[\{.*?"relation":"([a-z_|]+)"', text, re.S)
    assert m, "could not find the hyperedge relation enum in skill.md"
    prose = set(m.group(1).split("|"))
    assert prose == HYPEREDGE_RELATIONS


def test_pipeline_relations_are_known():
    assert {"part_of", "investigated", "modified"} <= KNOWN_RELATIONS
