# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task
(none — engine-cleanup-on-close fix landed and verified; idle, awaiting next task)

## Done
- [x] Create `HANDOFF.md` handoff file (this file)
- [x] Add the session handoff protocol to `CLAUDE.md`
- [x] Create `AGENTS.md` so Antigravity auto-loads the same rules
- [x] TUI: Replace `RichLog` with read-only `TextArea` in SERVICE LOG panel so users can mouse-select and copy log text (Ctrl+C)
- [x] Verified handoff both directions: Claude -> Antigravity (TUI change picked up) and Antigravity -> Claude (work confirmed against git + code). Mechanism works.
- [x] TUI: Engine no longer survives a window 'X' close. Engine subprocess is now assigned to a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS kills it whenever the TUI process dies (graceful quit, window 'X', or crash). Verified: closing the job handle terminates the child. (`tui/app.py`)

## In Progress / Blocked
- (none)

## Next Steps
- (none pending) Possible future idea: more TUI features (log search / filtering).
- Edge case not yet handled: if the engine was already running on :18800 *before* the TUI
  started (`already_up` branch), the TUI has no handle to it, so it is NOT in the Job Object.
  Only graceful quit (netstat/taskkill fallback) cleans it up. Could adopt it via
  `OpenProcess(pid)` + `AssignProcessToJobObject` if this becomes a problem.

## Decisions & Pitfalls
- The only shared state is the disk / git repo; the two AIs cannot share memory directly,
  so all state must live in this file + git history.
- When quota is about to run out, proactively say "update the handoff file" — do not wait for a hard stop.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- TUI SERVICE LOG: Switched from `RichLog` to `TextArea(read_only=True)` because `RichLog` does not support mouse text selection. `TextArea` in read-only mode supports click+drag selection and Ctrl+C copy natively.
- TUI engine cleanup: `atexit`/signal handlers are unreliable on Windows window-close (process is force-killed before they run). The Job Object approach is OS-enforced and fires on *any* parent death — the only robust fix. No extra dependency; implemented with `ctypes`. The child must NOT be launched with `CREATE_BREAKAWAY_FROM_JOB` (it isn't).

## Last Updated
2026-06-15 by Claude
