# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task
TUI improvements.

## Done
- [x] Create `HANDOFF.md` handoff file (this file)
- [x] Add the session handoff protocol to `CLAUDE.md`
- [x] Create `AGENTS.md` so Antigravity auto-loads the same rules
- [x] TUI: Replace `RichLog` with read-only `TextArea` in SERVICE LOG panel so users can mouse-select and copy log text (Ctrl+C)

## In Progress / Blocked
- (none)

## Next Steps
1. Test it: do some work in Claude -> update this file -> open Antigravity and check it can pick up.
2. Test the reverse direction (Antigravity -> Claude).
3. Consider adding more TUI features (search in logs, log filtering, etc.)

## Decisions & Pitfalls
- The only shared state is the disk / git repo; the two AIs cannot share memory directly,
  so all state must live in this file + git history.
- When quota is about to run out, proactively say "update the handoff file" — do not wait for a hard stop.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- TUI SERVICE LOG: Switched from `RichLog` to `TextArea(read_only=True)` because `RichLog` does not support mouse text selection. `TextArea` in read-only mode supports click+drag selection and Ctrl+C copy natively.

## Last Updated
2026-06-15 by Antigravity
