# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task
(none — SERVICE LOG live-output fix landed and verified; idle, awaiting next task)

## Done
- [x] Create `HANDOFF.md` handoff file (this file)
- [x] Add the session handoff protocol to `CLAUDE.md`
- [x] Create `AGENTS.md` so Antigravity auto-loads the same rules
- [x] TUI: Replace `RichLog` with read-only `TextArea` in SERVICE LOG panel so users can mouse-select and copy log text (Ctrl+C)
- [x] Verified handoff both directions: Claude -> Antigravity (TUI change picked up) and Antigravity -> Claude (work confirmed against git + code). Mechanism works.
- [x] TUI: Engine no longer survives a window 'X' close. Engine subprocess is now assigned to a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS kills it whenever the TUI process dies (graceful quit, window 'X', or crash). Verified: closing the job handle terminates the child. (`tui/app.py`)
- [x] SERVICE LOG live output fixed & verified. Logs (`[ENGINE]`/`[AUTO]`/`API>>`) now stream in real time, including the startup login. Five distinct root causes, all fixed:
  1. **stdout buffering** — piped child stdout is block-buffered, so `print()` never flushed. Fixed: `sys.stdout/stderr.reconfigure(line_buffering=True)` in `engine_service.py` + `-u` flag on the spawn in `tui/app.py`.
  2. **`API>>` only went to file/queue** — `_log_debug` now also `print(log_msg, flush=True)` so it reaches stdout (`browser_engine.py`).
  3. **Textual worker cancellation** — all `@work`s shared the default group; the `exclusive=True` pollers cancelled the stdout streaming worker ~2s after mount. Fixed: gave each worker its own `group=` (`svc_stream` / `poll_logs` / `poll_status`).
  4. **No startup login trigger (the actual "startup is blank" cause)** — the `auto_start_browser` setting was an orphan (defined + a UI switch, but no code consumed it), so at startup the engine just sat idle. Wired up new `_engine_autostart` worker: waits for `/health`, then POSTs `/engine/start` if enabled.
  5. **Stale/duplicate processes** — multiple TUI instances each spawning an engine on :18800 collide; the window you watch may stream a dead engine. `already_up` branch now taskkills the leftover and respawns its own (always owns a live stdout stream); `_poll_engine_logs` only polls as a fallback when there's no stream (`_service_proc is None`) to avoid duplicate lines.
- [x] Suppressed the Python 3.16 `WindowsProactorEventLoopPolicy` / `set_event_loop_policy` DeprecationWarnings (targeted filter, not a blanket silence) in `engine_service.py`.

## In Progress / Blocked
- (none)

## Next Steps
- (none pending) Possible future idea: more TUI features (log search / filtering).
- Edge case not yet handled: if the engine was already running on :18800 *before* the TUI
  started (`already_up` branch), the TUI has no handle to it, so it is NOT in the Job Object.
  Only graceful quit (netstat/taskkill fallback) cleans it up. Could adopt it via
  `OpenProcess(pid)` + `AssignProcessToJobObject` if this becomes a problem.
- Latent: `engine_service.py` hardcodes port 18800; the sibling project `GemiPersonaPro_DT`
  uses 18000, so they don't clash — but two Gemi_MCP TUIs still fight over 18800. Consider
  making the port configurable (env var / config.json) if multi-instance is ever needed.

## Decisions & Pitfalls
- The only shared state is the disk / git repo; the two AIs cannot share memory directly,
  so all state must live in this file + git history.
- When quota is about to run out, proactively say "update the handoff file" — do not wait for a hard stop.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or any `data/*.json`.
- TUI SERVICE LOG: Switched from `RichLog` to `TextArea(read_only=True)` because `RichLog` does not support mouse text selection. `TextArea` in read-only mode supports click+drag selection and Ctrl+C copy natively.
- TUI engine cleanup: `atexit`/signal handlers are unreliable on Windows window-close (process is force-killed before they run). The Job Object approach is OS-enforced and fires on *any* parent death — the only robust fix. No extra dependency; implemented with `ctypes`. The child must NOT be launched with `CREATE_BREAKAWAY_FROM_JOB` (it isn't).

## Last Updated
2026-06-15 by Claude (SERVICE LOG live-output fix)
