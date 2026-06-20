# Maintenance Log — Gemi_MCP

> Long-term maintenance diary for this repo. **Reverse-chronological** (newest on top).
> One entry per maintenance change: what broke / why, what changed, how it was verified.
> This is the durable record — `HANDOFF.md` is only the current in-flight task baton.
>
> **For AI assistants:** when you finish a maintenance change here, add an entry at the
> top using the template at the bottom. Keep entries factual and short.

---

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
