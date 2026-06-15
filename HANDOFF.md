# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task
(none — handoff system is set up and verified; idle, awaiting next task)

## Done
- [x] Create `HANDOFF.md` handoff file (this file)
- [x] Add the session handoff protocol to `CLAUDE.md`
- [x] Create `AGENTS.md` so Antigravity auto-loads the same rules
- [x] TUI: Replace `RichLog` with read-only `TextArea` in SERVICE LOG panel so users can mouse-select and copy log text (Ctrl+C)
- [x] Verified handoff both directions: Claude -> Antigravity (TUI change picked up) and Antigravity -> Claude (work confirmed against git + code). Mechanism works.

## In Progress / Blocked
- (none)

## Next Steps
- (none pending) Possible future idea: more TUI features (log search / filtering).

## Decisions & Pitfalls
- The only shared state is the disk / git repo; the two AIs cannot share memory directly,
  so all state must live in this file + git history.
- When quota is about to run out, proactively say "update the handoff file" — do not wait for a hard stop.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- TUI SERVICE LOG: Switched from `RichLog` to `TextArea(read_only=True)` because `RichLog` does not support mouse text selection. `TextArea` in read-only mode supports click+drag selection and Ctrl+C copy natively.

## Last Updated
2026-06-15 by Claude
