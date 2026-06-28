# Maintenance Log — Gemi_MCP

> Long-term maintenance diary for this repo. **Reverse-chronological** (newest on top).
> One entry per maintenance change: what broke / why, what changed, how it was verified.
> This is the durable record — `HANDOFF.md` is only the current in-flight task baton.
>
> **For AI assistants:** when you finish a maintenance change here, add an entry at the
> top using the template at the bottom. Keep entries factual and short.

---
## 2026-06-28 — Bump version numbers to 1.1.0 (Gemi_MCP) and 1.2.0 (Gemi_Engine)

**Why:** Update version files to reflect the new Dual-Tab Architecture release.
**Changes:**
- `engine/version.json`: Bumped version to `1.2.0` with release notes for the dual-tab feature.
- `version.json`: Bumped version to `1.1.0` with release notes for dual-tab, launcher, and TUI fixes.
**Verified:** Checked the version values in files and successfully pushed to remote repositories.

## 2026-06-28 — Implement Dual-Tab Architecture (Simultaneous Gemini + DeepSeek tabs)

**Why:** Switching services in single-tab mode destroys DOM state (chat history, settings) and forces a full 5–15s page reload. Having both tabs warm concurrently solves this and enables stateless routing.
**Changes:**
- `engine/core/providers/base.py`: Bound each provider to its own tab using `_page_ref`, resolving a critical async background tab crossover concurrency bug.
- `engine/core/browser_engine.py`: Initialized dual pages and pre-warmed both tabs concurrently on start when `BROWSER_ENGINE_DUAL_TAB=true` is set.
- `engine/core/engine_service.py`: Added Pydantic and query param `service` routing support, using a shared `select_service` helper.
- `mcp/server.py`: Propagated `service` to all MCP tools.
- `tui/app.py`: Injected `BROWSER_ENGINE_DUAL_TAB="true"` environment variable when spawning the engine service.
**Verified:** Ran an automated integration test script (`test_dual_tab.py`) that pre-warmed both tabs, performed capability discovery on both, and successfully executed concurrent independent chats.

## 2026-06-22 — Correct Windows Terminal Launcher (run.vbs)

**Why:** Windows Terminal launch fails on some Windows 11 systems due to the app execution alias `wt` not resolving inside VBScript, and global error swallowing hides failures.
**Changes:**
- `run.vbs`: Removed global `On Error Resume Next` to prevent swallowing setup errors. Resolved the absolute path to `wt.exe` via `%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe` and verified its existence before execution. Added robust quoting for launch commands to support directories with space characters and implemented a clear diagnostic dialog (`MsgBox`) if both the primary WT and fallback CMD launches fail.
**Verified:** Manually checked WT path existence and read back verified VBScript execution paths.

## 2026-06-22 — Remove keyword-based refusal detection from send_chat

**Why:** Prevent Gemini's polite apologies and standard identity introductions (containing "sorry" or "language model") from falsely triggering refusal logic in `send_chat`, which previously crashed MCP tool calls.
**Changes:**
- `engine/core/providers/gemini.py`: Removed refusal keyword load/check logic inside `GeminiProvider.send_chat()`, and deleted the Python-side `status == "refused"` result branch. Left `submit_response` completely untouched.
**Verified:** Syntax verified using `py_compile`.

## 2026-06-21 — Rename data directory core/ to runtime/

**Why:** Eliminate visual confusion between Gemi_MCP's `core/` runtime data directory and `engine/core/` submodule code directory.
**Changes:**
- `engine/core/engine_service.py`, `engine/core/providers/gemini.py`, `engine/dom_debugger.py`: Added and wired `BROWSER_ENGINE_DATA_SUBDIR` environment variable support (defaulting to "core" for GemiPersonaPro_DT compatibility).
- Renamed project's `core/` data directory to `runtime/`.
- `tui/app.py`: Updated python path inserts and engine environment variables to pass `BROWSER_ENGINE_DATA_SUBDIR="runtime"`, and renamed data directories references to `runtime/`.
- `setup.bat`: Created directories under `runtime/` instead of `core/`.
- `reorganize_profiles.py`: Modified hardcoded path to `runtime/`.
- `.gitignore`: Updated rules to ignore user data in `runtime/` instead of `core/`.
- `CLAUDE.md`, `AGENTS.md`, `README.md`, `README.zh-CN.md`: Updated directories reference and Git Safety rules.
**Verified:** Ran python compilation checks on `tui/app.py`, `engine/core/engine_service.py`, and `reorganize_profiles.py`. Verified that Git correctly ignores browser data under `runtime/`.

