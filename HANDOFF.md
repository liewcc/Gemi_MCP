# Work Handoff

> Shared "baton" for the two AIs (Claude Code / Google Antigravity).
> Read this first when you start; update it before you stop.
> This is the single source of truth for progress — never assume state from memory.

---

## Current Task — TOOL & MODEL SELECTION (URGENT: has a regression bug)

**Feature:** Live-scan Gemini web UI to discover available models / thinking levels / tools,
display them in TUI dropdowns, apply selected settings before each conversation.

**Status:** Mostly working — one regression bug introduced at session end that breaks discovery.

### URGENT BUG TO FIX FIRST

**File:** `engine/core/providers/gemini.py`
**Function:** `discover_capabilities`
**Error seen:** `Discovery failed: 'NoneType' object is not iterable`

**Root cause:** The `_UPLOAD_LABEL_JS` f-string is evaluated as a function body `() => { expr }`
which has no `return` statement — evaluates to `undefined` → Python gets `None` → iterating `None`
crashes.

**Exact fix** — change lines ~330-348 (Step A + B). There are TWO bad evaluate calls:

```python
# BROKEN (returns None — no return in function body):
upload_base: list = await self._page.evaluate(f'() => {{{_UPLOAD_LABEL_JS}}}')
...
all_upload: list = await self._page.evaluate(f'() => {{{_UPLOAD_LABEL_JS}}}')
```

```python
# FIXED (concise arrow function — expression is the return value):
upload_base: list = await self._page.evaluate(f'() => ({_UPLOAD_LABEL_JS})')
...
all_upload: list = await self._page.evaluate(f'() => ({_UPLOAD_LABEL_JS})')
```

Change `{{{_UPLOAD_LABEL_JS}}}` → `({_UPLOAD_LABEL_JS})` in BOTH calls (triple-brace → parens).

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

## After fixing the URGENT bug, verify these:

1. **Discovery** — click Discover; Output tab should show:
   ```
   [TUI] Discover: scanning...
   API>> Discovery complete. Models: [...], Main tools (6): [...], Sub-menus: {'More uploads': ['Photos', 'Notebooks'], ...}
   [TUI] Discover complete: 3 models, 6 main tools, sub_tools=['More uploads', ...]
   ```
   And `sel-tool` dropdown should show: Upload files / Add from Drive / More uploads / Create image / Canvas

2. **Sub-menu** — select "More uploads" in `sel-tool` → `sel-upload` should update to Photos / Notebooks

3. **Apply** — select model + thinking + tool, click Apply; Output tab should show:
   ```
   [TUI] Apply: scanning menu...
   API>> Applying model: ...
   API>> Applying thinking level: ...
   API>> Applying tool: ...
   API>> apply_settings done: model=ok, thinking=ok, tool=ok
   [TUI] Apply result: model=ok, thinking=ok, tool=ok
   [TUI] Apply: settings saved to config
   ```

4. **Button state** — Start Engine / Start Browser buttons should NOT flip to "Start" after Discover or Apply

---

## Done (earlier tasks — unchanged)
- [x] TUI: Added "Auto Del" / "Range" controls to AccountsTab account cards
- [x] TUI: ENGINE OPERATIONS panel ported from GemiPersonaPro_DT
- [x] TUI: ACCOUNT ACTIONS panel ported
- [x] TUI: RichLog → TextArea for log copying
- [x] Engine cleanup via Windows Job Object
- [x] SERVICE LOG live output fixed
- [x] GitHub public repo published

## In Progress
- TOOL & MODEL SELECTION — feature mostly done, URGENT fix needed (see above)

## Next Steps (after TOOL & MODEL SELECTION is verified)
1. Complete `gemi-mcp` feature surface (see original task #1 in earlier handoff)
2. Add DeepSeek support to the engine
3. Build agy-mcp TUI (separate repo `D:\AI\AGY_MCP`)

## Decisions & Pitfalls
- Never commit `core/browser_session_sandbox/`, `core/browser_user_data/`, or `data/*.json`
- Two gemini.py files exist: `core/providers/gemini.py` (project) vs `engine/core/providers/gemini.py`
  (submodule, actually used at runtime). Always edit the ENGINE version.
- `_update_discovered_selects()` must NOT call `recompose()` — that resets button states.
- Textual `Select.set_options()` preserves current value if still valid, clears it otherwise.
  This is the validation behavior for "confirm saved settings still in menu".

## Last Updated
2026-06-18 by Claude — TOOL & MODEL SELECTION feature built + regression bug introduced at
session end (f-string JS evaluation, fix documented above). Handing off to Antigravity.
