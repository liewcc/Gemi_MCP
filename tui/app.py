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
RANGE_OPTIONS: list[tuple[str, str]] = [
    ("All", "All"),
    ("1-10", "1-10"),
    ("11-20", "11-20"),
    ("21-30", "21-30"),
    ("31-40", "31-40"),
    ("41-50", "41-50"),
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


# ─── Tab content widgets ──────────────────────────────────────────────────────

class EngineTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        c = load_config()
        disc = c.get("discovery", {})
        avail_models: list[str] = disc.get("available_models", [])
        avail_tools:  list[str] = disc.get("available_tools", [])

        # ── ENGINE OPERATIONS (mirrors GemiPersonaPro_DT setup panel) ──
        yield Label("ENGINE OPERATIONS", classes="section-title")
        yield Rule()
        with Horizontal(classes="action-row"):
            yield Button("Start Engine",  id="btn-toggle-engine",  variant="primary")
            yield Button("Start Browser", id="btn-toggle-browser", variant="primary")
        yield SettingRow("◎  Headless mode",      Switch(c.get("headless", True),             id="sw-headless"))
        yield SettingRow("▷  Auto-start browser", Switch(c.get("auto_start_browser", True),   id="sw-auto_start"))
        yield SettingRow("↺  Auto-continue loop", Switch(c.get("auto_continue_loop", False),  id="sw-auto_continue"))

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
        with Horizontal(classes="action-row"):
            yield Button("📋 Add profile", id="btn-add-profile", variant="success")
            yield Button("⏮ Prev",        id="btn-prev-profile")
            yield Button("🔄 Re-login",    id="btn-relogin")
            yield Button("⏭ Next",        id="btn-next-profile")
        with Horizontal(classes="action-row"):
            if profile_opts:
                yield Select(profile_opts, prompt="Select target profile…", id="sel-target-profile")
            else:
                yield Label("No profiles found — add one above.", classes="hint")
            yield Button("👤 Switch", id="btn-switch-target")

        yield Label("BROWSER", classes="section-title")
        yield Rule()
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

                range_val = acc.get("range") or "All"
                if not any(v == range_val for _, v in RANGE_OPTIONS):
                    range_val = "All"

                with Vertical(classes="account-card"):
                    with Horizontal(classes="account-card-row-top"):
                        yield Label(username, classes="acct-email")
                        yield Label(badge,    classes=f"acct-badge {badge_cls}")
                        if not is_active:
                            yield Button("⇄ Switch", id=f"btn-switch-{i}", name=username, classes="acct-btn")
                        yield Button("✕ Del", id=f"btn-del-{i}", name=username, classes="acct-btn acct-del")
                    with Horizontal(classes="account-card-row-bottom"):
                        yield Label("Auto Del", classes="acct-lbl-autodel")
                        yield Switch(acc.get("auto_delete", False), id=f"sw-autodel-{i}")
                        yield Label("Range", classes="acct-lbl-range")
                        yield Select(RANGE_OPTIONS, value=range_val, id=f"sel-range-{i}", allow_blank=False)

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

    .acct-status-row   { height: 3; align: left middle; }
    .acct-status-label { width: auto; content-align: left middle; color: $text-muted; }
    .acct-status-value { width: 1fr; content-align: left middle; color: $accent; text-style: bold; padding: 0 0 0 1; }
    .acct-act-btn      { min-width: 16; }

    .account-card { height: 6; align: left middle; border-bottom: solid $surface; }
    .account-card-row-top { height: 3; align: left middle; }
    .account-card-row-bottom { height: 3; align: left middle; }
    .acct-email   { width: 1fr; content-align: left middle; }
    .acct-badge   { width: 14; content-align: left middle; }
    .badge-active { color: $success; }
    .badge-quota  { color: $warning; }
    .badge-idle   { color: $text-muted; }
    .acct-btn     { min-width: 8; margin: 0 0 0 1; }
    .acct-lbl-autodel { width: 11; content-align: left middle; }
    .acct-lbl-range   { width: 8; content-align: right middle; margin: 0 1 0 2; }
    .account-card-row-bottom Select { width: 16; }

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

    engine_online:  reactive[bool] = reactive(False)
    browser_online: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        self._mounted = False
        self._service_proc = None
        # Job Object keeps the engine bound to this process — if the TUI is
        # killed via the window 'X' (no action_quit), the OS kills the engine.
        self._job = _create_kill_on_close_job()

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
        self._engine_autostart()

    # ─── Service process management ───────────────────────────────────────────

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

        if _port_open():
            # A leftover engine (e.g. from a crash or Ctrl+C) still holds the port.
            # Kill it and spawn our own so we always have a live stdout stream to
            # display, instead of silently short-circuiting with no log output.
            self.call_from_thread(
                self._append_log,
                "[engine service already running on port 18800 — killing leftover and respawning...]",
            )
            self._kill_leftover_engine()
            # Wait (up to ~5s) for the port to actually free before we bind it.
            for _ in range(20):
                time.sleep(0.25)
                if not _port_open():
                    break
            else:
                self.call_from_thread(
                    self._append_log,
                    "[warning: port 18800 still in use after kill; spawn may fail]",
                )
        CREATE_NO_WINDOW = 0x08000000
        self._service_proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "core" / "engine_service.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT / "core"),
            creationflags=CREATE_NO_WINDOW,
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
                r = await client.get(f"{ENGINE_URL}/health", timeout=2.0)
                online = r.status_code == 200
                if online:
                    browser = bool(r.json().get("engine_running"))
        except Exception:
            pass
        self.engine_online  = online
        self.browser_online = browser
        self._update_subtitle()
        self._update_op_buttons()

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
        elif wid.startswith("sel-range-") and event.value is not Select.BLANK:
            try:
                idx = int(wid.split("-")[-1])
                accounts = load_login_lookup()
                if 0 <= idx < len(accounts):
                    accounts[idx]["range"] = event.value
                    save_login_lookup(accounts)
                    self.notify(f"Saved {accounts[idx]['username']}: range = {event.value}", timeout=2)
            except (ValueError, IndexError):
                self.notify("Error saving range", severity="error")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if   bid == "btn-toggle-engine":  self._toggle_engine_service()
        elif bid == "btn-toggle-browser": self._toggle_browser()
        elif bid == "btn-add-account":   self._add_account()
        elif bid == "btn-check-status":  self._check_login_status()
        elif bid == "btn-add-profile":   self._add_account()
        elif bid == "btn-prev-profile":  self._switch_profile_dir(-1)
        elif bid == "btn-next-profile":  self._switch_profile_dir(1)
        elif bid == "btn-relogin":       self._relogin_current()
        elif bid == "btn-switch-target": self._switch_target_profile()
        elif bid.startswith("btn-switch-"):
            self._switch_account(event.button.name or "")
        elif bid.startswith("btn-del-"):
            self._delete_account(event.button.name or "")

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

    @work(group="ops", exclusive=True)
    async def _toggle_engine_service(self) -> None:
        if self.engine_online:
            self.notify("Stopping engine service...", timeout=3)
            await self._shutdown_service()
        else:
            self.notify("Starting engine service...", timeout=3)
            self._start_and_stream_service()
        self._poll_status()

    @work(group="ops", exclusive=True)
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
        self._poll_status()

    @work
    async def _add_account(self) -> None:
        try:
            async with httpx.AsyncClient() as c:
                await c.post(f"{ENGINE_URL}/engine/start_registration", timeout=30)
            self.notify("Registration browser opened — log in, then reload (ctrl+r)")
        except Exception as e:
            self.notify(f"Failed: {e}", severity="error")

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
            # Fallback: service was already running before TUI started.
            self._kill_leftover_engine()

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
