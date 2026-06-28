# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## DOM Analysis Workflow (read before writing any new provider)

When you need to discover CSS selectors for a new service, follow this workflow to minimise
Claude token usage:

### Step 1 — Capture the DOM

**The browser must be in headed mode** (user can see the window). User navigates to the
exact UI state they want to capture (e.g. menu open, message visible, settings panel showing).

Two capture methods:

**A. dom_debugger (recommended for hover/menu states):**
```
python engine/dom_debugger.py --service <name>
```
Then user presses **F9 (global hotkey)** — works from any window, mouse stays in place so
hover menus remain open. Output: `data/dom_debug_<name>.html`

**B. curl (quick, no hover):**
```
curl -X POST "http://127.0.0.1:18800/browser/capture_dom?service=<name>"
```
Output: `data/dom_debug_<name>.html`

**Important:** Always pass `--service <name>` / `?service=<name>`. Without it, the engine
captures whatever tab is currently active (often Gemini), not the target service.

### Step 2 — Analyse with Gemi (saves Claude tokens)

Pass the captured file to Gemi via Agy, not to Claude directly:

```python
mcp__agy__ask_antigravity(
    prompt="Read D:\\AI\\Gemi_MCP\\data\\dom_debug_copilot.html and find selectors for: ...",
    add_dirs=["D:\\AI\\Gemi_MCP\\data"],
    model="Gemini 3.1 Pro (High)"
)
```

Or use the `Agent` tool with `subagent_type="Explore"` pointing at the data/ directory.
Avoid reading large HTML files directly into the main Claude context.

### Step 3 — Verify selectors live

After writing the provider code, send one test message via MCP (`send_chat`) with
`new_conversation=False`. Check the engine log (`runtime/engine.log`) for selector errors.
Capture a second DOM *after* a message exchange to verify the response container selector.

---

## Current Task — Copilot Provider (Phase 0 + Phase 1) ✓ COMPLETE

**Status:** IMPLEMENTED, TESTED, AND COMMITTED (v1.2.1 / engine v1.3.1)

### What was built
- `engine/core/providers/copilot.py` — full CopilotProvider:
  - `new_chat()`: navigate if not already on Copilot, dismiss popups, wait for input
  - `send_chat()`: `keyboard.type()` with 25ms delay, waits for Stop button to disappear, content stabilization via `[data-testid="ai-message-body"]`
  - `get_last_response()`, `stop_response()`, `redo_response()`, `attach_files()`, `discover_capabilities()`, `apply_settings()`
  - Gem methods return `{"status": "unsupported"}`
- `engine/core/browser_engine.py`:
  - Launch changed to Chrome subprocess + `connect_over_cdp` (PRIMARY path); `launch_persistent_context` kept as fallback if Chrome not found
  - Chrome subprocess uses MINIMAL flags — **no `--no-sandbox`** (this was the key fix for Microsoft bot detection)
  - `_providers` dict now dynamic: `{name: cls(self) for name, cls in _PROVIDER_REGISTRY.items()}`
  - `ensure_page()` scans existing context pages for the target domain before opening a new tab
  - `new_chat()` skip-navigate if already on `copilot.microsoft.com`
- `engine/core/engine_service.py`: `select_service` validates against `_PROVIDER_REGISTRY`; `capture_dom` accepts `?service=` param
- `engine/dom_debugger.py`: `--service` CLI arg
- `tui/app.py`: pre-warm checkboxes (max 2; Gemini locked); engine launch passes `BROWSER_ENGINE_PREWARM`
- `BROWSER_ENGINE_DUAL_TAB` (bool) replaced by `BROWSER_ENGINE_PREWARM` (comma-separated list)

### Confirmed selectors (Copilot DOM, 2026-06-28)
| Purpose | Selector |
|---------|----------|
| Chat input | `[data-testid="composer"] textarea` |
| Send button | `[data-testid="composer"] button[title*="Send"]` |
| Stop button | `button[aria-label="Stop"]` |
| New chat | `[data-testid="sidebar-new-chat-button"]` |
| Response text | `[data-testid="ai-message-body"]` |
| Regenerate | `button[aria-label="Regenerate"]` |
| Response mode | `button[aria-label*="Response mode"]` |

