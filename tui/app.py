#!/usr/bin/env python3
"""Gemi MCP — TUI"""
from __future__ import annotations

import asyncio
import json as _json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.widgets import (
    Button, Checkbox, Header, Input, Label,
    Rule, Select, Static, Switch, TabbedContent, TabPane, TextArea,
)

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("BROWSER_ENGINE_PROJECT_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "core"))  # submodule (active shared code)
sys.path.insert(0, str(ROOT / "runtime"))              # project-local (processing_utils, legacy)
from config_utils import load_config, load_login_lookup, save_config, save_login_lookup

ENGINE_URL = "http://127.0.0.1:18800"


# ─── Windows Job Object: tie engine lifetime to this process ──────────────────
def _create_kill_on_close_job():
    """Create a Windows Job Object whose assigned processes are killed when the
    last handle to the job closes. Since this (TUI) process holds the only
    handle, the engine dies automatically when the TUI exits for *any* reason —
    graceful quit, window 'X' close, or crash. Returns the job handle, or None
    on non-Windows / failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        k32.CloseHandle(job)
        return None
    return job


def _assign_process_to_job(job, proc) -> None:
    """Assign a subprocess.Popen process to the given Job Object handle."""
    if job is None or sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject(job, int(proc._handle))

# ─── Config key maps ──────────────────────────────────────────────────────────

_SWITCH_MAP: dict[str, str] = {
    "sw-headless":      "headless",
    "sw-auto_start":    "auto_start_browser",
    "sw-auto_continue": "auto_continue_loop",
    "sw-bypass_quota":  "bypass_quota_full",
}

_INPUT_MAP: dict[str, tuple[str, type]] = {
    "in-heartbeat":   ("heartbeat_timeout", int),
    "in-browser_url": ("browser_url", str),
    "in-cooldown":    ("quota_cooldown_hours", int),
}

_SELECT_MAP: dict[str, str] = {
}


RANGE_OPTIONS: list[tuple[str, str]] = [
    ("Last hour", "Last hour"),
    ("Last day",  "Last day"),
    ("All time",  "All time"),
]

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


# ─── Modal dialog for Add Profile ─────────────────────────────────────────────

class AddProfileModal(ModalScreen[str | None]):
    """Modal dialog that asks for a Google account email to add as a new profile."""

    CSS = """
    AddProfileModal {
        align: center middle;
    }
    #modal-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #modal-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 1 0;
    }
    #modal-desc {
        margin: 0 0 1 0;
    }
    #input-new-email {
        width: 100%;
        margin: 0 0 1 0;
    }
    #modal-buttons {
        height: auto;
        align: right middle;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("Add Profile", id="modal-title")
            yield Label(
                "Enter the Google account email you will log in with:",
                id="modal-desc",
            )
            yield Input(placeholder="user@gmail.com", id="input-new-email")
            with Horizontal(id="modal-buttons"):
                yield Button("OK", id="btn-modal-ok", variant="success")
                yield Button("Cancel", id="btn-modal-cancel")

    @on(Button.Pressed, "#btn-modal-ok")
    def on_ok(self, _event: Button.Pressed) -> None:
        email = self.query_one("#input-new-email", Input).value.strip()
        self.dismiss(email if email else None)

    @on(Button.Pressed, "#btn-modal-cancel")
    def on_cancel(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#input-new-email")
    def on_input_submitted(self, _event: Input.Submitted) -> None:
        email = self.query_one("#input-new-email", Input).value.strip()
        self.dismiss(email if email else None)


# ─── Tab content widgets ──────────────────────────────────────────────────────

class EngineTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c = load_config()
        disc = self.app._discovered  # always live-scanned, never from config
        avail_models:   list[str] = disc.get("models",       [])

        # ── ENGINE OPERATIONS (mirrors GemiPersonaPro_DT setup panel) ──
        yield Label("ENGINE OPERATIONS", classes="section-title")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("Start Engine",  id="btn-toggle-engine",  variant="primary")
            yield Button("Start Browser", id="btn-toggle-browser", variant="primary")
        yield SettingRow("◎  Headless mode",      Switch(c.get("headless", True),             id="sw-headless"))
        yield SettingRow("▷  Auto-start browser", Switch(c.get("auto_start_browser", True),   id="sw-auto_start"))
        yield SettingRow("↺  Auto-continue loop", Switch(c.get("auto_continue_loop", False),  id="sw-auto_continue"))

        # ── PRE-WARM TABS (max 2; others open lazily on first MCP call) ──
        _prewarm_saved: list = c.get("prewarm_services", ["gemini", "deepseek"])
        yield Label("Pre-warm tabs (max 2):", classes="setting-label")
        with Horizontal(classes="action-row"):
            yield Checkbox("Gemini",   "gemini"   in _prewarm_saved, id="chk-prewarm-gemini",   disabled=True)
            yield Checkbox("DeepSeek", "deepseek" in _prewarm_saved, id="chk-prewarm-deepseek")
            yield Checkbox("Copilot",  "copilot"  in _prewarm_saved, id="chk-prewarm-copilot")
            yield Checkbox("ChatGPT",  "chatgpt"  in _prewarm_saved, id="chk-prewarm-chatgpt")

        # ── ACCOUNT ACTIONS (mirrors GemiPersonaPro_DT setup panel) ──
        accounts     = load_login_lookup()
        active       = c.get("active_user") or "none"
        profile_opts = [(u["username"], u["username"]) for u in accounts if u.get("username")]

        yield Label("ACCOUNT ACTIONS", classes="section-title")
        yield Rule()
        with Horizontal(classes="acct-status-row"):
            yield Label("Active profile:", classes="acct-status-label")
            yield Label(active, id="engine-active-profile", classes="acct-status-value")
            yield Button("🔍 Check status", id="btn-check-status", classes="acct-act-btn")
        with Horizontal(classes="action-row btn-row"):
            yield Button("📋 Add profile", id="btn-add-profile", variant="success")
            yield Button("🔄 Re-login",    id="btn-relogin")
        with Horizontal(classes="action-row btn-row"):
            yield Button("⏮ Prev",        id="btn-prev-profile")
            yield Button("⏭ Next",        id="btn-next-profile")
        with Horizontal(classes="action-row"):
            if profile_opts:
                yield Select(profile_opts, prompt="Select target profile…", id="sel-target-profile")
            else:
                yield Label("No profiles found — add one above.", classes="hint")
            yield Button("👤 Switch", id="btn-switch-target")

        yield Label("BROWSER", classes="section-title")
        yield Rule()
        yield SettingRow("⧗  Heartbeat timeout",  Input(str(c.get("heartbeat_timeout", 3600)), id="in-heartbeat"))
        yield SettingRow("⊕  Browser URL",        Input(c.get("browser_url", ""),              id="in-browser_url"), classes="wide")

        yield Label("TOOL & MODEL SELECTION", classes="section-title")
        yield Rule()
        sel_tool     = c.get("selected_tool",            "") or ""
        sel_upload   = c.get("selected_upload_tool",     "") or ""
        sel_model    = c.get("selected_model",           "") or ""
        sel_thinking = c.get("selected_thinking_level",  "") or ""

        avail_thinking: list[str] = disc.get("thinking_levels", [])
        avail_main:     list[str] = disc.get("main_tools",     [])
        sub_tools_dict: dict      = disc.get("sub_tools",      {})

        main_tool_opts: list[tuple[str, str]] = [(t, t) for t in avail_main    if t]
        model_opts:     list[tuple[str, str]] = [(m, m) for m in avail_models  if m]
        thinking_opts:  list[tuple[str, str]] = [(t, t) for t in avail_thinking if t]

        # Sub-menu options depend on the currently saved main tool selection
        cur_subs:    list[str]         = sub_tools_dict.get(sel_tool, [])
        sub_opts:    list[tuple[str, str]] = [(t, t) for t in cur_subs if t]

        _ph = [("(click Discover)", "")]
        tool_val     = sel_tool     if any(v == sel_tool     for _, v in main_tool_opts) else (main_tool_opts[0][1] if main_tool_opts else "")
        sub_val      = sel_upload   if any(v == sel_upload   for _, v in sub_opts)       else (sub_opts[0][1]       if sub_opts       else "")
        model_val    = sel_model    if any(v == sel_model    for _, v in model_opts)     else (model_opts[0][1]     if model_opts     else "")
        thinking_val = sel_thinking if any(v == sel_thinking for _, v in thinking_opts)  else (thinking_opts[0][1]  if thinking_opts  else "")

        # Row 1: main tool menu (mirrors Gemini UI order) + conditional sub-menu
        with Horizontal(classes="action-row"):
            yield Select(main_tool_opts or _ph, value=tool_val, id="sel-tool", allow_blank=False)
            yield Select(sub_opts or [("(select More... first)", "")], value=sub_val, id="sel-upload", allow_blank=False)
        # Row 2: model + thinking level
        with Horizontal(classes="action-row"):
            yield Select(model_opts    or _ph, value=model_val,    id="sel-model",    allow_blank=False)
            yield Select(thinking_opts or _ph, value=thinking_val, id="sel-thinking", allow_blank=False)
        with Horizontal(classes="action-row"):
            yield Button("Discover", id="btn-discover")
            yield Button("Save",     id="btn-save-model-tool")
            yield Button("Apply",    id="btn-apply-model-tool", variant="primary")

        # ── SINGLE ACTION CONTROL ──────────────────────────────────────────────
        yield Label("SINGLE ACTION CONTROL", classes="section-title")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("+ New Chat",    id="btn-new-chat")
            yield Button("Submit Prompt", id="btn-submit-prompt", variant="primary")
            yield Button("Upload File",   id="btn-upload-file",   variant="primary")
        with Horizontal(classes="action-row"):
            yield Button("Submit",        id="btn-submit",        variant="primary")
            yield Button("Redo",          id="btn-redo")
            yield Button("Stop",          id="btn-stop",          variant="error")

        yield Label("COMBINE ACTION CONTROL", classes="section-title")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("New Chat & Submit", id="btn-combine-submit", variant="primary")
            yield Button("Redo", id="btn-combine-redo")
            yield Button("Stop", id="btn-combine-stop", variant="error")

        with Horizontal(classes="action-row"):
            yield Button("Capture Browser DOM to File", id="btn-capture-dom")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("⬆ Update & Relaunch", id="btn-update-relaunch", variant="warning", disabled=True)

    @on(Select.Changed, "#sel-tool")
    def on_sel_tool_changed(self, event: Select.Changed) -> None:
        """When the main tool changes, update the sub-menu with that item's sub-options."""
        selected = str(event.value) if event.value is not Select.BLANK else ""
        sub_tools_dict = self.app._discovered.get("sub_tools", {})
        subs = sub_tools_dict.get(selected, [])
        sub_select = self.query_one("#sel-upload", Select)
        if subs:
            sub_select.set_options([(t, t) for t in subs])
        else:
            sub_select.set_options([("(select More... first)", "")])





class AccountsTab(Vertical):
    def compose(self) -> ComposeResult:
        c              = load_config()
        accounts       = load_login_lookup()
        active         = c.get("active_user") or ""
        quota_full_set = set(c.get("quota_full") or [])

        with Horizontal(classes="acct-header"):
            yield Label("Account",  classes="acct-email acct-hdr")
            yield Label("Auto Del", classes="acct-hdr-sw acct-hdr")
            yield Label("Range",    classes="acct-hdr-sel acct-hdr")
            yield Label("Actions",  classes="acct-hdr-acts acct-hdr")

        with VerticalScroll():
            if not accounts:
                yield Label("No accounts found. Add one below.", classes="hint")
            else:
                for i, acc in enumerate(accounts):
                    username  = acc.get("username", "")
                    is_active = bool(acc.get("active", False))
                    is_quota  = bool(acc.get("quota_full", ""))

                    range_val = acc.get("delete_range") or "Last hour"
                    if not any(v == range_val for _, v in RANGE_OPTIONS):
                        range_val = "Last hour"

                    display_name = username[:18] + "…" if len(username) > 19 else username
                    email_cls = "acct-email acct-email-active" if is_active else "acct-email"

                    with Horizontal(classes="account-card"):
                        yield Label(display_name, classes=email_cls)
                        yield Switch(acc.get("auto_delete", False), id=f"sw-autodel-{i}")
                        yield Select(RANGE_OPTIONS, value=range_val, id=f"sel-range-{i}", allow_blank=False)
                        if is_active:
                            yield Static("", classes="acct-switch-ph")
                        else:
                            yield Button("⇄ Switch", id=f"btn-switch-{i}", name=username, classes="acct-btn")
                        yield Button("✕ Del",    id=f"btn-del-{i}",     name=username, classes="acct-btn acct-del")
                        yield Button("🗑 Del Now", id=f"btn-delhist-{i}", name=username, classes="acct-btn acct-delhist")

            yield Label("QUOTA", classes="section-title")
            yield Rule()
            yield SettingRow("⧗  Cooldown hours",    Input(str(c.get("quota_cooldown_hours", 24)), id="in-cooldown"))
            yield SettingRow("⊗  Bypass quota check", Switch(c.get("bypass_quota_full", False),    id="sw-bypass_quota"))





# ─── Main App ─────────────────────────────────────────────────────────────────

class GemiTUI(App):
    CSS = """
    #main-panel    { height: 1fr; }
    TabbedContent  { width: 62; height: 1fr; }
    TabPane        { height: 1fr; padding: 0; }
    VerticalScroll { padding: 1 2; }

    .section-title { color: $accent; text-style: bold; padding: 1 0 0 0; }
    .hint          { color: $text-muted; padding: 0 0 0 2; }

    SettingRow  { height: 3; align: left middle; }
    .row-label  { width: 20; content-align: left middle; }
    Switch      { margin: 0 0 0 1; }
    Input       { width: 1fr; }
    Select      { width: 16; }

    .btn-row Button { width: 1fr; }

    .action-row { width: 1fr; height: auto; margin: 1 0; }
    .action-row Button:last-of-type { width: 1fr; }
    #btn-capture-dom, #btn-update-relaunch { width: 1fr; }
    #btn-toggle-engine, #btn-toggle-browser { width: 1fr; }
    #sel-target-profile { width: 1fr; }
    Button      { margin: 0 1 0 0; }
    #sel-tool, #sel-upload { width: 1fr; }
    #sel-model, #sel-thinking { width: 1fr; }

    .acct-status-row   { height: 3; align: left middle; }
    .acct-status-label { width: auto; content-align: left middle; color: $text-muted; }
    .acct-status-value { width: 1fr; content-align: left middle; color: $accent; text-style: bold; padding: 0 0 0 1; }
    .acct-act-btn      { min-width: 16; }

    AccountsTab { height: 1fr; }
    AccountsTab > VerticalScroll { height: 1fr; padding: 0 2; }
    .acct-header  { height: 3; align: left middle; background: $boost; padding: 0 4 0 2; }
    .acct-hdr     { content-align: left middle; color: $text-muted; text-style: bold; }
    .acct-hdr-sw  { width: 8;  margin: 0 0 0 1; }
    .acct-hdr-sel { width: 14; margin: 0 0 0 1; }
    .acct-hdr-acts  { width: 31; margin: 0 0 0 1; }
    .acct-switch-ph { width: 9;  margin: 0 0 0 1; }
    .account-card { height: 3; align: left middle; border-bottom: solid $surface; }
    .acct-email        { width: 1fr; content-align: left middle; }
    .acct-email-active { color: $success; }
    .acct-btn     { min-width: 9; margin: 0 0 0 1; }
    .account-card Switch { width: 8; margin: 0 0 0 1; }
    .account-card Select { width: 14; margin: 0 0 0 1; }
    .acct-delhist { min-width: 11; margin: 0 0 0 1; }



    #right-panel {
        width: 1fr;
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

    TITLE = "GEMI MCP"
    BINDINGS = [
        ("q",      "quit",           "Quit"),
        ("ctrl+r", "reload_config",  "Reload config"),
    ]

    engine_online:  reactive[bool] = reactive(False)
    browser_online: reactive[bool] = reactive(False)
    _update_status: reactive[str]  = reactive("")

    def watch_engine_online(self, value: bool) -> None:
        self._update_subtitle()
        self._update_op_buttons()

    def watch_browser_online(self, value: bool) -> None:
        self._update_subtitle()
        self._update_op_buttons()

    def __init__(self):
        super().__init__()
        self._mounted = False
        self._service_proc = None
        self._relaunch_on_exit = False
        self._job = _create_kill_on_close_job()
        # Live-scanned menu options — populated by Discover, never read from config.
        self._discovered: dict = {
            "models": [], "thinking_levels": [],
            "main_tools": [],   # ordered: Upload files, Add from Drive, More uploads, Create image, Canvas, More tools
            "sub_tools": {},    # {"More uploads": [...], "More tools": [...]}
            # legacy
            "tools": [], "upload_tools": [],
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-panel"):
            with TabbedContent():
                with TabPane("Engine",     id="tab-engine"):
                    yield EngineTab()

                with TabPane("Accounts",   id="tab-accounts"):
                    yield AccountsTab()

            with Vertical(id="right-panel"):
                yield Static("SERVICE LOG", id="log-header")
                yield TextArea("", id="service-log", read_only=True, language=None)
        yield Static("", id="status-bar")

    def on_mount(self) -> None:
        self._mounted = True
        self._update_subtitle()
        self.set_interval(2, self._poll_status)
        self.set_interval(2, self._poll_engine_logs)
        self._start_and_stream_service()
        self._engine_autostart()
        self._check_for_updates()

    # ─── Service process management ───────────────────────────────────────────

    @staticmethod
    def _find_port_pid(port: int) -> str | None:
        """Return the PID (as str) of the process LISTENING on *port*, or None."""
        import subprocess as _sp
        try:
            out = _sp.check_output(["netstat", "-ano"], text=True, stderr=_sp.DEVNULL)
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    return line.split()[-1]
        except Exception:
            pass
        return None

    def _kill_leftover_engine(self) -> bool:
        """taskkill any process LISTENING on port 18800. Returns True if one was killed."""
        import subprocess
        killed = False
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if ":18800" in line and "LISTENING" in line:
                    pid = line.split()[-1]
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    killed = True
                    break
        except Exception:
            pass
        return killed

    @work(thread=True, group="svc_stream", exclusive=True)
    def _start_and_stream_service(self) -> None:
        import socket, subprocess, sys, time

        def _port_open() -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(("127.0.0.1", 18800)) == 0

        def _kill_by_port() -> None:
            """Kill whatever PID is listening on 18800 via taskkill /F /T."""
            try:
                out = subprocess.check_output(
                    ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
                )
                for line in out.splitlines():
                    if ":18800" in line and "LISTENING" in line:
                        pid = line.split()[-1]
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", pid],
                            check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        break
            except Exception:
                pass

        if _port_open():
            self.call_from_thread(
                self._append_log,
                "[engine service already running on port 18800 — killing and respawning...]",
            )
            _kill_by_port()
            # Wait up to ~5 s for the port to free.
            for _ in range(20):
                time.sleep(0.25)
                if not _port_open():
                    break
            else:
                # Port still held — try once more with the legacy helper.
                self._kill_leftover_engine()
                time.sleep(1)
                if _port_open():
                    self.call_from_thread(
                        self._append_log,
                        "[warning: port 18800 still in use; spawn may fail]",
                    )

        CREATE_NO_WINDOW = 0x08000000
        import os as _os
        _env = _os.environ.copy()
        _core_path = str(ROOT / "runtime")  # project-local: processing_utils lives here
        _env["PYTHONPATH"] = _core_path + (_os.pathsep + _env["PYTHONPATH"] if "PYTHONPATH" in _env else "")
        _env["BROWSER_ENGINE_DATA_DIR"] = _core_path
        _env["BROWSER_ENGINE_DATA_SUBDIR"] = "runtime"
        _env["BROWSER_ENGINE_PROJECT_ROOT"] = str(ROOT)
        _prewarm_list = load_config().get("prewarm_services", ["gemini", "deepseek"])
        _env["BROWSER_ENGINE_PREWARM"] = ",".join(_prewarm_list)
        self._service_proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "engine" / "core" / "engine_service.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT / "engine" / "core"),
            creationflags=CREATE_NO_WINDOW,
            env=_env,
        )
        _assign_process_to_job(self._job, self._service_proc)
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

    @work(exclusive=True, group="poll_logs")
    async def _poll_engine_logs(self) -> None:
        # When we spawned the engine ourselves we already stream its stdout
        # (which now mirrors every _log_debug line), so polling would duplicate
        # those lines. Only poll as a fallback when the engine was already
        # running externally and we have no stdout stream to read.
        if self._service_proc is not None:
            return
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ENGINE_URL}/engine/logs", timeout=2.0)
                for line in r.json().get("logs", []):
                    self._append_log(line)
        except Exception:
            pass

    # ─── Status (subtitle) ────────────────────────────────────────────────────

    @work(exclusive=True, group="poll_status")
    async def _poll_status(self) -> None:
        online  = False
        browser = False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{ENGINE_URL}/health", timeout=1.5)
                online = r.status_code == 200
                if online:
                    data = r.json()
                    browser = bool(data.get("engine_running"))
        except Exception:
            pass
        self.engine_online  = online
        self.browser_online = browser

    def _update_op_buttons(self) -> None:
        """Flip the ENGINE OPERATIONS buttons between Start/Stop. Driven by the
        same status poll that feeds the status bar — no special-casing."""
        try:
            self.query_one("#btn-toggle-engine", Button).label = (
                "Stop Engine" if self.engine_online else "Start Engine")
        except Exception:
            pass
        try:
            self.query_one("#btn-toggle-browser", Button).label = (
                "Stop Browser" if self.browser_online else "Start Browser")
        except Exception:
            pass

    def watch__update_status(self, _: str) -> None:
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        cfg     = load_config()
        active  = cfg.get("active_user") or "none"
        engine  = "[green]● online[/green]"  if self.engine_online  else "[red]○ offline[/red]"
        browser = "[green]● browser[/green]" if self.browser_online else "[red]○ browser[/red]"
        upd     = self._update_status
        if upd:
            upd_part = f"  │  {upd}"
        else:
            upd_part = ""
        bar_text = f" Engine: {engine}  {browser}  │  {active}{upd_part}  │  q quit · ctrl+r reload "
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
        elif wid.startswith("sw-autodel-"):
            try:
                idx = int(wid.split("-")[-1])
                accounts = load_login_lookup()
                if 0 <= idx < len(accounts):
                    accounts[idx]["auto_delete"] = event.value
                    save_login_lookup(accounts)
                    self.notify(f"Saved {accounts[idx]['username']}: auto_delete = {event.value}", timeout=2)
            except (ValueError, IndexError):
                self.notify("Error saving auto_delete", severity="error")

    _PREWARM_IDS = {
        "chk-prewarm-gemini":   "gemini",
        "chk-prewarm-deepseek": "deepseek",
        "chk-prewarm-copilot":  "copilot",
        "chk-prewarm-chatgpt":  "chatgpt",
    }

    @on(Checkbox.Changed)
    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not self._mounted:
            return
        wid = event.checkbox.id or ""
        if wid not in self._PREWARM_IDS:
            return

        # Collect current checked state across all four checkboxes
        checked: list[str] = []
        for cid, svc in self._PREWARM_IDS.items():
            cb = self.query_one(f"#{cid}", Checkbox)
            checked.append(svc) if cb.value else None

        # Enforce max 2 (gemini always stays on, so effective user cap is 1 more)
        if len(checked) > 2:
            event.checkbox.value = False  # revert the just-checked box
            self.notify("Max 2 services can be pre-warmed.", severity="warning", timeout=3)
            return

        # gemini must always be checked — prevent unchecking
        if self._PREWARM_IDS.get(wid) == "gemini" and not event.value:
            event.checkbox.value = True
            self.notify("Gemini is the default tab and cannot be removed.", timeout=3)
            return

        save_config({"prewarm_services": checked})
        self.notify(f"Pre-warm set saved: {checked}  (takes effect on next engine start)", timeout=4)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        wid = event.input.id or ""
        if wid in _INPUT_MAP:
            dot_path, typ = _INPUT_MAP[wid]
            try:
                self._save(dot_path, typ(event.value))
            except (ValueError, TypeError):
                self.notify(f"Invalid value for {wid}", severity="error")


    @on(Select.Changed)
    def on_select_changed(self, event: Select.Changed) -> None:
        if not self._mounted:
            return
        wid = event.select.id or ""
        if wid in _SELECT_MAP and event.value is not Select.BLANK:
            self._save(_SELECT_MAP[wid], event.value)
        elif wid.startswith("sel-range-") and event.value is not Select.BLANK:
            try:
                idx = int(wid.split("-")[-1])
                accounts = load_login_lookup()
                if 0 <= idx < len(accounts):
                    accounts[idx]["delete_range"] = event.value
                    save_login_lookup(accounts)
                    self.notify(f"Saved {accounts[idx]['username']}: delete_range = {event.value}", timeout=2)
            except (ValueError, IndexError):
                self.notify("Error saving range", severity="error")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if   bid == "btn-toggle-engine":     self._toggle_engine_service()
        elif bid == "btn-toggle-browser":    self._toggle_browser()
        elif bid == "btn-update-relaunch":   self._update_and_relaunch()
        elif bid == "btn-discover":          self._discover_capabilities()
        elif bid == "btn-save-model-tool":   self._save_model_tool()
        elif bid == "btn-apply-model-tool":  self._apply_model_tool()
        elif bid == "btn-new-chat":          self._action_new_chat()
        elif bid == "btn-submit-prompt":     self._action_submit_prompt()
        elif bid == "btn-upload-file":       self._action_upload_file()
        elif bid == "btn-submit":            self._action_submit()
        elif bid == "btn-redo":              self._action_redo()
        elif bid == "btn-stop":              self._action_stop()
        elif bid == "btn-combine-submit":    self._action_combine_submit()
        elif bid == "btn-combine-redo":      self._action_redo()
        elif bid == "btn-combine-stop":      self._action_stop()
        elif bid == "btn-capture-dom":       self._action_capture_dom()
        elif bid == "btn-check-status":      self._check_login_status()
        elif bid == "btn-add-profile":       self._add_profile()
        elif bid == "btn-prev-profile":  self._switch_profile_dir(-1)
        elif bid == "btn-next-profile":  self._switch_profile_dir(1)
        elif bid == "btn-relogin":       self._relogin_current()
        elif bid == "btn-switch-target": self._switch_target_profile()
        elif bid.startswith("btn-switch-"):
            self._switch_account(event.button.name or "")
        elif bid.startswith("btn-del-"):
            self._delete_account(event.button.name or "")
        elif bid.startswith("btn-delhist-"):
            self._delete_history_for(event.button.name or "")

    # ─── Engine workers ───────────────────────────────────────────────────────

    @work(group="autostart", exclusive=True)
    async def _engine_autostart(self) -> None:
        # Honour the "auto_start_browser" setting: once the freshly-spawned
        # engine service is reachable, log in automatically. This mirrors a
        # manual Restart, so the startup login actions stream into the SERVICE
        # LOG instead of nothing happening until the user clicks Restart.
        if not load_config().get("auto_start_browser", True):
            return
        # Wait (up to ~30s) for our engine service to finish booting.
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                async with httpx.AsyncClient() as c:
                    r = await c.get(f"{ENGINE_URL}/health", timeout=2.0)
                if r.status_code == 200:
                    # Skip if a browser session is already running.
                    if r.json().get("engine_running"):
                        return
                    break
            except Exception:
                pass
        else:
            return
        self.notify("Auto-starting browser...", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/start", json={}, timeout=60)
        except Exception as e:
            self.notify(f"Auto-start failed: {e}", severity="error")

    @work(group="engine_ctrl", exclusive=True)
    async def _toggle_engine_service(self) -> None:
        self._append_log(f"[TUI] Engine toggle clicked (engine_online={self.engine_online})")
        if self.engine_online:
            self.notify("Stopping engine service...", timeout=3)
            await self._shutdown_service()
            self._append_log("[TUI] Engine shutdown requested")
        else:
            self.notify("Starting engine service...", timeout=3)
            self._start_and_stream_service()
            self._engine_autostart()
        await asyncio.sleep(1)
        self._poll_status()

    @work(group="engine_ctrl", exclusive=True)
    async def _update_and_relaunch(self) -> None:
        import subprocess
        if self.engine_online:
            await self._shutdown_service()
            self._append_log("[update] Engine stopped")
        
        git_err = False
        try:
            res = await asyncio.to_thread(
                subprocess.run,
                ["git", "pull"],
                capture_output=True,
                text=True,
                cwd=str(ROOT)
            )
            for line in (res.stdout or "").splitlines():
                self._append_log(f"[git] {line}")
            for line in (res.stderr or "").splitlines():
                self._append_log(f"[git] {line}")
            if res.returncode != 0:
                git_err = True
        except Exception as e:
            self._append_log(f"[git] Error running git pull: {e}")
            git_err = True

        try:
            res_sub = await asyncio.to_thread(
                subprocess.run,
                ["git", "submodule", "update", "--remote", "engine"],
                capture_output=True,
                text=True,
                cwd=str(ROOT)
            )
            for line in (res_sub.stdout or "").splitlines():
                self._append_log(f"[git/engine] {line}")
            for line in (res_sub.stderr or "").splitlines():
                self._append_log(f"[git/engine] {line}")
            if res_sub.returncode != 0:
                git_err = True
        except Exception as e:
            self._append_log(f"[git/engine] Error running git submodule update: {e}")
            git_err = True

        if git_err:
            self._append_log("[update] Warning: Git update had errors — aborting relaunch")
            return

        self._append_log("[update] Stopping engine before relaunch...")
        await self._shutdown_service()
        self._append_log("[update] Relaunching TUI — exiting cleanly first...")
        # ponytail: os.execv() while Textual is running corrupts the Windows terminal
        # (alternate screen not restored). Instead set a flag and exit cleanly; __main__
        # does the execv AFTER Textual has restored the terminal.
        self._relaunch_on_exit = True
        self.exit()

    @work(thread=True, group="check_updates")
    def _check_for_updates(self) -> None:
        import urllib.request, json as _json

        TUI_URL    = "https://raw.githubusercontent.com/liewcc/Gemi_MCP/master/version.json"
        ENGINE_URL = "https://raw.githubusercontent.com/liewcc/Gemi_Engine/master/version.json"

        def _parse(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.strip().split("."))
            except Exception:
                return (0, 0, 0)

        def _fetch_remote_version(url: str) -> tuple:
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    data = _json.loads(r.read())
                return _parse(data.get("version", "0.0.0"))
            except Exception:
                return (0, 0, 0)

        def _read_local_version(path: str) -> tuple:
            try:
                with open(path, encoding="utf-8") as f:
                    data = _json.load(f)
                return _parse(data.get("version", "0.0.0"))
            except Exception:
                return (0, 0, 0)

        local_tui    = _read_local_version(str(ROOT / "version.json"))
        local_engine = _read_local_version(str(ROOT / "engine" / "version.json"))
        remote_tui    = _fetch_remote_version(TUI_URL)
        remote_engine = _fetch_remote_version(ENGINE_URL)

        parts: list[str] = []
        if remote_tui    > local_tui:    parts.append(f"TUI {'.'.join(map(str,local_tui))}→{'.'.join(map(str,remote_tui))}")
        if remote_engine > local_engine: parts.append(f"Engine {'.'.join(map(str,local_engine))}→{'.'.join(map(str,remote_engine))}")

        if parts:
            self.call_from_thread(self._enable_update_button, parts)
        else:
            self.call_from_thread(setattr, self, "_update_status", "[green]✓ Up to date[/green]")

    def _enable_update_button(self, parts: list[str]) -> None:
        try:
            btn = self.query_one("#btn-update-relaunch", Button)
            btn.disabled = False
            btn.label = "⬆ Update & Relaunch  (" + ", ".join(parts) + ")"
        except Exception:
            pass
        self._update_status = "[yellow]⬆ " + ", ".join(parts) + "[/yellow]"
        self.notify("Updates available: " + ", ".join(parts), timeout=6)

    @work(group="browser_ctrl", exclusive=True)
    async def _toggle_browser(self) -> None:
        if self.browser_online:
            self.notify("Stopping browser...", timeout=3)
            try:
                async with httpx.AsyncClient() as c:
                    await c.post(f"{ENGINE_URL}/engine/stop", timeout=20)
                self.notify("Browser stopped")
            except Exception as e:
                self.notify(f"Stop failed: {e}", severity="error")
        else:
            headless = bool(load_config().get("headless", True))
            self.notify(f"Starting browser (headless={headless})...", timeout=3)
            try:
                async with httpx.AsyncClient() as c:
                    await c.post(f"{ENGINE_URL}/engine/start", json={"headless": headless}, timeout=120)
                self.notify("Browser started")
            except Exception as e:
                self.notify(f"Start failed: {e}", severity="error")
        # Poll a few times so the button flips as soon as the engine registers the change.
        for _ in range(4):
            await asyncio.sleep(1)
            self._poll_status()

    # ─── Single / Combine Action Control workers ──────────────────────────────

    def _get_prompt_text(self) -> str:
        try:
            return str(self.query_one("#in-prompt", Input).value)
        except Exception:
            return ""

    @work(group="ops", exclusive=True)
    async def _action_new_chat(self) -> None:
        self.notify("Triggering New Chat…", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/new_chat", timeout=30)
            self.notify(r.json().get("message", "New Chat done"))
        except Exception as e:
            self.notify(f"New Chat failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_submit_prompt(self) -> None:
        text = self._get_prompt_text()
        if not text:
            self.notify("Enter prompt text first", severity="warning")
            return
        self.notify(f"Filling prompt: {text[:40]}…", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/prompt",
                                 json={"text": text, "mode": "default"}, timeout=30)
            self.notify(r.json().get("message", "Prompt filled"))
        except Exception as e:
            self.notify(f"Submit Prompt failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_upload_file(self) -> None:
        self.notify("Syncing attachments…", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/attach_files",
                                 json={"files": []}, timeout=30)
            d = r.json()
            self.notify(f"Sync: added {d.get('added',0)}, removed {d.get('removed',0)}")
        except Exception as e:
            self.notify(f"Upload File failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_submit(self) -> None:
        self.notify("Submitting…", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/submit", timeout=120)
            self.notify(r.json().get("message", "Submit done"))
        except Exception as e:
            self.notify(f"Submit failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_redo(self) -> None:
        self.notify("Redo…", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/redo", timeout=60)
            self.notify(r.json().get("message", "Redo triggered"))
        except Exception as e:
            self.notify(f"Redo failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_stop(self) -> None:
        self.notify("Stopping…", timeout=3)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/stop", timeout=15)
            self.notify(r.json().get("message", "Stopped"))
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_combine_submit(self) -> None:
        text = self._get_prompt_text()
        self.notify("Combine: applying settings → new chat → submit…", timeout=15)
        try:
            async with httpx.AsyncClient() as c:
                # Auto-apply saved settings before every combine
                def _sel(wid_id: str) -> str:
                    try:
                        v = self.query_one(wid_id, Select).value
                        return "" if v is Select.BLANK else str(v)
                    except Exception:
                        return ""
                tool     = _sel("#sel-tool")
                upload   = _sel("#sel-upload")
                model    = _sel("#sel-model")
                thinking = _sel("#sel-thinking")
                for sentinel in ("(click Discover)", "(select More... first)"):
                    if tool     == sentinel: tool     = ""
                    if upload   == sentinel: upload   = ""
                    if model    == sentinel: model    = ""
                    if thinking == sentinel: thinking = ""
                effective_tool = upload if upload else tool
                if model or effective_tool or thinking:
                    await c.post(
                        f"{ENGINE_URL}/browser/apply_settings",
                        json={
                            "model":          model          or None,
                            "tool":           effective_tool or None,
                            "thinking_level": thinking       or None,
                        },
                        timeout=60,
                    )

                await c.post(f"{ENGINE_URL}/browser/new_chat", timeout=30)
                if text:
                    await c.post(f"{ENGINE_URL}/browser/prompt",
                                 json={"text": text, "mode": "default"}, timeout=30)
                r = await c.post(f"{ENGINE_URL}/browser/submit", timeout=120)
            self.notify(r.json().get("message", "Combine done"))
        except Exception as e:
            self.notify(f"Combine Submit failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _action_capture_dom(self) -> None:
        self.notify("Capturing DOM to file…", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/capture_dom", timeout=30)
            self.notify(r.json().get("message", "DOM captured"))
        except Exception as e:
            self.notify(f"Capture DOM failed: {e}", severity="error")

    def _update_discovered_selects(self) -> None:
        """Update only the TOOL & MODEL Select widgets in-place — no recompose needed."""
        disc = self._discovered
        try:
            eng = self.query_one(EngineTab)
        except Exception:
            return

        def _upd(wid_id: str, opts: list) -> None:
            if not opts:
                return
            try:
                eng.query_one(wid_id, Select).set_options(opts)
            except Exception:
                pass

        _upd("#sel-tool",     [(t, t) for t in disc.get("main_tools",      []) if t])
        _upd("#sel-model",    [(m, m) for m in disc.get("models",          []) if m])
        _upd("#sel-thinking", [(t, t) for t in disc.get("thinking_levels", []) if t])
        # sel-upload updates automatically via on_sel_tool_changed when sel-tool value changes

    @work(group="ops", exclusive=True)
    async def _discover_capabilities(self) -> None:
        self._append_log("[TUI] Discover: scanning...")
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/discover", timeout=60)
            data = r.json().get("data", {})
            self._discovered = {
                "models":         data.get("models",        []),
                "thinking_levels": data.get("thinking_levels", []),
                "main_tools":     data.get("main_tools",    []),
                "sub_tools":      data.get("sub_tools",     {}),
                "tools":          data.get("tools",         []),
                "upload_tools":   data.get("upload_tools",  []),
            }
            self._append_log(
                f"[TUI] Discover complete: {len(self._discovered['models'])} models, "
                f"{len(self._discovered['main_tools'])} main tools, "
                f"sub_tools={list(self._discovered['sub_tools'].keys())}"
            )
            self._update_discovered_selects()
        except Exception as e:
            self._append_log(f"[TUI] Discover failed: {e}")
            self.notify(f"Discover failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _save_model_tool(self) -> None:
        try:
            tool     = self.query_one("#sel-tool",     Select).value
            upload   = self.query_one("#sel-upload",   Select).value
            model    = self.query_one("#sel-model",    Select).value
            thinking = self.query_one("#sel-thinking", Select).value
            if tool     is Select.BLANK: tool     = ""
            if upload   is Select.BLANK: upload   = ""
            if model    is Select.BLANK: model    = ""
            if thinking is Select.BLANK: thinking = ""
            _dot_save("selected_tool",            tool)
            _dot_save("selected_upload_tool",     upload)
            _dot_save("selected_model",           model)
            _dot_save("selected_thinking_level",  thinking)
            self.notify(
                f"Saved: model={model or '—'} / thinking={thinking or '—'} / "
                f"tool={tool or '—'} / upload={upload or '—'}",
                timeout=3,
            )
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")

    @work(group="ops", exclusive=True)
    async def _apply_model_tool(self) -> None:
        """Discover → validate saved settings → apply → save config."""
        try:
            # Step 1: fresh discover
            self._append_log("[TUI] Apply: scanning menu...")
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/browser/discover", timeout=60)
            data = r.json().get("data", {})
            self._discovered = {
                "models":         data.get("models",        []),
                "thinking_levels": data.get("thinking_levels", []),
                "main_tools":     data.get("main_tools",    []),
                "sub_tools":      data.get("sub_tools",     {}),
                "tools":          data.get("tools",         []),
                "upload_tools":   data.get("upload_tools",  []),
            }
            # Step 2: read user's selections BEFORE set_options() might reset them
            def _val(wid_id: str) -> str:
                v = self.query_one(wid_id, Select).value
                return "" if v is Select.BLANK else str(v)

            tool     = _val("#sel-tool")
            upload   = _val("#sel-upload")
            model    = _val("#sel-model")
            thinking = _val("#sel-thinking")

            # Now update available options (set_options preserves value if still valid,
            # resets to first option otherwise — which is fine since we already saved the user's choice)
            self._update_discovered_selects()

            # Validate: if user's saved value is no longer in discovered options, use first available
            disc = self._discovered
            main_tools = disc.get("main_tools", [])
            models     = disc.get("models", [])
            thinkings  = disc.get("thinking_levels", [])
            if tool     and tool     not in main_tools:  tool     = main_tools[0]  if main_tools  else ""
            if model    and model    not in models:      model    = models[0]      if models      else ""
            if thinking and thinking not in thinkings:   thinking = thinkings[0]   if thinkings   else ""

            for sentinel in ("(click Discover)", "(select More... first)"):
                if tool     == sentinel: tool     = ""
                if upload   == sentinel: upload   = ""
                if model    == sentinel: model    = ""
                if thinking == sentinel: thinking = ""

            effective_tool = upload if upload else tool
            self._append_log(
                f"[TUI] Apply: model={model or '—'} / thinking={thinking or '—'} / "
                f"tool={effective_tool or '—'}"
            )

            # Step 3: apply to browser
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{ENGINE_URL}/browser/apply_settings",
                    json={
                        "model":          model          or None,
                        "tool":           effective_tool or None,
                        "thinking_level": thinking       or None,
                    },
                    timeout=60,
                )
            msg = r.json().get("message", "done")
            self._append_log(f"[TUI] Apply result: {msg}")

            # Step 4: persist
            _dot_save("selected_tool",           tool)
            _dot_save("selected_upload_tool",    upload)
            _dot_save("selected_model",          model)
            _dot_save("selected_thinking_level", thinking)
            self._append_log("[TUI] Apply: settings saved to config")
        except Exception as e:
            self._append_log(f"[TUI] Apply failed: {e}")
            self.notify(f"Apply failed: {e}", severity="error")

    @work(group="acct", exclusive=True)
    async def _add_profile(self) -> None:
        """Stop any running browser, show input dialog, and register the new email."""
        # Step 1: stop running engine / registration browser if active
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ENGINE_URL}/health", timeout=2.0)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("engine_running"):
                        await c.post(f"{ENGINE_URL}/engine/stop", timeout=20)
                    try:
                        await c.post(f"{ENGINE_URL}/engine/stop_registration", timeout=10)
                    except Exception:
                        pass
                    
                    # Update status
                    r2 = await c.get(f"{ENGINE_URL}/health", timeout=2.0)
                    if r2.status_code == 200:
                        self.engine_online = True
                        self.browser_online = bool(r2.json().get("engine_running"))
                    else:
                        self.engine_online = False
                        self.browser_online = False
                else:
                    self.engine_online = False
                    self.browser_online = False
            self._update_op_buttons()
            self._update_subtitle()
        except Exception:
            pass  # engine offline — nothing to stop

        # Step 2: Push a ModalScreen (Textual modal)
        email = await self.push_screen(AddProfileModal(), wait_for_dismiss=True)
        if not email or not email.strip():
            return

        email = email.strip()

        # Step 3: check for duplicate
        accounts = load_login_lookup()
        normalised = email.lower()
        if any(acc.get("username", "").strip().lower() == normalised for acc in accounts):
            self.notify(f"Profile for {email} already exists!", severity="warning")
            return

        # Step 4: append new entry
        accounts.append({
            "username":        email.split("@")[0],
            "active":          False,
            "quota_full":      "",
            "session_images":  "0",
            "session_refused": "0",
            "session_resets":  "0",
        })
        save_login_lookup(accounts)

        # Step 5: open registration browser
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/engine/start_registration", timeout=30)
            if r.status_code != 200:
                self.notify(
                    f"Registration failed ({r.status_code}): {r.json().get('detail', r.text)}",
                    severity="error",
                    timeout=10,
                )
                return
        except Exception as e:
            self.notify(f"Failed to open registration browser: {e}", severity="error", timeout=10)
            return

        # Step 6: recompose tabs to reflect the new account
        try:
            await self.query_one(EngineTab).recompose()
        except Exception:
            pass
        try:
            await self.query_one(AccountsTab).recompose()
        except Exception:
            pass

        self.notify(
            "Browser opened — log in, then close the window and press Ctrl+R",
            timeout=8,
        )

    # ─── Account actions (Engine tab panel) ─────────────────────────────────────

    def _set_active_profile_label(self, text: str) -> None:
        try:
            self.query_one("#engine-active-profile", Label).update(text)
        except Exception:
            pass

    @work(group="acct", exclusive=True)
    async def _check_login_status(self) -> None:
        cfg     = load_config()
        active  = cfg.get("active_user") or "none"
        label   = active
        online  = False
        browser = False
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ENGINE_URL}/health", timeout=2.0)
                online = r.status_code == 200
                if online:
                    browser = bool(r.json().get("engine_running"))
        except Exception:
            pass
        if browser:
            try:
                async with httpx.AsyncClient() as c:
                    r = await c.get(f"{ENGINE_URL}/browser/account", timeout=20)
                    data = r.json()
                if data.get("logged_in") and data.get("account_id"):
                    label = data["account_id"]
                elif not data.get("logged_in"):
                    label = f"{active} (not logged in)"
            except Exception:
                pass
        if not online:
            state = "engine offline"
        elif not browser:
            state = "engine online, browser stopped"
        else:
            state = "engine online"
        self._set_active_profile_label(label)
        self.notify(f"{state} · {label}", timeout=4)

    @work(group="acct", exclusive=True)
    async def _switch_profile_dir(self, direction: int) -> None:
        ep   = "switch_profile" if direction > 0 else "switch_profile_previous"
        word = "next" if direction > 0 else "previous"
        self.notify(f"Switching to {word} profile...", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/engine/{ep}", timeout=90)
            msg = r.json().get("message", "done")
            await self.query_one(AccountsTab).recompose()
            self._update_subtitle()
            self._set_active_profile_label(load_config().get("active_user") or "none")
            self.notify(f"Profile switch: {msg}")
        except Exception as e:
            self.notify(f"Switch failed: {e}", severity="error")

    @work(group="acct", exclusive=True)
    async def _relogin_current(self) -> None:
        self.notify("Re-logging current profile...", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{ENGINE_URL}/engine/re_login_current_profile", timeout=90)
            msg = r.json().get("message", "done")
            self.notify(f"Re-login: {msg}")
        except Exception as e:
            self.notify(f"Re-login failed: {e}", severity="error")

    @work(group="acct", exclusive=True)
    async def _switch_target_profile(self) -> None:
        try:
            sel = self.query_one("#sel-target-profile", Select)
        except Exception:
            self.notify("No profiles available", severity="warning")
            return
        username = sel.value
        if username is Select.BLANK or not username:
            self.notify("Select a target profile first", severity="warning")
            return
        self.notify(f"Switching to {username}...", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{ENGINE_URL}/engine/switch_to_profile",
                    params={"username": username},
                    timeout=90,
                )
            msg = r.json().get("message", "done")
            await self.query_one(AccountsTab).recompose()
            self._update_subtitle()
            self._set_active_profile_label(load_config().get("active_user") or "none")
            self.notify(f"Switched: {msg}")
        except Exception as e:
            self.notify(f"Switch failed: {e}", severity="error")

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
        # Remove from user_login_lookup.json
        accounts = [a for a in load_login_lookup() if a.get("username") != username]
        save_login_lookup(accounts)

        profile_found = False
        local_state_path = ROOT / "runtime" / "browser_user_data" / "Local State"
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                state = _json.load(f)
            info_cache = state.get("profile", {}).get("info_cache", {})
            for profile_dir, p_info in info_cache.items():
                user_name_val = p_info.get("user_name", "")
                if isinstance(user_name_val, str) and user_name_val:
                    # user_name (lowercased, part before @)
                    email_part = user_name_val.lower().split("@")[0]
                    # matches the username being deleted
                    if email_part == username.lower().split("@")[0]:
                        profile_path = ROOT / "runtime" / "browser_user_data" / profile_dir
                        shutil.rmtree(profile_path, ignore_errors=True)
                        profile_found = True
                        self._append_log(
                            f"[TUI] Deleted Chrome profile dir: {profile_dir} for {username}"
                        )
                        break
        except FileNotFoundError:
            pass  # No Local State file — nothing to clean
        except Exception as e:
            self._append_log(f"[TUI] Warning: could not clean Chrome profile for {username}: {e}")

        try:
            await self.query_one(EngineTab).recompose()
        except Exception:
            pass
        try:
            await self.query_one(AccountsTab).recompose()
        except Exception:
            pass
        if profile_found:
            self.notify(f"Deleted {username} (and browser profile)")
        else:
            self.notify(f"Deleted {username}")

    @work
    async def _delete_history_for(self, username: str) -> None:
        accounts = load_login_lookup()
        acc = next((a for a in accounts if a.get("username") == username), {})
        del_range = acc.get("delete_range", "Last hour")
        self.notify(f"Deleting history ({del_range}) for {username}...", timeout=5)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{ENGINE_URL}/engine/delete_history",
                    json={"range": del_range},
                    timeout=60,
                )
            msg = r.json().get("message", "done")
            self.notify(f"History deleted: {msg}")
        except Exception as e:
            self.notify(f"Delete history failed: {e}", severity="error")

    # ─── App actions ──────────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        self.notify("Shutting down service…", timeout=3)
        await self._shutdown_service()
        self.exit()

    async def _shutdown_service(self) -> None:
        import subprocess as _sp

        self._append_log("[TUI] _shutdown_service: step 1 — POST /engine/stop")
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/stop", timeout=3)
            self._append_log("[TUI] _shutdown_service: /engine/stop OK")
        except Exception as e:
            self._append_log(f"[TUI] _shutdown_service: /engine/stop failed ({e})")

        # Kill the process actually listening on port 18800. Run in a thread so we
        # don't block the event loop during netstat (can take ~500 ms on Windows).
        self._append_log("[TUI] _shutdown_service: step 2 — netstat kill")
        try:
            port_pid = await asyncio.to_thread(self._find_port_pid, 18800)
            if port_pid:
                self._append_log(f"[TUI] _shutdown_service: taskkill PID {port_pid}")
                await asyncio.to_thread(
                    _sp.run,
                    ["taskkill", "/F", "/T", "/PID", port_pid],
                    check=False, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
            else:
                self._append_log("[TUI] _shutdown_service: port 18800 not found in netstat")
        except Exception as e:
            self._append_log(f"[TUI] _shutdown_service: netstat/taskkill error ({e})")

        # Also kill our managed process handle as belt-and-suspenders.
        self._append_log(f"[TUI] _shutdown_service: step 3 — proc handle kill (pid={self._service_proc.pid if self._service_proc else 'None'})")
        if self._service_proc is not None:
            try:
                self._service_proc.kill()
            except Exception:
                pass
            self._service_proc = None
        self._append_log("[TUI] _shutdown_service: done")

    async def action_reload_config(self) -> None:
        for tab_cls in (EngineTab, AccountsTab):
            try:
                await self.query_one(tab_cls).recompose()
            except Exception:
                pass
        self._update_subtitle()
        self.notify("Config reloaded")

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        is_accounts = event.pane.id == "tab-accounts"
        self.query_one("#right-panel").display = not is_accounts
        self.query_one(TabbedContent).styles.width = "1fr" if is_accounts else 62


if __name__ == "__main__":
    import os
    app = GemiTUI()
    app.run()
    if getattr(app, "_relaunch_on_exit", False):
        os.execv(sys.executable, [sys.executable] + sys.argv)
