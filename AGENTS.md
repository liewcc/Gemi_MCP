# Agent Instructions (Google Antigravity)

This project is worked on alternately by **Google Antigravity** and **Claude Code**.
The two AIs share NO memory. The only shared state is the git repo + `HANDOFF.md`.

## Language Rules
- **Chat:** Communicate with the user in **Chinese**, mixing in English for technical terms.
- **Code comments / docstrings:** **English** only.
- **Documentation & explanation files** (`*.md`, design notes, `HANDOFF.md`, etc.): **English** only.

## Session Handoff Protocol (IMPORTANT)

1. **At session start:**
   - Read `HANDOFF.md`.
   - Run `git log --oneline -15` to see what the previous AI actually did.
   - Do NOT assume state from memory — trust `HANDOFF.md` + git history.

2. **Before ending a session, or when quota is about to run out:**
   - Commit your work with a clear message.
   - Update `HANDOFF.md`: Done / In Progress / Next Steps / Decisions.
   - Sign the "Last Updated" line with your name + date.

3. `HANDOFF.md` is the single source of truth for progress. Keep it short and current.

## Git Safety
- **NEVER** commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- Always run `git status` before committing.

## Project Purpose
Browser-automation engine that drives Gemini and DeepSeek web UIs (no API keys required),
exposed as an MCP server so AI assistants can delegate tasks to these free web-based AI services.
See `CLAUDE.md` for the full directory layout and code-quality rules.
