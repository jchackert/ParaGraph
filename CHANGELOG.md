# Changelog

## 0.1.0 (fork)

Forked from [graphify 0.5.0](https://github.com/safishamsi/graphify) by Safi Shamsi.

### Changes from upstream

- **Claude Code only** -- removed support for Codex, Cursor, Gemini, Aider, Kiro, OpenCode, Claw, Droid, Trae, Hermes, Copilot, Antigravity, VS Code
- **Renamed** -- package `graphifyy` to `paragraph`, CLI `graphify` to `paragraph`
- **Fix: LLM field normalization** -- `build.py` normalizes wrong field names from LLM extraction (`type` vs `file_type`, `file` vs `source_file`, `type` vs `confidence` on edges) before validation
- **Fix: code-only rebuild** -- `watch.py` passes `force=True` to `to_json` in `_rebuild_code` (semantic nodes preserved internally; node count drop is code ID churn, not data loss)
- **Simplified CLI** -- 1507 lines to ~680, single-platform install
- **Removed** -- 10 platform skill files (~460KB), `worked/` examples, `docs/translations/` (27 files)

### Upstream changelog

See [graphify releases](https://github.com/safishamsi/graphify/releases) for pre-fork history.
