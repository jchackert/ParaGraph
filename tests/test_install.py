"""Tests for `paragraph install` — the Claude-only global skill install.

This fork supports only Claude Code. install() copies the packaged skill.md
to ~/.claude/skills/paragraph/SKILL.md (or $CLAUDE_CONFIG_DIR/skills/...),
stamps .paragraph_version, and registers the skill in ~/.claude/CLAUDE.md.

Project-level CLAUDE.md + settings.json hook install is covered by
test_claude_md.py; git hook install is covered by test_hooks.py.
"""
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_config_dir_env(monkeypatch):
    """Ensure the ambient CLAUDE_CONFIG_DIR never leaks into these tests."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _install(tmp_path):
    from paragraph.__main__ import install
    with patch("paragraph.__main__.Path.home", return_value=tmp_path):
        install()


def test_install_copies_skill(tmp_path):
    """install() places SKILL.md under ~/.claude/skills/paragraph/."""
    _install(tmp_path)
    dst = tmp_path / ".claude" / "skills" / "paragraph" / "SKILL.md"
    assert dst.exists()
    import paragraph
    src = Path(paragraph.__file__).parent / "skill.md"
    assert dst.read_text() == src.read_text()


def test_install_writes_version_stamp(tmp_path):
    """install() stamps .paragraph_version with the package version."""
    from paragraph.__main__ import __version__
    _install(tmp_path)
    stamp = tmp_path / ".claude" / "skills" / "paragraph" / ".paragraph_version"
    assert stamp.exists()
    assert stamp.read_text().strip() == __version__


def test_install_creates_claude_md_registration(tmp_path):
    """install() creates ~/.claude/CLAUDE.md with the skill trigger when absent."""
    _install(tmp_path)
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "/paragraph" in content
    assert "SKILL.md" in content


def test_install_appends_to_existing_claude_md(tmp_path):
    """install() preserves existing global CLAUDE.md content."""
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# My rules\n\nDo not break things.\n")
    _install(tmp_path)
    content = claude_md.read_text()
    assert "Do not break things." in content
    assert "/paragraph" in content


def test_install_registration_idempotent(tmp_path):
    """Installing twice does not duplicate the CLAUDE.md registration."""
    _install(tmp_path)
    _install(tmp_path)
    content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
    assert content.count("# paragraph") == 1


def test_install_skill_copy_idempotent(tmp_path):
    """Re-running install() refreshes the skill file without error."""
    _install(tmp_path)
    dst = tmp_path / ".claude" / "skills" / "paragraph" / "SKILL.md"
    dst.write_text("stale contents")
    _install(tmp_path)
    assert dst.read_text() != "stale contents"  # refreshed from package


def test_install_respects_claude_config_dir(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR overrides the skill destination directory."""
    config_dir = tmp_path / "custom-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    home = tmp_path / "home"
    home.mkdir()
    from paragraph.__main__ import install
    with patch("paragraph.__main__.Path.home", return_value=home):
        install()
    assert (config_dir / "skills" / "paragraph" / "SKILL.md").exists()
    # Skill must NOT also land in the default home location
    assert not (home / ".claude" / "skills" / "paragraph" / "SKILL.md").exists()


def test_skill_file_exists_in_package():
    """The Claude skill file must ship inside the installed package."""
    import paragraph
    assert (Path(paragraph.__file__).parent / "skill.md").exists()


def test_install_takes_no_platform_argument():
    """The fork is Claude-only: install() must not accept a platform kwarg."""
    from paragraph.__main__ import install
    with pytest.raises(TypeError):
        install(platform="gemini")
