"""Tests for watch.py - file watcher helpers (no watchdog required)."""
import time
from pathlib import Path
import pytest

from paragraph.watch import _notify_only, _WATCHED_EXTENSIONS


# --- _notify_only ---

def test_notify_only_creates_flag(tmp_path):
    _notify_only(tmp_path)
    flag = tmp_path / "graphify-out" / "needs_update"
    assert flag.exists()
    assert flag.read_text() == "1"

def test_notify_only_creates_flag_dir(tmp_path):
    # graphify-out dir does not exist yet
    assert not (tmp_path / "graphify-out").exists()
    _notify_only(tmp_path)
    assert (tmp_path / "graphify-out").is_dir()

def test_notify_only_idempotent(tmp_path):
    _notify_only(tmp_path)
    _notify_only(tmp_path)
    flag = tmp_path / "graphify-out" / "needs_update"
    assert flag.read_text() == "1"


# --- _WATCHED_EXTENSIONS ---

def test_watched_extensions_includes_code():
    assert ".py" in _WATCHED_EXTENSIONS
    assert ".ts" in _WATCHED_EXTENSIONS
    assert ".go" in _WATCHED_EXTENSIONS
    assert ".rs" in _WATCHED_EXTENSIONS

def test_watched_extensions_includes_docs():
    assert ".md" in _WATCHED_EXTENSIONS
    assert ".txt" in _WATCHED_EXTENSIONS
    assert ".pdf" in _WATCHED_EXTENSIONS

def test_watched_extensions_includes_images():
    assert ".png" in _WATCHED_EXTENSIONS
    assert ".jpg" in _WATCHED_EXTENSIONS

def test_watched_extensions_excludes_noise():
    assert ".json" not in _WATCHED_EXTENSIONS
    assert ".pyc" not in _WATCHED_EXTENSIONS
    assert ".log" not in _WATCHED_EXTENSIONS


# --- watch() import error without watchdog ---

def test_check_update_no_flag_returns_true(tmp_path):
    """check_update returns True and is silent when needs_update flag is absent."""
    from paragraph.watch import check_update
    assert check_update(tmp_path) is True


def test_check_update_with_flag_returns_true_and_prints(tmp_path, capsys):
    """check_update returns True and prints notification when flag exists."""
    from paragraph.watch import check_update
    flag = tmp_path / "graphify-out" / "needs_update"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1")
    result = check_update(tmp_path)
    assert result is True
    out = capsys.readouterr().out
    assert "paragraph --update" in out


def test_check_update_does_not_clear_flag(tmp_path):
    """check_update never removes the needs_update flag (clearing is LLM's job)."""
    from paragraph.watch import check_update
    flag = tmp_path / "graphify-out" / "needs_update"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1")
    check_update(tmp_path)
    assert flag.exists()


def test_watch_raises_without_watchdog(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "watchdog.observers" or name == "watchdog.events":
            raise ImportError("mocked missing watchdog")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from paragraph.watch import watch
    with pytest.raises(ImportError, match="watchdog not installed"):
        watch(tmp_path)


def test_rebuild_code_carries_semantic_labels(tmp_path):
    """A Claude-written community label in graph.json survives an LLM-free rebuild."""
    import json
    from paragraph.watch import _rebuild_code

    (tmp_path / "app.py").write_text(
        "class AuthManager:\n"
        "    def login(self):\n"
        "        return validate()\n\n"
        "def validate():\n"
        "    return True\n"
    )
    assert _rebuild_code(tmp_path) is True
    graph_path = tmp_path / "graphify-out" / "graph.json"
    data = json.loads(graph_path.read_text())
    labels = data.get("graph", {}).get("community_labels")
    assert labels, "rebuild must persist community labels"
    assert not all(v.startswith("Community ") for v in labels.values()), \
        "labels must be member-based, not placeholders"

    # Simulate a semantic label written by a full /paragraph run
    for cid in labels:
        labels[cid] = "Authentication Layer"
    data["graph"]["community_labels"] = labels
    graph_path.write_text(json.dumps(data))

    assert _rebuild_code(tmp_path) is True
    data2 = json.loads(graph_path.read_text())
    assert "Authentication Layer" in data2["graph"]["community_labels"].values()