## 2026-06-21 — Set BROWSER_ENGINE_PROJECT_ROOT in app.py

**Why:** Ensure config_utils loaded from engine/core resolves the project root correctly to repo root instead of engine/ root, preventing empty account listings.
**Changes:**
- `tui/app.py`: Added global import of `os` and set `BROWSER_ENGINE_PROJECT_ROOT` environment variable using `ROOT` before inserting to sys.path and importing `config_utils`.
**Verified:** Ran python verification script using standard python executable redirected to log files, outputting the correct path `D:/AI/Gemi_MCP`.

## 2026-06-21 — Remove dead-code files from core directory

**Why:** Clean up unused legacy Python files that are no longer used by the application to reduce complexity and confusion.
**Changes:**
- Deleted 5 unused `.py` files from `core/`: `browser_engine.py`, `engine_service.py`, `config_utils.py`, `api_client.py`, and `health_parser.py`.
**Verified:** Verified that only `processing_utils.py` remains in the `core/` directory as a `.py` file.

## 2026-06-21 — Reorganize Chrome browser profiles

**Why:** Clean up duplicate user profiles (dapmuar and ccliew.email) and rename/renumber directories to `Profile 1` through `Profile 23` consistently to keep files tidy and prevent conflicts.
**Changes:**
- Cleaned up duplicate profile directories: deleted `Profile 16` (duplicate of `Profile 15`) and `Profile 26` (duplicate of `Profile 27`).
- Renamed the remaining 23 directories to `Profile 1` through `Profile 23` in lowest-to-highest order.
- Modified `core/browser_user_data/Local State` JSON structure under `profile.info_cache` by rebuilding it sequentially, removing keys `Profile 1` (ghost), `Profile 16`, and `Profile 26`, mapping the rest, and updating `profile.last_used` to `Profile 23`.
- Modified `reorganize_profiles.py` port check from `8000` to `18800` to avoid conflict with running local services (e.g. ComfyUI) which listen on port `8000` by default.
**Verified:** Ran python reorganization script successfully. Verified disk profile directories and Local State keys perfectly match `Profile 1` to `Profile 23` and `profile.last_used` is set to `Profile 23`.

## 2026-06-21 — Prevent engine restart if browser is already running

**Why:** Clicking "Start" in the TUI or calling `/engine/start` when the browser is already running stops the current browser and launches a new one unconditionally, causing disruption.
**Changes:**
- `engine/core/engine_service.py`: Added an early-return guard at the start of `/engine/start` (start_engine) that returns immediately with a message if `engine.is_running` is True.
**Verified:** Logical walkthrough. Did not commit or push.

## 2026-06-21 — Ensure active profile detection and switch logic verify folder existence

**Why:** A user can have multiple profile entries with the same email in Chrome's local state. Breaking on the first match can point to a deleted folder, causing silent switch failures.
**Changes:**
- `engine/core/engine_service.py`: In both startup detection and `perform_switch_logic`, removed the `break` statement and added `os.path.exists` check via `get_abs_path` to ensure only existing directories are selected. Last valid match wins.
**Verified:** Code review. Did not commit or push.

## 2026-06-21 — Prevent playwright.stop() hang and normalize profile username

**Why:**
- When the registration browser is closed manually by the user, calling `_reg_playwright.stop()` can hang indefinitely and prevent the engine from starting.
- Storing full email addresses as usernames in `user_login_lookup.json` causes inconsistency since other parts of the codebase only expect the local-part.

**Changes:**
- `engine/core/browser_engine.py`: Wrapped `await self._reg_playwright.stop()` with `asyncio.wait_for(..., timeout=5.0)` and caught `asyncio.TimeoutError` or other exceptions.
- `tui/app.py`: Split email on `@` and used the local part as username in `_add_profile`.

