# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task — send_chat STATE MANAGEMENT OVERHAUL (Plan C)

**Problem:** `send_chat` has no mechanism to guarantee clean state before each call.
It calls `prepare_chat_state()` (dismisses overlays, waits for idle) but **never starts a new
conversation**. Every call accumulates context in the same Gemini session → context pollution,
unpredictable drift, eventual token limit breach.

**Decision:** Implement **Option C** — add `new_conversation: bool = True` parameter to
`send_chat`. Default `True` forces a new chat before every call. Pass `False` to continue
an existing multi-turn conversation.

### Current Status (2026-06-19)

Steps 1–4 code is **committed** but **smoke test FAILING** — `new_conversation=True` does NOT isolate conversations. Gemini still remembers tokens from previous `send_chat` calls.

**Root cause hypothesis:** Clicking the "New Chat" button may NOT change the URL in all Gemini UI states. Our URL-change detection times out (8s), we proceed, but we're still in the old conversation. Need to add logging to confirm.

**Next debugging steps:**
1. Add a `self._log(f"URL before new_chat: {url_before}, after: {self._page.url}")` and check engine.log after a failed test to see what URL Gemini is actually on.
2. If URL doesn't change: try `page.evaluate` to click the button and then `page.wait_for_navigation()` instead of polling.
3. Alternative: use `navigate("https://gemini.google.com/app")` BUT first check if `/app` actually loads a fresh chat or the last conversation — open the browser and manually observe what URL you end up on after clicking New Chat vs navigating to /app.
4. If Gemini always stays at `/app` for new chats (no URL change), switch to checking for absence of `model-response` elements after a configurable grace period (e.g. 1.5s after click).

**Files to check:**
- `engine/core/providers/gemini.py` — `new_chat()` around line 2222

### Implementation Plan

#### Step 1 — `gemini.py`: harden `new_chat()` with confirmed-ready wait

File: `engine/core/providers/gemini.py`, function `new_chat` (~line 2160)

After the click / navigation, **do not rely on a bare `sleep(1.0)`**. Instead add:

```python
# Wait until the prompt input area is visible and interactive
try:
    await self._page.wait_for_selector(
        "div[aria-label='Enter a prompt for Gemini'], "
        "div[aria-label='Enter a prompt here'], "
        "div.ql-editor[contenteditable='true']",
        state="visible",
        timeout=8000,
    )
except Exception:
    self._log("new_chat: prompt input did not appear within 8s after navigation.")
```

Also verify the URL changed to `/app` (or `/gem/`) after the click — if it hasn't changed
within 3 s, fall back to `navigate("https://gemini.google.com/app")`.

#### Step 2 — `gemini.py`: add `new_conversation` param to `send_chat`

File: `engine/core/providers/gemini.py`, function `send_chat` (~line 1237)

Change signature:
```python
async def send_chat(self, prompt: str, new_conversation: bool = True) -> dict:
```

At **Step A**, after `prepare_chat_state()`, add:
```python
if new_conversation:
    await self.new_chat()   # now hardened — waits for prompt input ready
```

#### Step 3 — `engine_service.py`: expose param through REST

File: `engine/core/engine_service.py`, `PromptRequest` model + `/browser/chat` endpoint (~line 1638)

Extend `PromptRequest` (or create a `ChatRequest`):
```python
class ChatRequest(BaseModel):
    text: str
    new_conversation: bool = True
```

Update handler:
```python
@app.post("/browser/chat")
async def send_chat(req: ChatRequest):
    ...
    result = await engine.send_chat(req.text, new_conversation=req.new_conversation)
```

#### Step 4 — `mcp/server.py`: expose param as MCP tool arg

File: `mcp/server.py`, `send_chat` tool (~line 160)

```python
@mcp.tool()
async def send_chat(prompt: str, new_conversation: bool = True) -> str:
    """Send a text prompt to Gemini and return its text reply.

    Args:
        prompt:           The text message to send to Gemini.
        new_conversation: If True (default), starts a fresh Gemini chat before
                          sending — guarantees clean context. Set False to
                          continue an existing multi-turn conversation.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENGINE_URL}/browser/chat",
            json={"text": prompt, "new_conversation": new_conversation},
            timeout=240.0,
        )
        resp.raise_for_status()
        data = resp.json()
    if data.get("status") == "success":
        return data.get("text", "")
    raise RuntimeError(data.get("message", "Gemini chat failed with unknown error"))
```

#### Step 5 — Smoke test

After all edits:
1. Call `send_chat("hello")` twice — confirm each lands on a clean page (no prior context).
2. Call `send_chat("what did I say before?", new_conversation=False)` after a first turn — confirm Gemini remembers.
3. Call `send_chat(...)` while a drawer/overlay is open — confirm `prepare_chat_state` still clears it.

### Files to touch (in order)
1. `engine/core/providers/gemini.py` — `new_chat()`, `send_chat()`
2. `engine/core/engine_service.py` — `ChatRequest` model, `/browser/chat` handler
3. `mcp/server.py` — `send_chat` tool signature + payload

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
- [x] TUI: Fixed "Update & Relaunch" producing black/grey blocks — see **Decisions & Pitfalls** below

## In Progress
- [ ] **send_chat state management overhaul (Plan C)** — Steps 1, 2, 3, 4 implemented. Step 5 (smoke test) needs completion.
- [ ] **Update & Relaunch — needs live verification** (fix applied 2026-06-19, not yet tested with a real update)
  - The fix is in `tui/app.py`. After the next successful update run, mark this done and move to Done.

## Next Steps
1. Add DeepSeek support to the engine
2. Build agy-mcp TUI (separate repo `D:\AI\AGY_MCP`)
3. **Add `get_last_response` tool** — see proposal below

---

## Proposed Feature — `get_last_response` (Claude timeout resilience)

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
  This is the validation behavior for "confirm saved settings still in menu".

## Last Updated
2026-06-19 by Antigravity — Implemented Step 4 (mcp/server.py tool signature override with `new_conversation: bool = True`). Updated HANDOFF.md.

## Previous Last Updated (archived)
2026-06-19 by Antigravity — Implemented Edit 1 (harden new_chat in engine/core/providers/gemini.py) and Edit 2 (ChatRequest and /browser/chat handler in engine/core/engine_service.py) as requested.


## Last Updated (Claude, 2026-06-19)
2026-06-19 by Claude — Implemented Steps 1-4 (new_conversation param end-to-end). Smoke test FAILING: new_chat() does not isolate conversations. Hypothesis: URL does not change on New Chat click; URL-change detection times out and we proceed in old convo. Next: add URL logging to confirm, then fix wait logic (see "Current Status" above).

## Last Updated (Claude, 2026-06-20)
2026-06-20 by Claude — Added `get_last_response` feature proposal under Next Steps.
Motivation: Claude times out waiting for gemi extended thinking, but the browser tab
keeps generating. This tool lets Claude poll for the result without re-submitting.

## Last Updated (Claude, 2026-06-19 — session 2)
Implementation complete and smoke-tested. send_chat new_conversation param works end-to-end:
- True (default): navigate to config.browser_url (/app), SPA state resets, fresh blank conversation
- False: continue current conversation
Gemini Memory (cross-session recall) is a user-level setting, not an engine concern.
All 4 layers committed: gemini.py, browser_engine.py, engine_service.py, mcp/server.py.
