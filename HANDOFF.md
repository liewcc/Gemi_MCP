# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task — Rename data directory core/ to runtime/ COMPLETE ✓

**Status:** Renamed `core/` to `runtime/` to eliminate visual confusion with the `engine/core/` submodule code directory. Configured the engine to accept `BROWSER_ENGINE_DATA_SUBDIR` to retain compatibility with other projects like GemiPersonaPro_DT.

**Changes:**
- Defined `DATA_SUBDIR = os.getenv("BROWSER_ENGINE_DATA_SUBDIR", "core")` in `engine_service.py` and updated references.
- Renamed the project folder `core/` to `runtime/`.
- Updated `tui/app.py` to set `BROWSER_ENGINE_DATA_SUBDIR="runtime"` when launching the service subprocess, and updated import/clean paths.
- Updated `setup.bat`, `reorganize_profiles.py`, `.gitignore` to point to `runtime/` instead of `core/`.
- Updated `CLAUDE.md`, `AGENTS.md`, `README.md`, `README.zh-CN.md` to reference `runtime/` and update Git Safety rules.

## Previous Task — Chrome Profile Reorganization COMPLETE ✓

**Status:** Completed clean renumbering of Chrome profile directories and updated `Local State` JSON.

**Changes:**
- Removed duplicate dapmuar profile (deleted `Profile 16`, kept `Profile 15`).
- Removed duplicate ccliew.email profile (deleted `Profile 26`, kept `Profile 27`).
- Renamed the remaining 23 directories to `Profile 1` through `Profile 23` in exact lowest-to-highest order.
- Updated `core/browser_user_data/Local State` JSON by removing keys `'Profile 1'`, `'Profile 16'`, `'Profile 26'`, renaming other keys, preserving JSON format, and setting `profile.last_used = "Profile 23"`.
- Verified successful directory renumbering and JSON keys matching on disk.


## Previous Task — Targeted bug fix for Chrome profile directory existence check COMPLETE ✓

**Status:** Fixed active profile detection and switch logic issues related to multiple profiles with the same email pointing to deleted folders.

**Changes (`engine/core/engine_service.py`):**
1. **Startup profile detection:** Removed `break` and added `os.path.exists(p_path)` check.
2. **`perform_switch_logic`:** Removed `break` and added `os.path.exists(p_path)` check.

## Previous Task — Bug fixes (Timeout for playwright stop + Normalize profile username) COMPLETE ✓

**Status:** Both bug fixes implemented, syntax-verified via `py_compile`.

**Changes:**
1. **Prevent playwright.stop() hang (`engine/core/browser_engine.py`):** Wrapped `await self._reg_playwright.stop()` with `asyncio.wait_for(..., timeout=5.0)` and caught `asyncio.TimeoutError` or other exceptions.
2. **Normalize username before saving (`tui/app.py`):** Strip the `@domain` suffix from `email` before storing it as `username` via `email.split("@")[0]`.

## Previous Task — Redesign Add Profile flow + Fix Delete cleanup COMPLETE ✓

**Status:** Both changes implemented, syntax-verified via `py_compile`.

**Changes (`tui/app.py`):**
1. **Add Profile modal:** Removed broken `btn-add-account` button. `btn-add-profile` now pushes a `ModalScreen` that collects the email, stops any running browser/registration, checks for duplicate accounts, writes the new entry to `user_login_lookup.json`, opens the registration browser, and recomposes both EngineTab and AccountsTab.
2. **Delete cleanup:** `_delete_account()` now reads `core/browser_user_data/Local State`, finds the Chrome profile key matching the deleted username (case-insensitive local-part), and removes the profile directory with `shutil.rmtree`.

## Previous Task — Register close handler on browser context to handle manual close COMPLETE ✓

**Status:** Successfully registered close event listener to set engine status properly on manual browser close and handled re-entry / exceptions.

