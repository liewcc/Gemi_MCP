# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task
Set up a cross-tool session handoff mechanism (Claude <-> Antigravity).

## Done
- [x] Create `HANDOFF.md` handoff file (this file)
- [x] Add the session handoff protocol to `CLAUDE.md`
- [x] Create `AGENTS.md` so Antigravity auto-loads the same rules

## In Progress / Blocked
- (none)

## Next Steps
1. Test it: do some work in Claude -> update this file -> open Antigravity and check it can pick up.
2. Test the reverse direction (Antigravity -> Claude).

## Decisions & Pitfalls
- The only shared state is the disk / git repo; the two AIs cannot share memory directly,
  so all state must live in this file + git history.
- When quota is about to run out, proactively say "update the handoff file" — do not wait for a hard stop.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.

## Last Updated
2026-06-15 by Claude
