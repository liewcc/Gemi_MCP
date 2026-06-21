# Gemi_MCP — Agent Instructions

## 1. Project Purpose

Browser-automation engine that drives Gemini and DeepSeek web UIs (no API keys required),
exposed as an MCP server so AI assistants (Claude Code, Cursor, etc.) can delegate tasks to
these free web-based AI services.

## 2. Language Rules

- **Chat:** Communicate with the user in **Chinese**, mixing in English for technical terms.
- **Code comments / docstrings:** **English** only.
- **Documentation & explanation files** (`*.md`, design notes, `HANDOFF.md`, etc.): **English** only.
  - **Exception:** end-user-facing README translations are allowed (e.g. `README.zh-CN.md`).
    Keep `README.md` (English) as the canonical version and link translations from its top.

## 3. Directory Layout

```
runtime/
  processing_utils.py   ← Shared image processing utilities
  browser_session_sandbox/  ← Account cookies (gitignored, never commit)
  browser_user_data/        ← Browser profile (gitignored, never commit)
data/
  config.json           ← Runtime config (gitignored)
  user_login_lookup.json← Account list (gitignored)
engine/
  core/
    engine_service.py   ← FastAPI REST wrapper around BrowserEngine
    browser_engine.py   ← Playwright automation core (Gemini web UI)
mcp/
  server.py             ← MCP server (exposes engine as MCP tools)
```

## 4. Git Safety

- **NEVER** commit `runtime/browser_session_sandbox/`, `runtime/browser_user_data/`, or any `data/*.json`.
- Always run `git status` before committing.

## 5. Code Edit Quality

- Before applying any edit, verify bracket/brace balance.
- Prefer rewriting entire functions over patching for structural changes.

## 6. Session Handoff Protocol (会话交接协议 — IMPORTANT)

This project may be worked on alternately by **Claude Code** and **Google Antigravity**.
The two AIs share NO memory — the only shared state is the git repo + `HANDOFF.md`.

1. **At session start (开工第一件事):**
   - Read `HANDOFF.md` (current in-flight task baton).
   - Read `MAINTENANCE.md` (the maintenance diary — what was last fixed/changed and why,
     plus known gotchas). This tells you the latest maintenance state of the repo.
   - Run `git log --oneline -15` to see what the previous AI actually did.
   - Do NOT assume state from memory — trust `HANDOFF.md` + `MAINTENANCE.md` + git history.

2. **Before ending a session, or when quota is about to run out (收工前 / 额度快用完):**
   - Commit your work with a clear message.
   - Update `HANDOFF.md`: Done / In Progress / Next Steps / Decisions.
   - If you made a maintenance change (bug fix, behavior change, infra tweak), add a top entry
     to `MAINTENANCE.md` using the template at the bottom of that file.
   - Sign the "Last Updated" line with your name + date.

3. `HANDOFF.md` = progress on the current task. `MAINTENANCE.md` = durable log of what changed
   across the repo's life. Keep both short and current.