**Changes:**
- `engine/core/browser_engine.py`: Added close listener to `self._context` in `start()`.
- `engine/core/browser_engine.py`: Set `self.is_running = False` immediately at the beginning of `stop()`, and wrapped cleanup logic in `try/except` blocks to safely swallow exceptions on already closed resources.

## Previous Task — COMPLETE ✓ (DeepSeek provider fully working)

**Status:** All DeepSeek features implemented, tested, and committed.

**What works:**
- `switch_service("deepseek")` → navigates to chat.deepseek.com ✓
- `new_chat()` → ignores config.json Gemini URL, always uses BASE_URL ✓
- `send_chat()` → content-stabilization poll (3×0.8s), correct selectors ✓
- `attach_files()` → upload completion via `ds-button--disabled` class ✓
- `apply_settings()` → Instant/Expert/Vision mode toggle via span text ✓
- `redo_response()` → SVG path `d*="7.92136"` fingerprint + stabilization poll ✓ (TESTED LIVE)
- `get_last_response()` → reads `div.ds-markdown`, checks primary btn SVG state ✓
- `stop_response()` → SVG path check to distinguish stop vs send state ✓

**Correct attach+send flow (important):**
```
new_chat() → attach_files([...]) → send_chat(new_conversation=False)
```
Do NOT use `new_conversation=True` with attachments — it calls `new_chat()` internally → clears files.

**Committed:** engine submodule `a4b41b3`

---

## Previous Task — TOOL & MODEL SELECTION (needs end-to-end verification)

**Feature:** Live-scan Gemini web UI to discover available models / thinking levels / tools,
display them in TUI dropdowns, apply selected settings before each conversation.

**Status:** All known bugs fixed. Needs a live run to confirm apply flow works end-to-end.

### All fixes applied (committed)

| Fix | Engine commit | Status |
|-----|--------------|--------|
| Arrow fn body → expression (discovery crash) | `7e0b719` | ✓ Done |
| Read user selections BEFORE set_options (wrong defaults) | TUI commit `59e60ea` | ✓ Done |
| sleep(0.3) → sleep(1.0) after Escape (overlay still active) | `6855a8e` | ✓ Done |
| Multi-selector for toolbox drawer open | `6855a8e` | ✓ Done |
| Main repo submodule pointer updated | `5f0ef6a` | ✓ Done |

---

## Architecture (what was built this session)

### Data flow
```
/browser/discover  →  discover_capabilities()  →  {main_tools, sub_tools, models, thinking_levels}
                                                              ↓
                              TUI._discovered dict  ←  _discover_capabilities() worker
                                      ↓
              _update_discovered_selects()  →  set_options() on #sel-tool, #sel-model, #sel-thinking
                                      ↓
              on_sel_tool_changed() event  →  set_options() on #sel-upload (dynamic sub-menu)
```

### Key design decisions
- **No caching**: `_discovered` is never saved to `config.json`. Always live-scanned.
- **Apply = Discover + Validate + Apply + Save**: clicking Apply button first rescans the DOM,
  updates selects (preserving valid saved values, clearing invalid ones), then calls `/browser/apply_settings`.
- **No `recompose()`**: replaced with `_update_discovered_selects()` which only touches the 4
  Select widgets — avoids button state reset (bug where Start Engine / Start Browser flipped to "Start").
- **Combine Submit auto-applies**: `_action_combine_submit` reads current TUI values and calls
  `/browser/apply_settings` before new_chat (no extra scan, fast path).

### Tool menu structure (confirmed via DOM debug)
Gemini's tool drawer order (mirrored in `sel-tool`):
1. Upload files
2. Add from Drive
3. More uploads → (sub-menu in `sel-upload`: Photos, Notebooks)
4. Create image
5. Canvas
6. More tools → (sub-menu in `sel-upload`: Create music, Guided learning)

**Critical finding about Photos/Notebooks DOM:**
- After clicking `button.more-upload-button`, Photos and Notebooks appear DIRECTLY in the same
  `mat-action-list` — there is NO `.more-uploads-list` container in the DOM.
