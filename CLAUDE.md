# Gemi_MCP — Agent Instructions

## 1. Project Purpose

Browser-automation engine that drives Gemini and DeepSeek web UIs (no API keys required),
exposed as an MCP server so AI assistants (Claude Code, Cursor, etc.) can delegate tasks to
these free web-based AI services.

## 2. Language Rules

- **UI / code:** All labels, comments, and docstrings in **English**.
- **Chat:** Communicate with the user in **Chinese**, mixing English for technical terms.

## 3. Directory Layout

```
core/
  browser_engine.py     ← Playwright automation core (Gemini web UI)
  engine_service.py     ← FastAPI REST wrapper around BrowserEngine
  config_utils.py       ← Config read/write helpers
  api_client.py         ← HTTP client for calling engine_service
  browser_session_sandbox/  ← Account cookies (gitignored, never commit)
  browser_user_data/        ← Browser profile (gitignored, never commit)
data/
  config.json           ← Runtime config (gitignored)
  user_login_lookup.json← Account list (gitignored)
mcp/
  server.py             ← MCP server (exposes engine as MCP tools)
```

## 4. Git Safety

- **NEVER** commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- Always run `git status` before committing.

## 5. Code Edit Quality

- Before applying any edit, verify bracket/brace balance.
- Prefer rewriting entire functions over patching for structural changes.