**Verified:** Syntax verified using `py_compile`. Did not commit or push.

## 2026-06-21 — Redesign Add Profile flow with ModalScreen

**Why:** The "Add profile" and "Add account" buttons both opened a registration browser but never wrote the new account to `user_login_lookup.json`, so the account never appeared in the UI.
**Changes:**
- `tui/app.py`: Removed `btn-add-account` button and its handler. Created `AddProfileModal` (Textual `ModalScreen`) that collects an email, then `_add_profile_worker()` stops any running browser/registration, checks for duplicate accounts, appends the new entry to `user_login_lookup.json`, opens the registration browser via `POST /engine/start_registration`, and recomposes both EngineTab and AccountsTab.
- Added `import json as _json`, `import shutil`, `from textual.screen import ModalScreen`, `from textual.containers import Center`.
**Verified:** Syntax verified via `py_compile`. Did not commit or push as per user instruction.

## 2026-06-21 — Clean Chrome profile directory on account deletion in TUI

**Why:** When an account was deleted via the TUI, its associated Chrome profile directory (cookies and cache) remained on disk, leading to orphaned browser directories.
**Changes:**
- `tui/app.py`: Updated `_delete_account()` to read `core/browser_user_data/Local State` JSON, look up `profile.info_cache` for a matching `user_name` (case-insensitive local-part before @), and `shutil.rmtree()` the matched profile directory. Handles `FileNotFoundError` gracefully if no Local State file exists.
**Verified:** Syntax verified via `py_compile`. Did not commit or push as per user instruction.

## 2026-06-21 — Implement close event listener on BrowserContext for manual browser close

**Why:** When the user manually closed the browser, the Playwright context closed but BrowserEngine.is_running remained True, causing /health to return engine_running=True and the TUI button to be stuck on "Stop Browser".
**Changes:**
- `engine/core/browser_engine.py`: Added close listener to `self._context` in `start()`.
- `engine/core/browser_engine.py`: Moved `self.is_running = False` to the start of `stop()`, and wrapped cleanup logic in `try/except` blocks to safely swallow exceptions on already closed resources.
**Verified:** Verified syntax and manual logical walkthrough. Did not commit/push as per user instruction.

## 2026-06-21 — Detect all three Antigravity products in setup step 7

**Why:** The initial implementation only checked for the CLI path, causing the auto-registration step to be skipped for Desktop or IDE-only installations.
**Changes:**
- `setup.bat`: Replaced the single `%LOCALAPPDATA%\agy\bin\agy.exe` existence check with checks for all three installation paths (CLI, Desktop, IDE). Set `AGY_FOUND=1` if any of them is present.
**Verified:** Verified syntax and logical flow using git diff and batch file analysis.

## 2026-06-20 — Auto-register gemi-mcp with Antigravity in setup step 7

**Why:** To streamline the installation experience by automatically configuring the gemi-mcp MCP server for users using any of the Antigravity products (CLI, Desktop, IDE).
**Changes:**
- `setup.bat`: Renumbered all existing steps to total 7. Updated Claude Code exit routes to go to `:register_antigravity` label instead of `:done`. Added a new step 7 to detect Antigravity config, prompt user, and safely merge mcp servers config into `mcp_config.json` via a temp Python script.
**Verified:** Verified pathing syntax and the Python detection command using CMD wrapper.

## 2026-06-20 — Refactor new_chat in DeepSeekProvider for Cloudflare and validation readiness

**Why:** DeepSeek web UI often presents Cloudflare verification checks or login challenge screen which blocks input field visibility. Checking login directly on page load was fragile and crashed. Checking input field presence first allows us to detect if the page needs human interaction and relaunch a headed browser cleanly.
**Changes:**
- `engine/core/providers/deepseek.py`: Refactored `new_chat()`. It now checks for the chat input text area visibility (15s timeout). If not found, it flags `input_ready=False` and automatically switches to a headed browser to let the user complete challenges or login. If ready, it logs login state as optional debug information and proceeds with `"success"`.
**Verified:** Compiled successfully via `py_compile`. Did not commit or push.