- Photos: text in `span.menu-text.gem-menu-item-label`
- Notebooks: text in `span.gem-menu-item-label` (WITHOUT `.menu-text` class) — this is why
  earlier selectors missed it.
- Fix: read all `mat-action-list button[role="menuitem"]` before and after clicking more-upload,
  diff the two lists → new items are Photos/Notebooks.

### Files changed this session
| File | What changed |
|------|-------------|
| `engine/core/providers/gemini.py` | `discover_capabilities`: new `main_tools`/`sub_tools` return, 4-step ordered scan. `apply_settings`: added `thinking_level` param + implementation, 4-pass tool click logic |
| `engine/core/engine_service.py` | `SettingsRequest`: added `thinking_level` field |
| `tui/app.py` | `EngineTab.compose`: uses `main_tools`/`sub_tools`; `on_sel_tool_changed`: dynamic sub-menu; `_update_discovered_selects`: replaces `recompose()`; `_apply_model_tool`: Discover→Validate→Apply→Save; `_action_combine_submit`: auto-applies before chat; `_discover_capabilities`: logs to `_append_log` not notify |

---

## TOOL & MODEL SELECTION — VERIFIED ✓

Full end-to-end test passed (2026-06-18):
- Discovery: 3 models, 6 main tools, sub_tools = {More uploads: [Photos, Notebooks], More tools: [Create music, Guided learning]}
- Apply: model=ok, thinking=ok, tool=ok (including Notebooks sub-menu item)

---

## Done (earlier tasks — unchanged)
- [x] TUI: Added "Auto Del" / "Range" controls to AccountsTab account cards
- [x] TUI: ENGINE OPERATIONS panel ported from GemiPersonaPro_DT
- [x] TUI: ACCOUNT ACTIONS panel ported
- [x] TUI: RichLog → TextArea for log copying
- [x] Engine cleanup via Windows Job Object
- [x] SERVICE LOG live output fixed
- [x] GitHub public repo published
- [x] TUI: Fixed bug where options update reset user selection (read choices before set_options)
- [x] TUI: Added "Update & Relaunch" button to ENGINE OPERATIONS and implemented `_update_and_relaunch` worker
- [x] Complete `gemi-mcp` MCP tool surface: exposed `discover_capabilities` and `new_chat` tools in `mcp/server.py`.
- [x] Fix `send_chat` state management in `engine/core/providers/gemini.py` (pre-flight checks, overlay dismissal, busy checks, refusal/quota keyword scanning).
- [x] TUI: Replaced git-based update detection with JSON version comparison (version.json)
- [x] TUI: Fixed "Update & Relaunch" producing black/grey blocks — see **Decisions & Pitfalls** below

## In Progress
- [ ] **Update & Relaunch — needs live verification** (fix applied 2026-06-19, not yet tested with a real update)
  - The fix is in `tui/app.py`. After the next successful update run, mark this done - [x] Conducted detailed compatibility and structural analysis for Task A (submodule integration) and Task B (processing_utils.py relocation). Provided recommendation report.
- [x] Deleted 5 dead-code files from `core/` directory (`browser_engine.py`, `engine_service.py`, `config_utils.py`, `api_client.py`, `health_parser.py`) and verified that only `processing_utils.py` remains.
- [x] Reorganized Chrome profile directories to cleanly number them 1 to 23, deleted duplicate profiles (dapmuar Profile 16, ccliew Profile 26), and updated Local State JSON.
- [x] Prevented engine restart if the browser is already running by adding an early-return check to the `/engine/start` endpoint.
- [x] Redesigned Add Profile flow: ModalScreen-based email input → duplicate check → write to JSON → open registration browser → recompose tabs.
- [x] Fixed Delete to also remove Chrome profile cookies directory via `Local State` lookup + `shutil.rmtree`.
- [x] Updated Chinese README.zh-CN.md to align with English version, including Gemini & DeepSeek support details, new setup bullet, 📋 Add profile flow, switching/verification details, and troubleshooting updates.
- [x] Wrote a step-by-step Chrome profile reorganization Python script to reorganize_profiles.py without running or committing, following user specifications.
- [x] Edited tui/app.py to set BROWSER_ENGINE_PROJECT_ROOT environment variable to ensure project root is correctly identified by config_utils before imports.

