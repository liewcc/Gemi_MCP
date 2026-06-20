# Maintenance Log — Gemi_MCP

> Long-term maintenance diary for this repo. **Reverse-chronological** (newest on top).
> One entry per maintenance change: what broke / why, what changed, how it was verified.
> This is the durable record — `HANDOFF.md` is only the current in-flight task baton.
>
> **For AI assistants:** when you finish a maintenance change here, add an entry at the
> top using the template at the bottom. Keep entries factual and short.

---

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