## 2026-06-20 — Resolve window state and navigation issues in DeepSeek headed login relaunch

**Why:** Relaunching the browser via `start()` did not navigate to DeepSeek as `url` parameter was unused, and it minimized the window. Now we explicitly navigate and restore the window state using CDP.
**Changes:**
- `engine/core/providers/deepseek.py`: Added `navigate()` call and a CDP block in `new_chat()`'s unauthenticated handler to force the browser window state to `"normal"` and focus it.
**Verified:** Compiled successfully via `py_compile`. Did not commit or push.

## 2026-06-20 — Auto-relaunch headed browser for DeepSeek manual login

**Why:** When DeepSeek was not logged in, the user had to manually open a browser or configuration to log in, which was not interactive. Now, we automatically stop the headless instance and launch a headed instance if login is required.
**Changes:**
- `engine/core/providers/deepseek.py`: Modified `new_chat()`'s unauthenticated handler. If login state check fails, it captures the profile name, stops the headless browser, and starts a headed browser targeting DeepSeek to prompt the user to log in.
- `engine/core/browser_engine.py`: Checked `start()` method structure for instance variables.
**Verified:** Compiled successfully via `py_compile`. Did not commit or push.

## 2026-06-20 — Add login detection support for DeepSeek provider

**Why:** DeepSeek provider lacked automated detection for account login state, potentially proceeding with unauthenticated sessions leading to failures.
**Changes:**
- `engine/core/providers/deepseek.py`: Implemented `_check_login` to query for the user avatar (`img[src*="user-avatar"]`) and walk DOM structure to extract username. Modified `new_chat` to enforce this check, returning `login_required` status if not authenticated. Also updated `send_chat` to handle and propagate `login_required` when it launches a new conversation.
**Verified:** Compiled successfully via `py_compile`. Did not commit or push.

## 2026-06-20 — Implement redo_response for DeepSeek provider

**Why:** DeepSeek provider lacked support for the `redo_response` (regenerate) action, which is needed to trigger a retry on an AI response.
**Changes:**
- `engine/core/providers/deepseek.py`: Implemented `redo_response` method. It locates the circular arrow refresh icon using the unique SVG path selector (`svg path[d*="7.92136"]`) found in DOM debug data, clicks its closest role="button" parent, and uses a content-stabilization poll to wait until the text stabilizes.
**Verified:** Compiled successfully via `py_compile`. Did not commit or push.

## 2026-06-20 — Service-switching mechanism and DeepSeek provider support

**Why:** To allow switching the active AI service provider (Gemini or DeepSeek) at runtime.
**Changes:**
- `engine/core/providers/deepseek.py`: Created `DeepSeekProvider` implementing the base provider adapter interface with specific methods. Added missing `get_last_response` method. Solved stop-button ambiguity by using a content-stabilization poll in `send_chat` and a smart SVG path coordinates check in `get_last_response` and `stop_response` to dynamically check if the primary button is in the stop state versus the send state.
- `engine/core/browser_engine.py`: Added import of `DeepSeekProvider`, registry mapping `"deepseek"` to the class, `self._active_service` tracking, and `switch_provider` implementation.
- `engine/core/engine_service.py`: Added `SwitchServiceRequest` request model and `/browser/switch_service` POST endpoint.
- `mcp/server.py`: Added `switch_service` MCP tool.
**Verified:** Checked syntax, content stabilization poll, and SVG coordinates check logic. Did not commit or push as per user instruction.



## 2026-06-20 — Replace git update-check with version.json semver comparison

**Why:** The TUI checked for updates by running git commands (`git fetch` + `git rev-list`) on both main repo and engine submodule. Replacing this with a JSON semver comparison against raw GitHub URLs simplifies the update flow and avoids git dependency for update checks.
**Changes:**
- `engine/version.json`: Created with initial version `1.0.0`.
- `version.json`: Created with initial version `1.0.0`.
- `tui/app.py`: Replaced `_check_for_updates` logic to fetch remote versions via GitHub raw URLs and compare with local versions.
**Verified:** Syntax check passed (`python -m py_compile tui/app.py`). Submodule and parent repo successfully committed.