## Done (previous session — 2026-06-20)
- [x] DeepSeek new_chat refactor for verification readiness — refactored the method to detect input visibility first (15s timeout) to handle Cloudflare or login challenges. Relaunches as headed browser only if input is not ready, allowing more robust session flow. Verified compilation.
- [x] DeepSeek headed login relaunch — updated the login interception inside `new_chat` to stop the headless browser instance and relaunch it as a headed browser (`headless=False`), explicitly navigate to DeepSeek, and restore window state to `"normal"` using CDP. Verified compilation.
- [x] DeepSeek login detection — implemented `_check_login` using DOM hierarchy walking from the avatar (`img[src*="user-avatar"]`) to extract username, and updated `new_chat` to enforce the login state. Modified `send_chat` to handle and propagate `login_required` status when starting a new conversation. Tested syntax compilation.
- [x] DeepSeek redo_response implementation — queried DeepSeek to identify the regenerate button's stable circular arrow SVG path (`svg path[d*="7.92136"]`) in DOM debug, and implemented `redo_response` in deepseek.py with a content-stabilization poll. Verified compilation.
- [x] Service-switching mechanism & DeepSeek provider — added ability to switch between Gemini and DeepSeek providers at runtime via `/browser/switch_service` route and `switch_service` MCP tool. Implemented `DeepSeekProvider` in `engine/core/providers/deepseek.py`. Not committed/pushed as requested.
- [x] `get_last_response` MCP tool — implemented across all 3 layers (gemini.py, engine_service.py, mcp/server.py), smoke tested ✓
- [x] Version-based update detection — replaced git commit-count with `version.json` semver comparison in both Gemi_MCP (tui/app.py) and GemiPersonaPro_DT (app/main.js + preload.js). Gemi_Engine now has shared version.json.


---

## Implemented Feature — `get_last_response` (Claude timeout resilience) ✓

**Problem:** When Claude delegates a long query to gemi via `send_chat`, the MCP call
times out before Gemini finishes thinking. Claude sees a timeout error and has no way to
know if the response is still being generated or was lost. This causes Claude to either
give up or re-send the same prompt (wasting quota and causing duplicate context).

**Root cause:** `send_chat` blocks until Gemini returns a complete response. Extended
thinking (Gemini 3.5 Flash + Extended level) can take 60–120 s, which exceeds the MCP
call timeout.

**Proposed solution: add a `get_last_response` tool**

The Gemini browser tab continues rendering even after the MCP call times out. A new tool
that reads the current DOM state of the last assistant message allows Claude to poll for
the result without re-submitting the prompt.

### Implementation sketch

**`engine/core/providers/gemini.py`** — add method:
```python
async def get_last_response(self) -> dict:
    """Read the current text of the last model-response element in the DOM.
    Returns partial text if Gemini is still generating; empty string if none found."""
    try:
        text = await self._page.evaluate("""
            () => {
                const els = document.querySelectorAll('model-response');
                if (!els.length) return '';
                const last = els[els.length - 1];
                return last.innerText || last.textContent || '';
            }
        """)
        # Check if Gemini is still generating (stop button visible)
        still_generating = await self._page.is_visible('button[aria-label="Stop response"]')
        return {"text": text.strip(), "done": not still_generating}
    except Exception as e:
        return {"text": "", "done": False, "error": str(e)}
```

**`engine/core/engine_service.py`** — add route:
```python
@app.get("/browser/last_response")
async def get_last_response():
    result = await engine.get_last_response()
    return {"status": "success", **result}
```

