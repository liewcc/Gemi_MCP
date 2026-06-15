#!/usr/bin/env python3
"""Gemi Engine Control — TUI"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Button, Header, Input, Label,
    Rule, Select, Static, Switch, TabbedContent, TabPane, TextArea,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
from config_utils import load_config, load_login_lookup, save_config, save_login_lookup

ENGINE_URL = "http://127.0.0.1:18800"

# ─── Config key maps ──────────────────────────────────────────────────────────

_SWITCH_MAP: dict[str, str] = {
    "sw-headless":      "headless",
    "sw-auto_start":    "auto_start_browser",
    "sw-show_console":  "show_engine_console",
    "sw-auto_looping":  "automation.auto_looping",
    "sw-auto_continue": "auto_continue_loop",
    "sw-remove_wm":     "automation.remove_watermark",
    "sw-use_gpu":       "automation.use_gpu",
    "sw-clear_pending": "automation.continue_clear_pending",
    "sw-track_filenum": "track_last_file_num",
    "sw-matrix_on":     "prompt_matrix.enabled",
    "sw-bypass_quota":  "bypass_quota_full",
}

_INPUT_MAP: dict[str, tuple[str, type]] = {
    "in-heartbeat":   ("heartbeat_timeout", int),
    "in-browser_url": ("browser_url", str),
    "in-model":       ("selected_model", str),
    "in-tool":        ("selected_tool", str),
    "in-goal":        ("automation.goal", int),
    "in-save_dir":    ("save_dir", str),
    "in-prefix":      ("name_prefix", str),
    "in-padding":     ("name_padding", int),
    "in-start":       ("name_start", int),
    "in-cooldown":    ("quota_cooldown_hours", int),
}

_SELECT_MAP: dict[str, str] = {
    "sel-mode":   "automation.mode",
    "sel-aspect": "fixed_aspect_ratio",
}

ASPECT_OPTIONS: list[tuple[str, str]] = [
    ("None",             "None"),
    ("16:9 (Landscape)", "16:9 (Landscape)"),
    ("9:16 (Portrait)",  "9:16 (Portrait)"),
    ("1:1 (Square)",     "1:1 (Square)"),
    ("4:3 (Landscape)",  "4:3 (Landscape)"),
    ("3:4 (Portrait)",   "3:4 (Portrait)"),
    ("21:9 (Ultrawide)", "21:9 (Ultrawide)"),
    ("3:2 (Landscape)",  "3:2 (Landscape)"),
    ("2:3 (Portrait)",   "2:3 (Portrait)"),
]
MODE_OPTIONS: list[tuple[str, str]] = [("rounds", "rounds"), ("images", "images")]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _dot_get(cfg: dict, path: str, default: Any = None) -> Any:
    parts = path.split(".", 1)
    val = cfg.get(parts[0])
    if val is None:
        return default
    if len(parts) == 1:
        return val
    return val.get(parts[1], default) if isinstance(val, dict) else default


def _dot_save(path: str, value: Any) -> None:
    cfg = load_config()
    parts = path.split(".", 1)
    if len(parts) == 1:
        save_config({parts[0]: value})
    else:
        block = dict(cfg.get(parts[0]) or {})
        block[parts[1]] = value
        save_config({parts[0]: block})


# ─── Reusable row widget ──────────────────────────────────────────────────────

class SettingRow(Horizontal):
    def __init__(self, label: str, *controls: Any, classes: str = ""):
        super().__init__(classes=classes)
        self._lbl = label
        self._ctrls = controls

    def compose(self) -> ComposeResult:
        yield Label(self._lbl, classes="row-label")
        for w in self._ctrls:
            yield w


# ─── Tab content widgets ──────────────────────────────────────────────────────

class EngineTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c = load_config()
        disc = c.get("discovery", {})
        avail_models: list[str] = disc.get("available_models", [])
        avail_tools:  list[str] = disc.get("available_tools", [])

        yield Label("BROWSER", classes="section-title")
        yield Rule()
        yield SettingRow("◎  Headless mode",      Switch(c.get("headless", True),              id="sw-headless"))
        yield SettingRow("▷  Auto-start browser", Switch(c.get("auto_start_browser", True),    id="sw-auto_start"))
        yield SettingRow(">_ Show console",        Switch(c.get("show_engine_console", False),  id="sw-show_console"))
        yield SettingRow("⧗  Heartbeat timeout",  Input(str(c.get("heartbeat_timeout", 3600)), id="in-heartbeat"))
        yield SettingRow("⊕  Browser URL",        Input(c.get("browser_url", ""),              id="in-browser_url"), classes="wide")

        yield Label("MODEL / TOOL", classes="section-title")
        yield Rule()
        yield SettingRow("⊞  Model",
                         Input(c.get("selected_model", ""), placeholder="e.g. 2.0 Flash",        id="in-model"))
        yield SettingRow("⚙  Tool",
                         Input(c.get("selected_tool", ""),  placeholder="e.g. Image generation",  id="in-tool"))
        if avail_models:
            yield Label(f"   Available: {', '.join(avail_models)}", classes="hint")
        if avail_tools:
            yield Label(f"   Available: {', '.join(avail_tools)}", classes="hint")

        yield Label("ENGINE ACTIONS", classes="section-title")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("↺ Restart",     id="btn-restart",       variant="primary")
            yield Button("■ Stop",        id="btn-stop",          variant="warning")
            yield Button("+ New chat",    id="btn-new-chat")
            yield Button("⟳ Refresh",    id="btn-refresh-page")
            yield Button("⊙ Discover",   id="btn-discover")


class AutomationTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c    = load_config()
        auto = c.get("automation", {})

        yield Label("LOOP", classes="section-title")
        yield Rule()
        yield SettingRow("↺  Auto-looping",        Switch(auto.get("auto_looping", False),          id="sw-auto_looping"))
        cur_mode = auto.get("mode", "rounds")
        mode_val = cur_mode if any(v == cur_mode for _, v in MODE_OPTIONS) else Select.BLANK
        yield SettingRow("⊙  Mode",                Select(MODE_OPTIONS, value=mode_val,             id="sel-mode"))
        yield SettingRow("▣  Goal (rounds)",        Input(str(auto.get("goal", 1)),                 id="in-goal"))
        yield SettingRow("▷  Auto-continue loop",  Switch(c.get("auto_continue_loop", False),       id="sw-auto_continue"))

        yield Label("IMAGE PROCESSING", classes="section-title")
        yield Rule()
        yield SettingRow("◫  Remove watermark",    Switch(auto.get("remove_watermark", True),       id="sw-remove_wm"))
        yield SettingRow("▪  Use GPU",             Switch(auto.get("use_gpu", True),                id="sw-use_gpu"))
        yield SettingRow("⊗  Clear pending on cont.", Switch(auto.get("continue_clear_pending", True), id="sw-clear_pending"))


class OutputTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c = load_config()
        raw_ratio = c.get("fixed_aspect_ratio", "None")
        cur_ratio = raw_ratio if any(v == raw_ratio for _, v in ASPECT_OPTIONS) else "None"

        yield Label("SAVE LOCATION", classes="section-title")
        yield Rule()
        yield SettingRow("📁  Save directory",  Input(c.get("save_dir", ""),          id="in-save_dir"), classes="wide")
        yield SettingRow("⊞  Filename prefix", Input(c.get("name_prefix", ""),        id="in-prefix"))
        yield SettingRow("#  Padding digits",  Input(str(c.get("name_padding", 4)),   id="in-padding"))
        yield SettingRow("1  Start number",    Input(str(c.get("name_start", 1)),     id="in-start"))
        yield SettingRow("⧗  Track last num", Switch(c.get("track_last_file_num", False), id="sw-track_filenum"))

        yield Label("IMAGE", classes="section-title")
        yield Rule()
        yield SettingRow("⊡  Fixed aspect ratio", Select(ASPECT_OPTIONS, value=cur_ratio, id="sel-aspect"))


class AccountsTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c              = load_config()
        accounts       = load_login_lookup()
        active         = c.get("active_user") or ""
        quota_full_set = set(c.get("quota_full") or [])

        yield Label("ACCOUNTS", classes="section-title")
        yield Rule()

        if not accounts:
            yield Label("No accounts found. Add one below.", classes="hint")
        else:
            for i, acc in enumerate(accounts):
                username  = acc.get("username", "")
                is_active = bool(acc.get("active", False))
                is_quota  = bool(acc.get("quota_full", ""))
                if is_active:
                    badge, badge_cls = "● Active", "badge-active"
                elif is_quota:
                    badge, badge_cls = "⚠ Quota", "badge-quota"
                else:
                    badge, badge_cls = "○ Idle", "badge-idle"

                with Horizontal(classes="account-card"):
                    yield Label(username, classes="acct-email")
                    yield Label(badge,    classes=f"acct-badge {badge_cls}")
                    if not is_active:
                        yield Button("⇄ Switch", id=f"btn-switch-{i}", name=username, classes="acct-btn")
                    yield Button("✕ Del", id=f"btn-del-{i}", name=username, classes="acct-btn acct-del")

        with Horizontal(classes="action-row"):
            yield Button("+ Add account (registration mode)", id="btn-add-account", variant="success")

        yield Label("QUOTA", classes="section-title")
        yield Rule()
        yield SettingRow("⧗  Cooldown hours",    Input(str(c.get("quota_cooldown_hours", 24)), id="in-cooldown"))
        yield SettingRow("⊗  Bypass quota check", Switch(c.get("bypass_quota_full", False),    id="sw-bypass_quota"))


class MatrixTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c      = load_config()
        matrix = c.get("prompt_matrix", {})
        items: list[dict] = matrix.get("items", [])

        yield Label("PROMPT MATRIX", classes="section-title")
        yield Rule()
        yield SettingRow("◎  Enabled", Switch(matrix.get("enabled", False), id="sw-matrix_on"))

        if items:
            yield Label("")
            with Horizontal(classes="matrix-header"):
                yield Label("Ratio",    classes="mh-ratio")
                yield Label("Target",  classes="mh-num")
                yield Label("Done",    classes="mh-num")
                yield Label("%",       classes="mh-num")
            yield Rule()
            for i, item in enumerate(items):
                ratio   = item.get("ratio", "")
                target  = item.get("target", 0)
                current = item.get("current", 0)
                pct     = f"{int(current / target * 100)}%" if target > 0 else "—"
                with Horizontal(classes="matrix-row"):
                    yield Label(ratio,         classes="mr-ratio")
                    yield Input(str(target),   classes="mr-num", id=f"in-matrix-{i}-target")
                    yield Label(str(current),  classes="mr-num")
                    yield Label(pct,           classes="mr-num")


# ─── Main App ─────────────────────────────────────────────────────────────────

class GemiTUI(App):
    CSS = """
    #main-panel    { height: 1fr; }
    TabbedContent  { width: 3fr; height: 1fr; }
    TabPane        { height: 1fr; padding: 0; }
    VerticalScroll { padding: 1 2; }

    .section-title { color: $accent; text-style: bold; padding: 1 0 0 0; }
    .hint          { color: $text-muted; padding: 0 0 0 2; }

    SettingRow  { height: 3; align: left middle; }
    .row-label  { width: 1fr; content-align: left middle; }
    Switch      { margin: 0 0 0 1; }
    Input       { width: 32; }
    Select      { width: 32; }
    .wide Input { width: 50; }

    .action-row { height: auto; margin: 1 0; }
    Button      { margin: 0 1 0 0; }

    .account-card { height: 3; align: left middle; border-bottom: solid $surface; }
    .acct-email   { width: 1fr; content-align: left middle; }
    .acct-badge   { width: 14; content-align: left middle; }
    .badge-active { color: $success; }
    .badge-quota  { color: $warning; }
    .badge-idle   { color: $text-muted; }
    .acct-btn     { min-width: 8; margin: 0 0 0 1; }

    .matrix-header { height: 2; color: $text-muted; }
    .matrix-row    { height: 3; align: left middle; }
    .mh-ratio, .mr-ratio { width: 1fr; content-align: left middle; }
    .mh-num,  .mr-num    { width: 10; content-align: center middle; }

    #right-panel {
        width: 2fr;
        height: 1fr;
        border-left: solid $surface;
    }
    #log-header {
        height: 1;
        color: $accent;
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    #service-log {
        height: 1fr;
        padding: 0 1;
    }
    #service-log.read-only {
        border: none;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $boost;
        color: $text-muted;
        text-style: bold;
        padding: 0 1;
    }
    """

    TITLE = "Gemi Engine Control TUI"
    BINDINGS = [
        ("q",      "quit",           "Quit"),
        ("ctrl+r", "reload_config",  "Reload config"),
    ]

    engine_online: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        self._mounted = False
        self._service_proc = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-panel"):
            with TabbedContent():
                with TabPane("Engine",     id="tab-engine"):
                    yield EngineTab()
                with TabPane("Automation", id="tab-automation"):
                    yield AutomationTab()
                with TabPane("Output",     id="tab-output"):
                    yield OutputTab()
                with TabPane("Accounts",   id="tab-accounts"):
                    yield AccountsTab()
                with TabPane("Matrix",     id="tab-matrix"):
                    yield MatrixTab()
            with Vertical(id="right-panel"):
                yield Static("SERVICE LOG", id="log-header")
                yield TextArea("", id="service-log", read_only=True, language=None)
        yield Static("", id="status-bar")

    def on_mount(self) -> None:
        self._mounted = True
        self._update_subtitle()
        self.set_interval(4, self._poll_status)
        self.set_interval(2, self._poll_engine_logs)
        self._start_and_stream_service()

    # ─── Service process management ───────────────────────────────────────────

    @work(thread=True)
    def _start_and_stream_service(self) -> None:
        import socket, subprocess, sys
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            already_up = s.connect_ex(("127.0.0.1", 18800)) == 0
        if already_up:
            self.call_from_thread(self._append_log, "[engine service already running on port 18800]")
            return
        CREATE_NO_WINDOW = 0x08000000
        self._service_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "core" / "engine_service.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT / "core"),
            creationflags=CREATE_NO_WINDOW,
        )
        self.call_from_thread(self._append_log, "[engine service started]")
        for raw in iter(self._service_proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            self.call_from_thread(self._append_log, line)

    def _append_log(self, line: str) -> None:
        try:
            ta = self.query_one("#service-log", TextArea)
            # Move cursor to end and insert the new line
            end = ta.document.end
            ta.move_cursor(end)
            ta.insert(line + "\n")
            # Auto-scroll to the bottom
            ta.scroll_end(animate=False)
        except Exception:
            pass

    @work(exclusive=True)
    async def _poll_engine_logs(self) -> None:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ENGINE_URL}/engine/logs", timeout=2.0)
                for line in r.json().get("logs", []):
                    self._append_log(line)
        except Exception:
            pass

    # ─── Status (subtitle) ────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _poll_status(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{ENGINE_URL}/health", timeout=2.0)
                online = r.status_code == 200
        except Exception:
            online = False
        self.engine_online = online
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        cfg    = load_config()
        active = cfg.get("active_user") or "none"
        state  = "● online" if self.engine_online else "○ offline"
        bar_text = f" Engine: {state}  │  {active}  │  q quit · ctrl+r reload "
        try:
            self.query_one("#status-bar", Static).update(bar_text)
        except Exception:
            pass

    # ─── Config saves ─────────────────────────────────────────────────────────

    def _save(self, dot_path: str, value: Any) -> None:
        _dot_save(dot_path, value)
        key = dot_path.split(".")[-1]
        self.notify(f"Saved: {key} = {value}", timeout=2)

    # ─── Event handlers ───────────────────────────────────────────────────────

    @on(Switch.Changed)
    def on_switch_changed(self, event: Switch.Changed) -> None:
        if not self._mounted:
            return
        wid = event.switch.id or ""
        if wid in _SWITCH_MAP:
            self._save(_SWITCH_MAP[wid], event.value)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        wid = event.input.id or ""
        if wid in _INPUT_MAP:
            dot_path, typ = _INPUT_MAP[wid]
            try:
                self._save(dot_path, typ(event.value))
            except (ValueError, TypeError):
                self.notify(f"Invalid value for {wid}", severity="error")
        elif wid.startswith("in-matrix-") and wid.endswith("-target"):
            idx_str = wid[len("in-matrix-"):-len("-target")]
            try:
                idx = int(idx_str)
                cfg = load_config()
                items = cfg.get("prompt_matrix", {}).get("items", [])
                if 0 <= idx < len(items):
                    items[idx]["target"] = int(event.value)
                    cfg["prompt_matrix"]["items"] = items
                    save_config({"prompt_matrix": cfg["prompt_matrix"]})
                    self.notify(f"Matrix target [{idx}] = {event.value}", timeout=2)
            except (ValueError, IndexError):
                self.notify("Invalid matrix target value", severity="error")

    @on(Select.Changed)
    def on_select_changed(self, event: Select.Changed) -> None:
        if not self._mounted:
            return
        wid = event.select.id or ""
        if wid in _SELECT_MAP and event.value is not Select.BLANK:
            self._save(_SELECT_MAP[wid], event.value)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if   bid == "btn-restart":       self._engine_restart()
        elif bid == "btn-stop":          self._engine_stop()
        elif bid == "btn-new-chat":      self._new_chat()
        elif bid == "btn-refresh-page":  self._refresh_page()
        elif bid == "btn-discover":      self._discover()
        elif bid == "btn-add-account":   self._add_account()
        elif bid.startswith("btn-switch-"):
            self._switch_account(event.button.name or "")
        elif bid.startswith("btn-del-"):
            self._delete_account(event.button.name or "")

    # ─── Engine workers ───────────────────────────────────────────────────────

    @work
    async def _engine_restart(self) -> None:
        self.notify("Restarting engine...", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/stop", timeout=10)
            await asyncio.sleep(1.5)
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/start", json={}, timeout=30)
            self.notify("Engine restarted")
        except Exception as e:
            self.notify(f"Restart failed: {e}", severity="error")

    @work
    async def _engine_stop(self) -> None:
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/stop", timeout=10)
            self.notify("Engine stopped")
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")

    @work
    async def _new_chat(self) -> None:
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/browser/new_chat", timeout=30)
            self.notify("New chat started")
        except Exception as e:
            self.notify(f"Failed: {e}", severity="error")

    @work
    async def _refresh_page(self) -> None:
        try:
            cfg = load_config()
            url = cfg.get("browser_url", "https://gemini.google.com/app")
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/browser/navigate", json={"url": url}, timeout=30)
            self.notify("Page refreshed")
        except Exception as e:
            self.notify(f"Failed: {e}", severity="error")

    @work
    async def _discover(self) -> None:
        self.notify("Discovering models & tools...", timeout=10)
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/browser/discover", timeout=60)
            cfg    = load_config()
            disc   = cfg.get("discovery", {})
            models = disc.get("available_models", [])
            tools  = disc.get("available_tools", [])
            await self.query_one(EngineTab).recompose()
            self.notify(f"Found {len(models)} models, {len(tools)} tools")
        except Exception as e:
            self.notify(f"Discover failed: {e}", severity="error")

    @work
    async def _add_account(self) -> None:
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/start_registration", timeout=30)
            self.notify("Registration browser opened — log in, then reload (ctrl+r)")
        except Exception as e:
            self.notify(f"Failed: {e}", severity="error")

    @work
    async def _switch_account(self, username: str) -> None:
        self.notify(f"Switching to {username}...", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                await c.post(
                    f"{ENGINE_URL}/engine/switch_to_profile",
                    params={"username": username},
                    timeout=65,
                )
            await self.query_one(AccountsTab).recompose()
            self._update_subtitle()
            self.notify(f"Switched to {username}")
        except Exception as e:
            self.notify(f"Switch failed: {e}", severity="error")

    @work
    async def _delete_account(self, username: str) -> None:
        accounts = [a for a in load_login_lookup() if a.get("username") != username]
        save_login_lookup(accounts)
        await self.query_one(AccountsTab).recompose()
        self.notify(f"Deleted {username}")

    # ─── App actions ──────────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        self.notify("Shutting down service…", timeout=3)
        await self._shutdown_service()
        self.exit()

    async def _shutdown_service(self) -> None:
        import subprocess
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/stop", timeout=5)
        except Exception:
            pass
        if self._service_proc is not None:
            try:
                self._service_proc.kill()
            except Exception:
                pass
            self._service_proc = None
        else:
            # Fallback: service was already running before TUI started
            try:
                out = subprocess.check_output(
                    ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
                )
                for line in out.splitlines():
                    if ":18800" in line and "LISTENING" in line:
                        pid = line.split()[-1]
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            check=False, stderr=subprocess.DEVNULL,
                        )
                        break
            except Exception:
                pass

    async def action_reload_config(self) -> None:
        for tab_cls in (EngineTab, AutomationTab, OutputTab, AccountsTab, MatrixTab):
            try:
                await self.query_one(tab_cls).recompose()
            except Exception:
                pass
        self._update_subtitle()
        self.notify("Config reloaded")


if __name__ == "__main__":
    GemiTUI().run()