### Key findings / gotchas
- **Microsoft bot detection is Chrome-subprocess-fingerprint-based.** The critical fix was removing `--no-sandbox` from the Chrome subprocess launch args. With it gone, Copilot passes bot detection consistently.
- **Chrome account ≠ Playwright account constraint is FALSE.** Earlier session wrongly concluded Chrome and Playwright must share the same Microsoft account. The real issue was the `_check_login()` avatar selector was broken (returned false even when logged in), causing `_relaunch_headed()` and engine restarts.
- **`_check_login()` removed from `new_chat()` post-input flow.** Input visible = logged in. The `sidebar-settings-button` avatar check was unreliable and is no longer used for gating.
- **`connect_over_cdp` path:** Chrome is launched as subprocess → Playwright attaches via CDP. Same `browser_session_sandbox` user-data-dir as before.
- **`ensure_page` domain reuse:** prevents opening duplicate Copilot tabs; finds existing tab on `copilot.microsoft.com` if present.

### Long-tail methods still to implement for Copilot
- `download_images` — needs DOM capture of Copilot image generation state
- `delete_activity_history` — needs DOM capture of history panel
- `clear_attachments` — needs DOM capture of attachment state

---

## Next Task — ChatGPT Provider (Phase 2)

**Goal:** Add `chatgpt.com` as a fourth service in `_PROVIDER_REGISTRY`.

**File to create:** `engine/core/providers/chatgpt.py` (model after `copilot.py`)

**Key decisions already made:**
- Same architecture: lazy init via `ensure_page`, `new_conversation` param
- `send_chat`, `get_last_response`, `stop_response`, `redo_response`, `attach_files`, `discover_capabilities`, `apply_settings`
- `get_gem_title` / `get_gem_info` → `{"status": "unsupported"}` (ChatGPT has no Gem concept)
- Bot detection: likely similar to Copilot. Start with same `connect_over_cdp` + minimal Chrome flags approach.

**Starting point:** Capture DOM of chatgpt.com to discover selectors before writing code.
Run: `python engine/dom_debugger.py --service chatgpt` (after registering ChatGPTProvider skeleton first so `ensure_page` can open the tab).

**Register skeleton in `browser_engine.py`:**
```python
from providers.chatgpt import ChatGPTProvider
_PROVIDER_REGISTRY = {
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "copilot": CopilotProvider,
    "chatgpt": ChatGPTProvider,
}
```

---

## Queued (lower priority)

### A. Copilot long-tail methods
`download_images`, `delete_activity_history`, `clear_attachments` — need DOM captures of respective UI states.

### B. Watchdog per-service refactor
`_session_lost` (bool) → dict keyed by service name; `_stop_automation_event` → per-service.
Each provider implements its own `get_account_info()` for watchdog checks.

### C. Eliminate core/ as a code directory
`processing_utils.py` is the only code file left in `runtime/`. Options: move to `engine/core/`, keep as-is. Must work for both Gemi_MCP and GemiPersonaPro_DT.

---

## Architecture Reference

### Provider registry pattern
```python
_PROVIDER_REGISTRY = {
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "copilot": CopilotProvider,
    # add new services here only
}
self._providers = {name: cls(self) for name, cls in self._PROVIDER_REGISTRY.items()}
self._pages = {name: None for name in self._PROVIDER_REGISTRY}
```

### Pre-warm flow
`BROWSER_ENGINE_PREWARM=gemini,deepseek` → engine warms those two at startup; all others lazy-init on first MCP call via `ensure_page()`.

### Browser launch (connect_over_cdp path)
1. Find `chrome.exe` in LOCALAPPDATA or Program Files
2. Launch: `chrome.exe --remote-debugging-port=9222 --user-data-dir=<sandbox> --no-first-run --no-default-browser-check --disable-blink-features=AutomationControlled [--headless=new]`
3. Poll port 9222 until ready (max 10s)
4. `playwright.chromium.connect_over_cdp("http://localhost:9222")`
5. Use `browser.contexts[0]` or create new context
6. Fallback: `launch_persistent_context` if Chrome not found

---

## Last Updated
2026-06-28 by Claude Code (implemented Copilot provider, connect_over_cdp, multiple-tab pre-warm UI; v1.2.1 / engine v1.3.1)
