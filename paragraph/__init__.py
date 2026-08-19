"""paragraph - extract · build · cluster · analyze · report."""


def __getattr__(name):
    # Lazy imports so `paragraph install` works before heavy deps are in place.
    _map = {
        "extract": ("paragraph.extract", "extract"),
        "collect_files": ("paragraph.extract", "collect_files"),
        "build_from_json": ("paragraph.build", "build_from_json"),
        "cluster": ("paragraph.cluster", "cluster"),
        "score_all": ("paragraph.cluster", "score_all"),
        "cohesion_score": ("paragraph.cluster", "cohesion_score"),
        "god_nodes": ("paragraph.analyze", "god_nodes"),
        "surprising_connections": ("paragraph.analyze", "surprising_connections"),
        "suggest_questions": ("paragraph.analyze", "suggest_questions"),
        "generate": ("paragraph.report", "generate"),
        "to_json": ("paragraph.export", "to_json"),
        "to_html": ("paragraph.export", "to_html"),
    }
    if name in _map:
        import importlib
        mod_name, attr = _map[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module 'paragraph' has no attribute {name!r}")