**`mcp/server.py`** — add tool:
```python
@mcp.tool()
async def get_last_response() -> str:
    """Read whatever Gemini has generated so far in the current chat.

    Use this after send_chat times out — the browser tab may still be generating.
    Returns the current response text and a 'done' flag.
    Poll every few seconds until done=True.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{ENGINE_URL}/browser/last_response", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    done = data.get("done", False)
    text = data.get("text", "")
    return f"done={done}\n\n{text}"
```

### Claude's usage pattern (after this tool exists)
```
send_chat(prompt, timeout=30) → TimeoutError
    ↓ do NOT retry
get_last_response() → {"done": False, "text": "...partial..."}
    ↓ wait ~10s
get_last_response() → {"done": True, "text": "...full answer..."}
    ↓ use the answer
```

### Files to touch (in order)
1. `engine/core/providers/gemini.py` — add `get_last_response()`
2. `engine/core/engine_service.py` — add `/browser/last_response` GET route
3. `mcp/server.py` — add `get_last_response` MCP tool

## Decisions & Pitfalls

### Update & Relaunch — terminal corruption bug (fixed 2026-06-19)
**Symptom:** After clicking "Update & Relaunch", the DOS window shows black/grey blocks instead of
the normal TUI. Must manually close and re-open to recover.

**Root cause:** The original code called `os.execv()` while Textual was still running. On Windows,
`execv` replaces the process without giving Textual a chance to run its cleanup (close alternate
screen, restore terminal raw-mode). The new process inherited a corrupted terminal state.

**Fix applied (`tui/app.py`):**
1. `_update_and_relaunch` now sets `self._relaunch_on_exit = True` then calls `self.exit()` instead
   of `os.execv()` directly. This lets Textual shut down cleanly and restore the terminal.
2. `__main__` block changed to `app = GemiTUI(); app.run()` and after `run()` returns checks the
   flag — only then calls `os.execv()`, at which point the terminal is clean.

**Status:** Code fixed, awaiting next real update run to confirm it works end-to-end.
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or `data/*.json`
- Two gemini.py files exist: `core/providers/gemini.py` (project) vs `engine/core/providers/gemini.py`
  (submodule, actually used at runtime). Always edit the ENGINE version.
- `_update_discovered_selects()` must NOT call `recompose()` — that resets button states.
- Textual `Select.set_options()` preserves current value if still valid, clears it otherwise.
- This is the validation behavior for "confirm saved settings still in menu".

## Next Steps (queued — 2026-06-21)

### A. ~~Engine submodule compatibility with GemiPersonaPro_DT~~ — NOT A TASK
Both Gemi_MCP and GemiPersonaPro_DT already use Gemi_Engine as a shared submodule.
DT has its own update notification UI — user clicks to bump. No manual work needed here.
(HANDOFF description was stale/wrong; deleted 2026-06-21.)

### B. Eliminate core/ as a code directory — use engine/core/ exclusively
**Goal:** Remove the last ambiguity: core/ should be data-only (browser_user_data, engine.log).

**Remaining blocker:** `core/processing_utils.py` is needed by engine at runtime via PYTHONPATH=core/.
It contains image/model processing code (GemiPersonaPro_DT-specific, not generic engine logic).

Options:
1. Move processing_utils.py → engine/core/ (becomes part of submodule, shared with GemiPersonaPro_DT)
2. Keep it in core/ but rename directory to avoid confusion (e.g. core/local_lib/)
3. Make it a separate package at repo root level

Constraint: Whatever solution must work for BOTH Gemi_MCP and GemiPersonaPro_DT
(since engine submodule may eventually be shared).

**Pitfall to avoid:** core/ also has browser_session_sandbox/ and browser_user_data/ —
these are data dirs and must NEVER be deleted regardless of code cleanup.

## Last Updated
2026-06-21 by Google Antigravity (Gemini 3.5 Flash)