---

## 2026-06-20 — Add `get_last_response` tool (Claude timeout resilience)

**Why:** When Claude delegates a long query to Gemini via `send_chat`, the MCP call can
time out before Gemini finishes thinking. The browser tab continues generating, but Claude
has no way to retrieve the result without re-submitting the prompt.

**Changes:**
- `engine/core/providers/gemini.py`: added `GeminiProvider.get_last_response()` — reads the
  last `model-response` DOM element's `innerText` and checks the "Stop response" button
  visibility to determine a `done` flag.
- `engine/core/browser_engine.py`: added delegation method `get_last_response()`.
- `engine/core/engine_service.py`: added `GET /browser/last_response` route.
- `mcp/server.py`: added `get_last_response` MCP tool (GET, 10s timeout, returns `done=` flag + text).

**Verified:** Code review only — no live test run yet (requires engine + MCP server restart).

**Gotcha:** Both the engine (`engine_service.py`, port 18800) and MCP server (`mcp/server.py`)
must be restarted to pick up the new route/tool.

---

## 2026-06-20 — Scan-on-ready capability cache + apply_settings validation + 422 fix

**Why:** Gemini's web UI changes its model/tool menus ("抽屉") over time. The interactive
MCP path (`apply_settings`, `send_chat`) had no mechanism to know the *current* real menu
state — it blindly clicked menu items by name. Stale names silently failed or surfaced as an
opaque `HTTP 422 Unprocessable Content`. The validate-against-live-scan logic already existed,
but only inside the automation loop (`browser_engine.py` ~L721-783), never on the interactive path.

**Root cause of the 422 specifically:** the MCP wrapper sent `{"tool": null}` when no tool was
passed, and the engine's `SettingsRequest.tool` was typed `str` → Pydantic rejected `null`.

**Changes:**
- `engine/core/providers/gemini.py`
  - `GeminiProvider.__init__`: added `self._caps` (cached capabilities) — initialised `None`.
  - `new_chat()` (end, ~L2339): **scan-on-ready** — runs `discover_capabilities()` once after
    every new chat and caches `{models, main_tools, sub_tools, thinking_levels}` into `self._caps`.
    Non-fatal if it fails.
  - `apply_settings()` (~L454): normalises the `"default"` sentinel → no-op; lazily populates
    `self._caps` if empty; validates incoming `model`/`tool`/`thinking_level` via partial
    case-insensitive match against `self._caps`; on no match returns a structured error
    **listing the live options** instead of blindly clicking.
- `engine/core/engine_service.py`: `SettingsRequest.tool` default `None → "default"`.
- `mcp/server.py`: `apply_settings` signature `tool: str = "default"`; returns the engine error
  string instead of raising; docstring notes the live-scan validation.
- `tui/app.py`: fixed the **Update & Relaunch** flow — `os.execv()` while Textual was running
  corrupted the Windows terminal (alternate screen not restored). Now sets `_relaunch_on_exit`
  and `self.exit()`s cleanly; `__main__` does the `execv` after the terminal is restored.

**Verified (live, after restarting both the engine *and* the MCP server):**
- `apply_settings(model="3.5 Flash")` with no `tool` → `Settings applied: model=3.5 Flash` (no 422).
- Invalid model → `Error: model 'X' not found in live UI. Available: ['3.1 Flash-Lite', '3.5 Flash', '3.1 Pro']`.

**Gotcha for future maintainers:** the **engine** (`engine_service.py`, port 18800) and the
**MCP server** (`mcp/server.py`, the process the MCP client connects to) are *separate processes*.
A code change to either requires restarting **that** process. Restarting only the engine left the
old MCP wrapper sending `null`, so the 422 persisted until the MCP server was also reloaded.

---

## Entry template

```
## YYYY-MM-DD — <short title>

**Why:** <what broke / motivation>
**Changes:** <files + what changed>
**Verified:** <how you confirmed it works>
**Gotcha:** <anything non-obvious for next time, optional>
```
