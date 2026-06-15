import asyncio
import os
import sys
import time
import json
import traceback
from config_utils import load_config, save_config
from playwright.async_api import async_playwright
from datetime import datetime

# Fix for Windows asyncio NotImplementedError with subprocesses
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

class BrowserEngine:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self.is_running = False
        self._state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "browser_state.json"))
        self._sandbox_dir = None
        self._log_queue = []
        self._log_history = []
        self._stop_automation_event = asyncio.Event()
        self.automation_status = {
            "is_running": False,
            "mode": "rounds",
            "goal": 0,
            "cycles": 0,
            "successes": 0,
            "refusals": 0,
            "resets": 0,
            "pending_refused": 0,
            "pending_resets": 0,
            "start_time": None,
            "initial_user": None
        }
        # Per-image reject rate tracking
        self._reject_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "reject_stat_log.json"))
        self._cycle_start_time = None   # float: time.time() at start of current cycle
        self._pending_refused = 0       # refused count waiting to be attributed to next successful image
        self._pending_resets = 0        # reset count waiting to be attributed to next successful image
        self._automation_needs_new_chat = True # Flag to force New Chat on next cycle
        self._session_lost = False      # Watchdog flag for engine_service to detect logout
        self._watchdog_task = None      # Handle for the background watchdog task
        self._watchdog_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "watchdog.log"))
        # Per-account session stats snapshot: captured when an account becomes active.
        # Stores {"successes": N, "refusals": N, "resets": N} so that per-account deltas
        # can be computed when that account is later switched away.
        self._acct_snapshot = None
        # Registration browser handles (separate from main browser)
        self._reg_playwright = None
        self._reg_context = None
        self._engine_log_last_pos = None



    @property
    def current_url(self):
        """Returns the current page URL."""
        if self._page:
            return self._page.url
        return None

    @property
    def browser_pids(self):
        """Returns a list of all browser-related PIDs."""
        pids = []
        try:
            import psutil
            current_proc = psutil.Process(os.getpid())
            for child in current_proc.children(recursive=True):
                try:
                    name = child.name().lower()
                    if "chrome" in name or "chromium" in name:
                        if child.pid not in pids:
                            pids.append(child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return pids

    @property
    def browser_pid(self):
        """Returns the main browser PID (first in list)."""
        pids = self.browser_pids
        return pids[0] if pids else None

    async def inject_session_state(self):
        """Inject saved session state from browser_state.json."""
        if not os.path.exists(self._state_file) or not self._context:
            return
        try:
            import json
            with open(self._state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if 'cookies' in state:
                await self._context.add_cookies(state['cookies'])
        except Exception as e:
            print(f"Session injection failed: {e}")

    async def save_session_state(self):
        """Safely export current state."""
        if self._context:
            try:
                await self._context.storage_state(path=self._state_file)
            except Exception as e:
                print(f"Session save failed: {e}")

    async def apply_hardcore_stealth(self, page):
        """Manual JS injection for anti-detection and auto-interruption handling."""
        try:
            await page.add_init_script("""
                // Anti-detection
                Object.defineProperty(navigator, 'webdriver', {get: () => False});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

                // Proactive Dialog Dismissal (MutationObserver)
                const observer = new MutationObserver((mutations) => {
                    for (const mutation of mutations) {
                        for (const node of mutation.addedNodes) {
                            if (node.nodeType === 1) { // Element node
                                // Target the "Agree" button in the MMGen disclaimer dialog
                                const agreeBtn = node.querySelector('button[data-test-id="upload-image-agree-button"]');
                                if (agreeBtn) {
                                    console.log("[GemiPersona] Disclaimer detected. Auto-clicking Agree...");
                                    agreeBtn.click();
                                }
                            }
                        }
                    }
                });
                observer.observe(document.documentElement, { childList: true, subtree: true });
            """)
        except Exception as e:
            print(f"Stealth injection failed: {e}")

    async def _cleanup_sandbox(self):
        """Cleanup junction and sandbox directory."""
        if self._sandbox_dir and os.path.exists(self._sandbox_dir):
            try:
                # Remove junction first (Windows 'rmdir' on junction doesn't delete source)
                junction_path = os.path.join(self._sandbox_dir, "Default")
                if os.path.exists(junction_path):
                    import subprocess
                    subprocess.run(['rmdir', junction_path], shell=True, capture_output=True)
                
                # Small delay to release file handles
                import shutil
                shutil.rmtree(self._sandbox_dir, ignore_errors=True)
            except Exception as e:
                pass  # Sandbox cleanup failed silently
            self._sandbox_dir = None

    async def start(self, headless=True, url="https://gemini.google.com/app", profile_name="Default"):
        """
        Scheme A: Dynamic Sandbox - Creates a junction to the target profile
        and launches Playwright with a unique temporary user data dir.
        """
        self.last_headless = headless
        
        if self.is_running:
            return
        
        # Guard: close any lingering registration browser before starting the main one
        await self.stop_registration()
        
        # 1. Prepare Sandbox
        base_dir = os.path.abspath(os.path.dirname(__file__))
        source_user_data = os.path.join(base_dir, "browser_user_data")
        
        # Note: _cleanup_sandbox() sets self._sandbox_dir to None, so we must 
        # initialize/re-initialize it AFTER cleanup.
        temp_sandbox_path = os.path.join(base_dir, "browser_session_sandbox")
        if os.path.exists(temp_sandbox_path):
            # Temporarily set it so cleanup knows what to delete
            self._sandbox_dir = temp_sandbox_path
            await self._cleanup_sandbox()
        
        self._sandbox_dir = temp_sandbox_path
        os.makedirs(self._sandbox_dir, exist_ok=True)
        
        # 2. Map Profile
        if profile_name:
            target_profile_path = os.path.join(source_user_data, profile_name)
            sandbox_default = os.path.join(self._sandbox_dir, "Default")
            
            if os.path.exists(target_profile_path):
                import subprocess
                # Create Junction: Playwright will look for 'Default' inside the sandbox
                cmd = f'mklink /J "{sandbox_default}" "{target_profile_path}"'
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"[ERROR] Junction failed (Code {res.returncode}): {res.stderr.strip()}")
                else:
                    if os.path.exists(sandbox_default):
                        pass  # Junction verified
                    else:
                        print(f"[ERROR] Junction reported success but path does not exist!")
            else:
                print(f"[ERROR] Source profile path not found: {target_profile_path}")
            
            # Copy root config files
            import shutil
            for f_name in ["Local State", "Variations"]:
                src = os.path.join(source_user_data, f_name)
                if os.path.exists(src):
                    dest = os.path.join(self._sandbox_dir, f_name)
                    shutil.copy2(src, dest)
                    
                    # Force "Default" profile in Local State to match our junction
                    if f_name == "Local State":
                        try:
                            import json
                            with open(dest, "r", encoding="utf-8") as f:
                                state = json.load(f)
                            if "profile" in state:
                                state["profile"]["last_used"] = "Default"
                                state["profile"]["last_active_profiles"] = ["Default"]
                            with open(dest, "w", encoding="utf-8") as f:
                                json.dump(state, f)
                            pass  # Local State patched
                        except Exception as e:
                            print(f"[ERROR] Failed to patch Local State: {e}")
            
            # Explicitly force "Last Profile" file in sandbox root
            try:
                last_profile_path = os.path.join(self._sandbox_dir, "Last Profile")
                with open(last_profile_path, "w", encoding="utf-8") as f:
                    f.write("Default")
                pass  # 'Last Profile' file created
            except Exception as e:
                print(f"[ERROR] Failed to create 'Last Profile': {e}")

        # 3. Launch Playwright
        self._playwright = await async_playwright().start()
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        target_viewport = {'width': 2560, 'height': 1440} if headless else None
        
        launch_args = [
            "--start-minimized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--safebrowsing-disable-download-protection"
        ]
        
        # Use our sandbox as the persistent context root
        launch_dir = self._sandbox_dir if profile_name else source_user_data
        self._user_data_dir = launch_dir  # Stored so download_images() can locate Chrome's download dir
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=launch_dir,
            headless=headless,
            user_agent=user_agent,
            viewport=target_viewport,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=launch_args,
            ignore_https_errors=True,
            java_script_enabled=True,
            device_scale_factor=1,
            accept_downloads=True,
            bypass_csp=True
        )
        
        # Removed manual state injection - Playwright Persistent Context 
        # handles this more reliably via the profile folder itself.
        # if headless:
        #    await self.inject_session_state()

        self._page = await self._context.new_page()
        await self.apply_hardcore_stealth(self._page)
        
        # Force minimize for non-headless mode.
        # --start-minimized gets overridden by Playwright's new_page(), so we
        # re-minimize via CDP to keep the headed fallback window invisible.
        if not headless:
            await self._force_minimize_window()
        
        self.is_running = True

    async def _force_minimize_window(self):
        """Use CDP to force the browser window into minimized state.
        
        Called after new_page() in non-headless mode because Playwright's
        page creation overrides Chrome's --start-minimized flag.
        """
        if not self._page:
            return
        try:
            cdp = await self._page.context.new_cdp_session(self._page)
            window_info = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {
                "windowId": window_info["windowId"],
                "bounds": {"windowState": "minimized"}
            })
        except Exception as e:
            pass  # CDP minimize failed silently

    async def stop(self):
        """Stops the browser session and cleans up sandbox."""
        if not self.is_running:
            return
        
        # Removed manual state save 
        # await self.save_session_state()
        
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
            
        self.is_running = False
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        
        # Final cleanup
        await self._cleanup_sandbox()

    async def start_registration(self):
        """
        Opens a headed browser directly against browser_user_data/ (no sandbox).
        Allows the user to add new Google accounts / Chrome profiles.
        Data is written directly to disk and will be visible to the engine on next start.
        """
        # Close any previous registration browser first
        await self.stop_registration()
        
        base_dir = os.path.abspath(os.path.dirname(__file__))
        user_data_dir = os.path.join(base_dir, "browser_user_data")
        
        self._reg_playwright = await async_playwright().start()
        self._reg_context = await self._reg_playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=["--start-minimized", "--disable-blink-features=AutomationControlled", "--no-sandbox"],
            ignore_https_errors=True,
            bypass_csp=True
        )
        print("[REG] Registration browser started. user_data_dir:", user_data_dir)

    async def stop_registration(self):
        """Closes the registration browser if it is open."""
        if self._reg_context:
            try:
                await self._reg_context.close()
            except Exception as e:
                print(f"[REG] Error closing registration context: {e}")
            self._reg_context = None
        if self._reg_playwright:
            try:
                await self._reg_playwright.stop()
            except Exception as e:
                print(f"[REG] Error stopping registration playwright: {e}")
            self._reg_playwright = None
        print("[REG] Registration browser stopped.")


    async def navigate(self, url):
        """Navigates to a URL using reference-aligned wait state."""
        if not self.is_running:
            raise Exception("Browser Engine not started")
        
        try:
            # Use domcontentloaded as per reference watcher.py (more stable for SPAs)
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # PROACTIVE: Check for agreement popups immediately after navigation
            await self.dismiss_agreement_popups()
            await asyncio.sleep(0.5)  # Grace period: let DOM stabilize after popup dismissal
            # Re-minimize after navigation if running in non-headless mode,
            # since goto() can restore a minimized window.
            if not self.last_headless:
                await self._force_minimize_window()
            return response.status if response else 0
        except Exception as e:
            print(f"Navigation warning: {e}")
            return 200 

    async def send_prompt(self, text):
        """Types text into Gemini's prompt area and sends it."""
        if not self.is_running:
            raise Exception("Browser Engine not started")
        
        # Target Gemini's prompt input (common selectors)
        prompt_selectors = [
            "div[aria-label='Enter a prompt for Gemini']",
            "div[aria-label='Enter a prompt here']",
            "div.ql-editor[contenteditable='true']",
            "textarea[aria-label='Enter a prompt for Gemini']",
            "textarea[aria-label='Enter a prompt here']",
            # NOTE: "[contenteditable='true']" removed â€” too broad, causes Playwright
            # strict=True violation when multiple contenteditable elements exist (e.g.
            # after a popup is dismissed and Gemini re-renders its UI).
        ]
        
        target = None
        retry_waits = [0, 3, 5, 8]  # Progressive waits in seconds (first attempt is instant)
        for attempt, wait_sec in enumerate(retry_waits):
            if wait_sec > 0:
                self._log_debug(f"Prompt input not found. Retrying in {wait_sec}s (attempt {attempt + 1}/{len(retry_waits)})...")
                await asyncio.sleep(wait_sec)
            for sel in prompt_selectors:
                try:
                    elem = self._page.locator(sel).first
                    if await elem.is_visible(timeout=2000):
                        target = elem
                        break
                except:
                    continue
            if target:
                break
        
        if not target:
            raise Exception("Could not find prompt input area on current page.")
        
        # Clear existing text if any (Gemini uses contenteditable often)
        await target.click()
        # For contenteditable, sometimes we need to select all and delete
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")
        
        # Type the new prompt
        await target.fill(text) if await target.is_editable() else await target.type(text)
        
        return {"status": "filled", "prompt": text}

    async def attach_files(self, file_paths):
        """
        Smart Incremental Sync (Stem-Based):
        1. Scans Gemini DOM for existing attached filenames.
        2. Compares filenames by STEM (name without extension) to handle Gemini's auto-conversion/renaming.
        3. Only Adds/Removes the differences.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")
        
        LOG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "engine.log"))
        
        def log_debug(msg):
            timestamp = datetime.now().strftime("[%H:%M:%S]")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [SYNC] {msg}\n")

        # 1. Detection Phase: Get filenames currently in Gemini
        raw_labels = await self._page.evaluate('''() => {
            const buttons = Array.from(document.querySelectorAll('button[data-test-id="cancel-button"]'));
            return buttons.map(btn => btn.getAttribute('aria-label') || "").filter(l => l.length > 0);
        }''')
        
        attached_filenames = []
        for label in raw_labels:
            parts = label.split()
            low_parts = [p.lower() for p in parts]
            if "file" in low_parts:
                idx = low_parts.index("file")
                name = " ".join(parts[idx+1:]).strip()
            else:
                name = parts[-1].strip()
            if name.endswith('.'): name = name[:-1]
            attached_filenames.append(name.strip())

        # CRITICAL FIX: Match by STEM (filename without extension)
        # Because Gemini often renames .png to .jpg in the label.
        def get_stem(filename):
            return os.path.splitext(filename)[0].lower()

        attached_stems = [get_stem(n) for n in attached_filenames]
        
        # Build local target mapping by stem
        target_map = {} # stem -> (original_name, full_path)
        for p in file_paths:
            base = os.path.basename(p)
            target_map[get_stem(base)] = (base, p)
        
        target_stems = list(target_map.keys())
        
        log_debug(f"Browser has (Raw): {attached_filenames}")
        log_debug(f"Target has (Raw): {[v[0] for v in target_map.values()]}")
        log_debug(f"Matching via stems: {attached_stems} vs {target_stems}")
        
        added_count = 0
        removed_count = 0
        
        # 2. Remove Phase: Delete files from browser whose STEM is NOT in target
        for i, stem in enumerate(attached_stems):
            if stem not in target_stems:
                real_name = attached_filenames[i]
                try:
                    log_debug(f"Removing (Stem mismatch): {real_name}")
                    selector = f'button[data-test-id="cancel-button"][aria-label*="{real_name}"]'
                    btn = self._page.locator(selector).first
                    if await btn.is_visible():
                        await btn.click()
                        removed_count += 1
                        await asyncio.sleep(0.8)
                except Exception as e:
                    log_debug(f"Remove failed: {real_name} -> {e}")

        # 3. Add Phase: Upload local files whose STEM is NOT in browser
        for stem, (orig_name, full_path) in target_map.items():
            if stem not in attached_stems:
                if not os.path.exists(full_path):
                    continue
                
                try:
                    log_debug(f"Adding (New stem): {orig_name}")
                    async with self._page.expect_file_chooser(timeout=20000) as fc_info:
                        await self._page.evaluate('''() => {
                            const plusBtn = document.querySelector('button[aria-label="Upload & tools"]') ||
                                            document.querySelector('button[aria-label="Open upload file menu"]') ||
                                            document.querySelector('button[aria-label*="upload" i]') ||
                                            document.querySelector('button[aria-label*="Upload" i]');
                            if (plusBtn) {
                                plusBtn.click();
                            } else {
                                const gemsIcon = document.querySelector('mat-icon[data-mat-icon-name="add_2"]') || 
                                               document.querySelector('mat-icon[fonticon="add"]');
                                if (gemsIcon) { gemsIcon.closest('button').click(); }
                            }
                        }''')
                        await asyncio.sleep(1.2)
                        await self._page.evaluate('''() => {
                            const explicitIcon = document.querySelector('[data-test-id="local-images-files-uploader-icon"]');
                            if (explicitIcon) {
                                const menuItem = explicitIcon.closest('.mat-mdc-menu-item, [role="menuitem"], button');
                                if (menuItem) {
                                    menuItem.click();
                                    return;
                                }
                            }
                            
                            const opt = Array.from(document.querySelectorAll('.menu-text, span, .mdc-list-item__primary-text'))
                                             .find(i => {
                                                 const txt = i.innerText.toLowerCase();
                                                 return txt.includes("upload") || txt.includes("attach");
                                             });
                            if (opt) opt.click();
                        }''')
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(full_path)
                    
                    # PROACTIVE: Immediately check for the MMGen disclaimer after upload
                    await self.dismiss_agreement_popups()
                    
                    added_count += 1
                    await asyncio.sleep(2.5)
                except Exception as e:
                    log_debug(f"Add failed: {orig_name} -> {e}")
            else:
                log_debug(f"Skipping (Stem already present): {orig_name}")
        
        return {
            "status": "success", 
            "added": added_count, 
            "removed": removed_count, 
            "total_now": len(file_paths)
        }

    def _log_debug(self, msg, event_type=None):
        """Helper to log debug info to engine.log and internal queue."""
        LOG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "engine.log"))
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        # Standardize prefix for the UI backend logs
        log_msg = f"{timestamp} API>> {msg}"

        # Mirror to stdout so the console / TUI SERVICE LOG shows these too,
        # not just the engine.log file and the in-memory queue.
        print(log_msg, flush=True)

        # Add to internal queue for API consumption
        if not hasattr(self, '_log_queue'):
             self._log_queue = []
        self._log_queue.append(log_msg)
        # Keep queue somewhat bounded
        if len(self._log_queue) > 500:
             self._log_queue = self._log_queue[-500:]

        # Add to history buffer for cross-page persistence
        if not hasattr(self, '_log_history'):
             self._log_history = []
        self._log_history.append(log_msg)
        if len(self._log_history) > 500:
             self._log_history = self._log_history[-500:]

        import json
        msg_lower = msg.lower()
        
        if event_type is None:
            event_type = "DEBUG"
            if "--- [auto] running round" in msg_lower:
                event_type = "START"
            elif "response successful" in msg_lower or ("saved:" in msg_lower and ".png" in msg_lower):
                event_type = "SUCCESS"
            elif "response failed (refused)" in msg_lower or "treating as refusal" in msg_lower or "gemini refused" in msg_lower:
                event_type = "REJECT"
            elif (
                "gemini page was unexpectedly reset" in msg_lower 
                or "automation loop encountered an issue" in msg_lower
                or "env reset detected" in msg_lower
                or "reset detected during redo" in msg_lower
                or "reset detected in cycle" in msg_lower
                or "submission likely failed" in msg_lower
                or "reset during redo" in msg_lower
                or "reset unexpectedly" in msg_lower
                or "automation error in cycle" in msg_lower
                or "automation error (recovered)" in msg_lower
            ):
                event_type = "RESET"
            elif "automation manager started" in msg_lower or "automation finished" in msg_lower:
                event_type = "BOUNDARY"
            elif "switched to" in msg_lower:
                event_type = "ACCOUNT_SWITCH"
        
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "round": self.automation_status.get("cycles", 0) + 1,
            "account": (self.automation_status.get("current_account_id") or self.automation_status.get("initial_user") or "unknown").split('@')[0].lower(),
            "event": event_type,
            "message": msg
        }
        
        if "rejectstat: wrote record for" in msg_lower:
            entry["event"] = "REJECT_STAT"
            import re
            stat_match = re.search(r"dur=([\d.]+)s, ref=(\d+), rst=(\d+)", msg)
            if stat_match:
                entry["duration"] = float(stat_match.group(1))
                entry["reject"] = int(stat_match.group(2))
                entry["reset"] = int(stat_match.group(3))
            fname_match = re.search(r"for\s+([^ ]+)\s+\(", msg)
            if fname_match:
                entry["filename"] = fname_match.group(1).strip()
                
        if "saved: " in msg_lower:
            try:
                entry["filename"] = msg.split("Saved: ")[1].strip()
            except:
                pass

        json_line = json.dumps(entry, ensure_ascii=False) + "\n"

        # Write to engine.log (always)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json_line)
        except:
            pass

    def clear_physical_logs(self):
        """Truncates the engine.log file."""
        LOG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "engine.log"))
        try:
            import json
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "round": 0,
                "account": "system",
                "event": "LOG_CLEARED",
                "message": "Engine log cleared by user."
            }
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            print(f"Failed to clear log: {e}")
            return False

    def _log_watchdog(self, msg, to_ui=False):
        """Helper to log anomalies to watchdog.log and optionally to the UI."""
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        log_entry = f"{timestamp} {msg}\n"
        try:
            with open(self._watchdog_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except:
            pass
        
        if to_ui:
            self._log_debug(f"WATCHDOG>> {msg}")
            # Ensure the critical record is also printed to console as per "æ­£å¼log" request
            timestamp_now = datetime.now().strftime("[%H:%M:%S]")
            print(f"{timestamp_now} WATCHDOG>> {msg}")

    def get_and_clear_logs(self):
        """Returns all queued logs and clears the queue."""
        if not hasattr(self, '_log_queue'):
             self._log_queue = []
        logs = list(self._log_queue)
        self._log_queue.clear()
        return logs

    def get_log_history(self):
        """Returns the full log history buffer without clearing it."""
        if not hasattr(self, '_log_history'):
             self._log_history = []
        return list(self._log_history)


    def _write_reject_stat(self, filename, duration_sec, refused_count, reset_count):
        """Appends a per-image stat record to reject_stat_log.json."""
        try:
            records = []
            if os.path.exists(self._reject_log_path):
                with open(self._reject_log_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            image_count = sum(1 for r in records if not str(r.get("filename", "")).startswith("["))
            idx_val = image_count + 1 if not filename.startswith("[") else "-"
            
            records.append({
                "index": idx_val,
                "filename": filename,
                "duration_sec": round(duration_sec, 2),
                "refused_count": refused_count,
                "reset_count": reset_count
            })
            with open(self._reject_log_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            self._log_debug(f"RejectStat: Wrote record for {filename} (dur={duration_sec:.1f}s, ref={refused_count}, rst={reset_count})")
        except Exception as e:
            self._log_debug(f"RejectStat: Failed to write stat for {filename}: {e}")

    async def discover_capabilities(self):
        """
        Scans Gemini DOM to find available models and tools.
        Updates config.json with discovery results.
        """
        if not self.is_running:
            return {"status": "error", "message": "Browser not started"}
        
        self._log_debug("Starting discovery scan...")
        results = {"models": [], "tools": [], "current_model": "Unknown"}
        
        try:
            # 1. Discover Models
            # First, check current visible model
            current_model_el = await self._page.query_selector('button[data-test-id="bard-mode-menu-button"] .logo-pill-label-container span')
            if current_model_el:
                results["current_model"] = (await current_model_el.innerText()).split('\n')[0].strip()

            # Trigger model menu
            await self._page.click('button[data-test-id="bard-mode-menu-button"]')
            await asyncio.sleep(1.2)
            
            # Extract models using precise selectors based on current Gemini DOM structure.
            # Strategy:
            #   1. Primary: target [data-test-id^="bard-mode-option-"] buttons — these are
            #      the only real model-selection items and exclude the Upgrade container,
            #      "Thinking level" picker, and the menu title row.
            #   2. Extract text from .mode-title (e.g. "3.1 Flash-Lite") rather than the
            #      full button innerText which would also include .mode-desc ("Fastest answers")
            #      and icon text ("check_circle"), leading to stale/wrong entries like "Fast".
            #   3. Fallback: if the primary selector yields nothing (Google redesign), fall
            #      back to .bard-mode-list-button with the same .mode-title extraction.
            results["models"] = await self._page.evaluate('''() => {
                // Primary: data-test-id prefixed selectors are stable model-button identifiers
                let items = Array.from(document.querySelectorAll('[data-test-id^="bard-mode-option-"]'));

                // Fallback: class-based selector if data-test-id scheme changes
                if (items.length === 0) {
                    items = Array.from(document.querySelectorAll('button.bard-mode-list-button'));
                }

                return items.map(i => {
                    // .mode-title contains only the clean model name, nothing else
                    const titleEl = i.querySelector('.mode-title');
                    return titleEl ? titleEl.innerText.trim() : i.innerText.split('\\n')[0].trim();
                }).filter(t => t.length > 0);
            }''')
            
            # Close menu
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

            # 2. Discover Tools
            # Use the confirmed 'toolbox-drawer-button'
            self._log_debug("Attempting to open Tools drawer...")
            btn = self._page.locator('button.toolbox-drawer-button').first
            if await btn.is_visible():
                await btn.click()
            else:
                # Fallback to text matching if class fails
                await self._page.evaluate('''() => {
                    const btn = Array.from(document.querySelectorAll('button'))
                                     .find(b => b.innerText.includes("Tools"));
                    if (btn) btn.click();
                }''')
            
            # Wait for the drawer
            try:
                await self._page.wait_for_selector('#toolbox-drawer-menu, toolbox-drawer-item', timeout=5000)
            except:
                self._log_debug("Tools drawer did not appear.")

            await asyncio.sleep(0.8)
            
            # Grab tool labels - SCOPE TO #toolbox-drawer-menu to avoid external items
            results["tools"] = await self._page.evaluate('''() => {
                const menu = document.getElementById('toolbox-drawer-menu');
                if (!menu) return [];
                const items = Array.from(menu.querySelectorAll('toolbox-drawer-item'));
                return items.map(i => {
                    const label = i.querySelector('.label.gds-label-l') || i.querySelector('.mdc-list-item__primary-text');
                    if (label) {
                        // Take only the first line to avoid "New" badges etc.
                        return label.innerText.split('\\n')[0].trim();
                    }
                    return "";
                }).filter(t => t.length > 0);
            }''')
            
            # Close by clicking escape
            await self._page.keyboard.press("Escape")
            
            # Persist to config.json using standard utility
            try:
                save_config({
                    "discovery": {
                        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "available_models": results["models"],
                        "available_tools": results["tools"]
                    }
                })
                self._log_debug(f"Discovery results saved. Models: {len(results['models'])}, Tools: {len(results['tools'])}")
            except Exception as e:
                self._log_debug(f"Failed to save discovery: {e}")
            
            return {"status": "success", "data": results}
            
        except Exception as e:
            self._log_debug(f"Discovery failed: {e}")
            return {"status": "error", "message": str(e)}

    async def apply_settings(self, model_name=None, tool_name=None):
        """
        Automates switching to the specified model and/or tool.
        """
        if not self.is_running:
            return {"status": "error", "message": "Browser not started"}
            
        try:
            # 1. Apply Model
            if model_name:
                self._log_debug(f"Applying model: {model_name}")
                await self._page.click('button[data-test-id="bard-mode-menu-button"]')
                await asyncio.sleep(0.8)
                
                await self._page.evaluate(f'''(name) => {{
                    const items = Array.from(document.querySelectorAll('.mat-mdc-menu-item, [role="menuitem"]'));
                    const target = items.find(i => {{
                        const raw = i.innerText.split('\\n')[0].trim().toLowerCase();
                        return raw.startsWith(name.toLowerCase()) || name.toLowerCase().startsWith(raw);
                    }});
                    if (target) target.click();
                }}''', model_name)
                await asyncio.sleep(1.5)

            # 2. Apply Tool
            if tool_name:
                self._log_debug(f"Applying tool: {tool_name}")
                if tool_name.lower() == "default":
                    pass
                else:
                    # Open Tools drawer
                    btn = self._page.locator('button.toolbox-drawer-button').first
                    if await btn.is_visible():
                        await btn.click()
                    else:
                        await self._page.evaluate('''() => {
                            const btn = Array.from(document.querySelectorAll('button'))
                                             .find(b => b.innerText.includes("Tools"));
                            if (btn) btn.click();
                        }''')
                    
                    await asyncio.sleep(1.0)
                    
                    await self._page.evaluate(f'''(name) => {{
                        const menu = document.getElementById('toolbox-drawer-menu');
                        if (!menu) return;
                        const items = Array.from(menu.querySelectorAll('toolbox-drawer-item'));
                        const target = items.find(i => {{
                            const label = i.querySelector('.label.gds-label-l') || i.querySelector('.mdc-list-item__primary-text');
                            return label && label.innerText.toLowerCase().includes(name.toLowerCase());
                        }});
                        if (target) {{
                            const btn = target.querySelector('button');
                            if (btn) btn.click();
                        }}
                    }}''', tool_name)
                    await asyncio.sleep(1.0)

            return {"status": "success"}
        except Exception as e:
            self._log_debug(f"Apply settings failed: {e}")
            return {"status": "error", "message": str(e)}

    async def clear_attachments(self):
        """
        Forcefully removes all file attachments from the Gemini UI.
        Matches all elements with data-test-id="cancel-button".
        """
        if not self.is_running:
            return {"status": "error", "message": "Browser not started"}
            
        try:
            # Locate all cancel buttons
            buttons = await self._page.query_selector_all('button[data-test-id="cancel-button"]')
            removed = 0
            for btn in buttons:
                try:
                    await btn.click()
                    removed += 1
                    await asyncio.sleep(0.5)
                except:
                    continue
            return {"status": "success", "removed": removed}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def dismiss_agreement_popups(self):
        """
        Detects and clicks 'Agree' or 'Got it' buttons in modal dialogs.
        Specifically handles the 'Creating content from images and files' popup.
        """
        if not self.is_running or not self._page:
            return

        # Target buttons with specific text patterns and data-test-ids
        popup_selectors = [
            'button[data-test-id="upload-image-agree-button"]', # Precise MMGen Agree
            "button:has-text('Agree')",
            "button:has-text('I agree')",
            "button:has-text('Got it')",
            "button:has-text('Confirm')",
            "button:has-text('åŒæ„')" # Support for Chinese UI
        ]
        
        try:
            # We use a very short timeout - if it's there, we kill it; if not, we move on.
            for selector in popup_selectors:
                btn = self._page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    self._log_debug(f"Popup detected. Clicking: {selector}")
                    await btn.click()
                    await asyncio.sleep(1.0) # Grace period for animation
                    return True
        except Exception:
            # Silence timeouts - if button isn't found/visible, it's not a failure
            pass
        return False

    async def get_screenshot(self, output_path=None):
        """Captures a screenshot with reference-aligned stability."""
        if not self.is_running:
            raise Exception("Browser Engine not started")
        
        # Stability waits
        await self._page.wait_for_load_state("load")
        await self._page.wait_for_timeout(2000) 
        
        # Fix for white screen: ensure body is present and visible
        try:
            body_visible = await self._page.is_visible("body")
            if not body_visible:
                await self._page.wait_for_selector("body", state="visible", timeout=10000)
        except Exception:
            pass
        
        if not output_path:
            out_dir = "browser_screen_capture"
            output_path = f"{out_dir}/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            os.makedirs(out_dir, exist_ok=True)
            
        # Using full_page=True as seen in reference check_signin.py
        await self._page.screenshot(path=output_path, full_page=True)
        return output_path

    async def get_gem_title(self) -> dict:
        """Extracts the Custom Gem Title from the active Gemini page."""
        if not self.is_running:
            raise Exception("Browser Engine not started")
            
        try:
            # We look for the main heading element that typically holds the Gem name
            # in gemini.google.com/gem/*
            title_text = await self._page.evaluate('''() => {
                const clean = (t) => t ? t.trim().replace(/\\n/g, ' ') : "";
                
                // Try to find Name using the reliable legacy selector
                const nameContainer = document.querySelector('.bot-name-container');
                let name = "";
                if (nameContainer) {
                    const temp = nameContainer.cloneNode(true);
                    const badge = temp.querySelector('bot-experiment-badge, .bot-name-container-animation-box');
                    if (badge) badge.remove();
                    name = clean(temp.innerText);
                    if (name) return name;
                }
                
                // Fallback to document title, stripped of generic "Gemini"
                const docTitle = document.title;
                if (docTitle.includes(" - Gemini") || docTitle === "Gemini") {
                    return docTitle.replace(" - Gemini", "").trim();
                }
                return docTitle;
            }''')
            
            return {"status": "success", "title": title_text or "Unknown"}
        except Exception as e:
            self._log_debug(f"Error extracting gem title: {e}")
            return {"status": "error", "message": str(e)}

    async def get_gem_info(self) -> dict:
        """Extracts the Custom Gem Title AND Description from the active Gemini Gem page."""
        if not self.is_running:
            raise Exception("Browser Engine not started")

        try:
            result = await self._page.evaluate('''() => {
                const clean = (t) => t ? t.trim().replace(/\\n/g, ' ') : "";

                // --- Extract Name (Exact logic from get_gem_title) ---
                const nameContainer = document.querySelector('.bot-name-container');
                let name = "";
                if (nameContainer) {
                    const temp = nameContainer.cloneNode(true);
                    const badge = temp.querySelector('bot-experiment-badge, .bot-name-container-animation-box');
                    if (badge) badge.remove();
                    name = clean(temp.innerText);
                }
                if (!name) {
                    // Fallback to document title, stripped of generic "Gemini"
                    const docTitle = document.title;
                    if (docTitle.includes(" - Gemini") || docTitle === "Gemini") {
                        name = docTitle.replace(" - Gemini", "").trim();
                    } else {
                        name = docTitle;
                    }
                }

                // --- Extract Description ---
                let description = "";
                // Primary: dedicated description container
                const descContainer = document.querySelector('.bot-description-container');
                if (descContainer) {
                    description = clean(descContainer.innerText);
                }
                // Fallback: look for the subtitle/instruction text near the gem header
                if (!description) {
                    const subtitle = document.querySelector('.bot-subtitle, .bot-instruction-text, .gem-description');
                    if (subtitle) {
                        description = clean(subtitle.innerText);
                    }
                }
                // Fallback: aria-label on the main gem card
                if (!description) {
                    const card = document.querySelector('[data-test-id="gem-card"]');
                    if (card) {
                        const label = card.getAttribute('aria-label') || "";
                        if (label && label !== name) {
                            description = clean(label);
                        }
                    }
                }

                return { name: name || "Unknown Gem", description: description || "" };
            }''')

            return {
                "status": "success",
                "name": result.get("name", "Unknown Gem"),
                "description": result.get("description", "")
            }
        except Exception as e:
            self._log_debug(f"Error extracting gem info: {e}")
            return {"status": "error", "message": str(e)}

    async def submit_response(self, text=None, expect_attachments=False):
        """
        1. Injects prompt if provided.
        2. Presses Enter to submit.
        3. Monitors DOM for: Success (image), Quota Exceeded, or Policy Refusal.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        # Get the src of the last image on the page before we submit or monitor
        self._last_seen_src = await self._page.evaluate('''() => {
            const allResps = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
            const responses = allResps.filter(el => !allResps.some(parent => parent !== el && parent.contains(el)));
            if (responses.length === 0) return null;
            const lastResp = responses[responses.length - 1];
            const img = lastResp.querySelector('single-image img, img.generated-image, .generated-image img, .image-container img, img[alt*="generated" i], img[src^="blob:"]');
            return img ? img.src : null;
        }''')
        self._log_debug(f"Success monitor: last seen image src = {self._last_seen_src[:60] if self._last_seen_src else 'None'}")

        if text:
            await self.send_prompt(text)
            # Submit only if we injected text
            await self._page.keyboard.press("Enter")
            self._log_debug("Prompt submitted via Enter. Verifying submission...")
            
            # Brief pause then verify the prompt was actually submitted.
            # In the new Gemini UI (2026-05), Enter may not always trigger submit
            # if the input loses focus or the UI intercepts the keystroke.
            await asyncio.sleep(0.8)
            still_has_text = await self._page.evaluate('''() => {
                const editor = document.querySelector(".ql-editor, div[aria-label='Enter a prompt for Gemini'], div[aria-label='Enter a prompt here']");
                return !!(editor && editor.innerText && editor.innerText.trim().length > 0);
            }''')
            if still_has_text:
                self._log_debug("Text still in input after Enter — falling back to button click.")
                try:
                    # New UI: button[aria-label="Send message"] inside gem-icon-button.submit
                    btn = self._page.locator(
                        'gem-icon-button.submit button[aria-label="Send message"], '
                        'gem-icon-button.send-button button[aria-label="Send message"], '
                        'button[aria-label="Send message"]'
                    ).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        self._log_debug("Fallback button click submitted.")
                    else:
                        self._log_debug("Fallback button not visible — relying on Enter.")
                except Exception as _e:
                    self._log_debug(f"Fallback click failed: {_e}")
            
            # CRITICAL: Dismiss "Creating content from images/files" popup
            await self.dismiss_agreement_popups()
            
            self._log_debug("Monitoring for response...")
        else:
            self._log_debug("Monitoring existing response (no prompt injected)...")

        # Load quota and refusal keywords from external JSON files
        quota_kws = ["quota exceeded", "daily limit", "reached your limit"]
        refused_kws = []
        try:
            from config_utils import load_quota_keywords, load_refused_keywords
            quota_kws = [k.lower() for k in load_quota_keywords()]
            refused_kws = load_refused_keywords()
        except Exception as e:
            self._log_debug(f"Failed to load keyword files: {e}")

        self._log_debug("Waiting for Gemini response...")
        
        has_started_generating = False
        start_gen_time = None
        idle_start_time = None
        last_logged_text = ""

        for _ in range(90): # 180 seconds max
            if self._stop_automation_event.is_set():
                self._log_debug("Stop signal received during monitoring. Bailing out.")
                # Also try to click the browser's stop button to halt generation
                try:
                    await self.stop_response()
                except:
                    pass
                return {"status": "stopped", "message": "Monitoring interrupted by stop signal."}

            data = await self._page.evaluate('''(args) => {
                const bodyText = document.body.innerText.toLowerCase();
                
                // 1. Quota check (text-based - these are standard system phrases)
                for (const kw of args.quota) {
                    if (bodyText.includes(kw)) return { status: "quota_exceeded", text: kw };
                }

                // Utility to check real visibility.
                // Uses getComputedStyle instead of offsetWidth/offsetHeight:
                // offsetWidth/offsetHeight return 0 in minimized windows even for visible
                // elements. getComputedStyle reads the CSS cascade and is always accurate
                // regardless of window state (minimized, headless, off-screen).
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                };

                // 2. Active generation signals (highest priority)
                // stopIcon: truly appears/disappears — DOM presence check is sufficient.
                // progressBar + activeLoadingContainer: Angular Material keeps these elements
                // in the DOM at all times (hidden via CSS). Must use isVisible() to avoid
                // permanently treating every state as "generating".
                // Stop/generating icon detection — covers both old and new Gemini UI.
                // In the new UI (2026-05), icons may use the "lumi-symbols" namespace
                // and may use stop_circle instead of plain stop.
                const stopIcon =
                    document.querySelector('mat-icon[data-mat-icon-name="stop"]') ||
                    document.querySelector('mat-icon[fonticon="stop"]') ||
                    document.querySelector('mat-icon[data-mat-icon-name="stop_circle"]') ||
                    document.querySelector('mat-icon[fonticon="stop_circle"]') ||
                    document.querySelector('mat-icon[data-mat-icon-namespace="lumi-symbols"][data-mat-icon-name="stop"]') ||
                    document.querySelector('mat-icon[data-mat-icon-namespace="lumi-symbols"][data-mat-icon-name="stop_circle"]') ||
                    // Fallback: any mat-icon inside the send-button area whose name is NOT arrow_upward/send
                    // (i.e., it has switched to a stop variant)
                    (() => {
                        const sendBtn = document.querySelector('gem-icon-button.submit mat-icon, gem-icon-button.send-button mat-icon');
                        if (!sendBtn) return null;
                        const n = sendBtn.getAttribute('data-mat-icon-name') || sendBtn.getAttribute('fonticon') || '';
                        // If the icon name is NOT a send variant, assume it's a stop variant
                        return (n && n !== 'arrow_upward' && n !== 'send' && n !== 'send_spark') ? sendBtn : null;
                    })();
                const progressBar = document.querySelector('mat-progress-bar');
                const activeLoadingContainer = document.querySelector('section.processing-state_container--processing');

                if (stopIcon || isVisible(progressBar) || isVisible(activeLoadingContainer)) {
                    // Refusal keywords loaded from refused_keywords.json via args.refused
                    const refusalKws = args.refused || [];
                    let genText = "";
                    if (activeLoadingContainer) {
                        // 2026-06 fix: Gemini can simultaneously show the processing spinner
                        // AND a refusal message in structured-content-container.processing-state-visible.
                        // Check that element first — if it contains refusal text the response is
                        // already decided even though the spinner is still running.
                        const processingVisible = document.querySelector('structured-content-container.processing-state-visible');
                        if (processingVisible) {
                            const pvText = (processingVisible.querySelector('.model-response-text') || processingVisible.querySelector('message-content') || processingVisible).innerText.trim();
                            if (pvText.length > 0 && refusalKws.some(kw => pvText.toLowerCase().includes(kw.toLowerCase()))) {
                                return { status: "refused", text: pvText };
                            }
                        }
                        // Extract text from the active loading container's specific spans
                        const labelSpan = activeLoadingContainer.querySelector('.processing-state_ext-name_label span');
                        const placeholderSpan = activeLoadingContainer.querySelector('.processing-state_ext-name_placeholder span');

                        if (labelSpan && labelSpan.textContent) {
                            genText = labelSpan.textContent.trim();
                        } else if (placeholderSpan && placeholderSpan.textContent) {
                            genText = placeholderSpan.textContent.trim();
                        } else {
                            const _cl = activeLoadingContainer.cloneNode(true);
                            _cl.querySelectorAll('.cdk-visually-hidden,[aria-hidden="true"]').forEach(function(e){e.remove();});
                            genText = _cl.textContent.trim();
                        }
                    } else {
                        // Extract regular streaming generation text
                        let allResps = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
                        const responses = allResps.filter(el => !allResps.some(parent => parent !== el && parent.contains(el)));
                        const lastResp = responses.length > 0 ? responses[responses.length - 1] : null;
                        if (lastResp) {
                            const contentNode = lastResp.querySelector('.model-response-text') || lastResp.querySelector('.message-content') || lastResp;
                            const _cl2 = contentNode.cloneNode(true);
                            _cl2.querySelectorAll('.cdk-visually-hidden,[aria-hidden="true"]').forEach(function(e){e.remove();});
                            genText = _cl2.textContent.trim();
                            // Detect refusal keywords in the current streaming response immediately,
                            // without requiring .response-footer.complete. Gemini streams the refusal
                            // text while the stop-icon is still visible; by the time sendReady fires
                            // the DOM may have restructured and respText becomes empty, causing the
                            // refusal to be misclassified as idle/error.
                            if (genText.length > 0 && refusalKws.some(kw => genText.toLowerCase().includes(kw.toLowerCase()))) {
                                return { status: "refused", text: genText };
                            }
                        }
                    }
                    return { status: "generating", text: genText };
                }

                // 3. Idle state (send button ready)
                // ROOT CAUSE: isVisible() uses offsetWidth/offsetHeight which can be 0
                // in a minimized window even when the element is logically "visible".
                //
                // NEW STRATEGY: Check DOM presence + semantic attributes instead.
                //
                // The outer container has data-test-id="send-button-container" and
                // receives the class "visible" when the send button is active/ready.
                // gem-icon-button has aria-disabled="false" when interactive.
                // Neither check requires layout dimensions — works in minimized windows.
                //
                // 2026-05 UPDATE: Gemini redesigned the submit button icon from "send"
                // to "arrow_upward" under the "lumi-symbols" icon namespace.
                //
                // CRITICAL FIX: In the new Gemini UI, the send-button-container div
                // keeps the "visible" class at ALL times (even while Gemini is generating).
                // We MUST require the "arrow_upward" (send-mode) icon to be present in
                // the button, to distinguish idle-ready from active-generating state.
                // Without this check, sendReady fires immediately after submission,
                // before Gemini even starts generating, causing false reset detections.
                const _sendModeSelectors = [
                    'mat-icon[data-mat-icon-name="arrow_upward"]',
                    'mat-icon[fonticon="arrow_upward"]',
                    'mat-icon[data-mat-icon-name="send"]',
                    'mat-icon[fonticon="send"]',
                    'mat-icon[data-mat-icon-name="send_spark"]',
                    'mat-icon[fonticon="send_spark"]',
                ];
                const _inSendMode = _sendModeSelectors.some(s => !!document.querySelector(s));
                const sendReady = _inSendMode && !!(
                    document.querySelector('[data-test-id="send-button-container"].visible') ||
                    document.querySelector('gem-icon-button.send-button[aria-disabled="false"]') ||
                    document.querySelector('gem-icon-button.submit[aria-disabled="false"]') ||
                    document.querySelector('button[aria-label="Send message"]:not([disabled])') ||
                    document.querySelector('button[aria-label*="Send" i]:not([disabled])')
                );

                if (sendReady) {
                    let allResps = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
                    const responses = allResps.filter(el => !allResps.some(parent => parent !== el && parent.contains(el)));
                    
                    // Metadata for reset detection
                    const editor = document.querySelector('.ql-editor');
                    const inputEmpty = !editor || !editor.innerText.trim();
                    const attachmentCount = document.querySelectorAll('button[data-test-id="cancel-button"]').length;

                    // No conversation history visible - Gemini was reset or is a fresh session.
                    if (responses.length === 0) {
                        return { 
                            status: "reset", 
                            text: "", 
                            inputEmpty: inputEmpty,
                            attachmentCount: attachmentCount
                        };
                    }
                    
                    const lastResp = responses[responses.length - 1];
                    const imgEl = lastResp.querySelector('single-image img, img.generated-image, .generated-image img, .image-container img, img[alt*="generated" i], img[src^="blob:"]');
                    const hasImg = !!imgEl && imgEl.src && imgEl.src !== 'about:blank' && (!args.last_seen_src || imgEl.src !== args.last_seen_src);
                    
                    // Filter out the 'XXX said' header portion
                    const contentNode = lastResp.querySelector('.model-response-text') || lastResp.querySelector('.message-content') || lastResp;
                    const respText = contentNode.innerText.trim();

                    if (hasImg) return { status: "success", text: respText };

                    // Structural and Textual refusal detection:
                    // Gemini refused if the response is "complete" (has the complete footer class)
                    // and it has text content but NO image, OR if it matches known refusal text.
                    const completeFooter = lastResp.querySelector('.response-footer.complete');
                    // Refusal keywords loaded from refused_keywords.json via args.refused
                    const refusalKws = args.refused || [];
                    const isTextRefusal = refusalKws.some(kw => respText.toLowerCase().includes(kw.toLowerCase()));

                    if ((completeFooter || isTextRefusal) && respText.length > 0) {
                        return { status: "refused", text: respText };
                    }

                    // Fallback: respText may be empty due to DOM restructuring after generation.
                    // Check body text so a refusal is never silently misclassified as idle/error.
                    if (respText.length === 0 && refusalKws.length > 0) {
                        const bodyText = document.body.innerText;
                        const bodyRefusal = refusalKws.some(kw => bodyText.toLowerCase().includes(kw.toLowerCase()));
                        if (bodyRefusal) {
                            const fallbackText = bodyText.slice(0, 500);
                            return { status: "refused", text: fallbackText };
                        }
                    }

                    // Otherwise treat as stopped or transitional
                    return { status: "idle_no_img", text: respText };
                }

                return { status: "loading", text: "" };
            }''', {"quota": quota_kws, "refused": refused_kws, "last_seen_src": self._last_seen_src})

            status = data['status']
            resp_text = data.get('text', '') or ''
            current_time = time.time()

            # If generating but JS returned no text, try to read the loading label
            # via Playwright's native locator (more reliable than evaluate for this)
            if status == "generating" and not resp_text:
                try:
                    import re as _re
                    locator = self._page.locator('section.processing-state_container--processing').first
                    if await locator.count() > 0:
                        jslog_attr = await locator.get_attribute('jslog') or ""
                        m = _re.search(r'\["([^"]+)",0\]', jslog_attr)
                        if m:
                            resp_text = m.group(1)
                except Exception:
                    pass

            # Log any new status text (throttled to avoid noise)
            if resp_text and resp_text != last_logged_text and len(resp_text) > 2:
                flat = " ".join(resp_text.replace('\n', ' ').split())
                self._log_debug(f"Gemini: \"{flat[:200]}\"")
                last_logged_text = resp_text

            if status == "generating":
                if not has_started_generating:
                    has_started_generating = True
                    start_gen_time = current_time
            
            if status == "success":
                self._log_debug("Response successful: Image detected.")
                return {"status": "success", "message": "Image generated successfully."}
            elif status == "quota_exceeded":
                self._log_debug(f"Response failed: Quota exceeded.")
                return {"status": "error", "message": "Quota exceeded. Please wait before retrying."}
            elif status == "refused":
                flat_text = " ".join(resp_text.replace('\n', ' ').split())
                self._log_debug(f"Response failed (Refused): {flat_text[:300]}")
                return {"status": "refused", "message": f"Gemini refused: {flat_text[:300]}"}
            elif status == "idle_no_img":
                if not idle_start_time:
                    idle_start_time = current_time
                    
                # Only report 'stopped' after sustained generation (4s grace period)
                if has_started_generating and start_gen_time and (current_time - start_gen_time > 4.0):
                    self._log_debug(f"Idle detected after {current_time - start_gen_time:.1f}s of generation.")
                    return {"status": "error", "message": "Stopped or failed to generate image."}
                elif (current_time - idle_start_time) > 8.0:
                    self._log_debug("Sustained idle detected without image. Treating as refusal.")
                    flat_text = " ".join(resp_text.replace('\n', ' ').split())
                    return {"status": "refused", "message": f"Gemini refused (Sustained Idle): {flat_text[:300]}"}
                else:
                    self._log_debug("Idle detected - in grace period, continuing to monitor...")
            elif status == "reset":
                # Gemini reset to initial state (no conversation history)
                if has_started_generating:
                    # Was generating but now page is empty - definitely an unexpected reset
                    self._log_debug("Gemini page was unexpectedly reset during generation.")
                    return {"status": "error", "message": "Gemini was reset unexpectedly."}
                
                # If we are NOT injecting a new prompt (monitoring a Redo) and we see a reset,
                # it means the Redo triggered a soft-reset. Return immediately to trigger recovery.
                if not text:
                    self._log_debug("Gemini reset detected during Redo monitoring. Triggering recovery...")
                    return {"status": "reset", "message": "Gemini reset during Redo."}
                
                # Case: Initial Prompt Submission (text has value)
                input_empty = data.get('inputEmpty', True)
                attachment_count = data.get('attachmentCount', 0)

                # Signal 1: Prompt is still in the input box (Submission failed or page reset)
                # Note: We give it a few seconds grace period to clear
                if not input_empty:
                    if not hasattr(self, '_reset_watchdog_start') or self._reset_watchdog_start is None:
                        self._reset_watchdog_start = current_time
                    elif (current_time - self._reset_watchdog_start) > 6.0:
                        self._log_debug("Prompt still remains in input box after 6s. Submission likely failed.")
                        self._reset_watchdog_start = None
                        return {"status": "reset", "message": "Prompt remains in input box."}
                
                # Signal 2: Missing attachments (Env reset)
                # If we expected attachments but they are gone, it's a reset.
                if expect_attachments and attachment_count == 0:
                    self._log_debug("Expected attachments disappeared. Env reset detected.")
                    return {"status": "reset", "message": "Attachments missing during monitoring."}
                
                self._log_debug("Waiting for conversation to appear...")
            else:
                # Any other status (generating, success, etc.) clears the watchdog
                self._reset_watchdog_start = None

            
            await asyncio.sleep(2)

        return {"status": "timeout", "message": "Timed out waiting for image response."}


    async def send_chat(self, prompt: str) -> dict:
        """Sends a text prompt to Gemini and waits for the text reply."""
        if not self.is_running:
            raise Exception("Browser Engine not started")

        # Record how many model responses already exist before we submit
        existing_count = await self._page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
            return all.filter(el => !all.some(p => p !== el && p.contains(el))).length;
        }''')
        self._log_debug(f"send_chat: existing responses before submit = {existing_count}")

        await self.send_prompt(prompt)
        await self._page.keyboard.press("Enter")
        self._log_debug("send_chat: prompt submitted.")

        await asyncio.sleep(0.8)
        still_has_text = await self._page.evaluate('''() => {
            const editor = document.querySelector(".ql-editor, div[aria-label='Enter a prompt for Gemini'], div[aria-label='Enter a prompt here']");
            return !!(editor && editor.innerText && editor.innerText.trim().length > 0);
        }''')
        if still_has_text:
            try:
                btn = self._page.locator(
                    'gem-icon-button.submit button[aria-label="Send message"], '
                    'gem-icon-button.send-button button[aria-label="Send message"], '
                    'button[aria-label="Send message"]'
                ).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    self._log_debug("send_chat: fallback button click sent.")
            except Exception:
                pass

        # Wait for a NEW response to appear and its content to stabilize.
        # Strategy: look for response count > existing_count, then wait until
        # .response-footer.complete is present on the last response OR the
        # text has been unchanged for 3 consecutive 2-second polls (6s stable).
        last_text = ""
        stable_count = 0
        STABLE_NEEDED = 3

        for i in range(120):  # 240s max
            data = await self._page.evaluate(f'''() => {{
                const all = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
                const responses = all.filter(el => !all.some(p => p !== el && p.contains(el)));

                if (responses.length <= {existing_count}) {{
                    return {{ status: 'waiting', text: '' }};
                }}

                const lastResp = responses[responses.length - 1];
                const contentNode = lastResp.querySelector('.model-response-text') ||
                                    lastResp.querySelector('.message-content') || lastResp;
                const cl = contentNode.cloneNode(true);
                cl.querySelectorAll('.cdk-visually-hidden,[aria-hidden="true"]').forEach(function(e){{ e.remove(); }});
                const text = cl.innerText.trim();

                const isComplete = !!lastResp.querySelector('.response-footer.complete');
                return {{ status: 'has_response', text: text, complete: isComplete }};
            }}''')

            status = data.get("status")
            if status == "waiting":
                if i % 5 == 0:
                    self._log_debug(f"send_chat: waiting for new response... (iter {i})")
            elif status == "has_response":
                text = data.get("text", "")
                is_complete = data.get("complete", False)

                if is_complete and text:
                    self._log_debug(f"send_chat: complete footer detected ({len(text)} chars).")
                    return {"status": "success", "text": text}

                if text and text == last_text:
                    stable_count += 1
                    self._log_debug(f"send_chat: text stable {stable_count}/{STABLE_NEEDED} ({len(text)} chars)")
                    if stable_count >= STABLE_NEEDED:
                        return {"status": "success", "text": text}
                else:
                    stable_count = 0

                last_text = text

            await asyncio.sleep(2)

        return {"status": "timeout", "message": "Timed out waiting for text response."}

    async def redo_response(self):
        """
        Triggers Gemini's redo (regenerate) action.
        Handles both the single button redo and the menu-based 'Try again' redo.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        # 1. Scroll to reveal Redo if hidden
        await self._page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await asyncio.sleep(0.5)

        # 2. Click redo/refresh button (target the latest turn's button)
        result = await self._page.evaluate('''async () => {
            const findLastBtn = (sel) => {
                const btns = Array.from(document.querySelectorAll(sel));
                return btns.length > 0 ? btns[btns.length - 1] : null;
            };
            const findByText = (txt) => Array.from(document.querySelectorAll('.menu-text, span, button'))
                                            .reverse()
                                            .find(b => b.innerText.toLowerCase().includes(txt));
            
            const allIcons = Array.from(document.querySelectorAll('mat-icon[data-mat-icon-name="refresh"], mat-icon[fonticon="refresh"]'));
            const refreshIcon = allIcons.length > 0 ? allIcons[allIcons.length - 1] : null;
            
            let redoBtn = findLastBtn('regenerate-button button') || 
                          findLastBtn('button[aria-label="Redo"]');
            
            if (!redoBtn && refreshIcon) {
                redoBtn = refreshIcon.closest('button') || refreshIcon.closest('[role="button"]') || refreshIcon.parentElement;
            }

            if (redoBtn) {
                redoBtn.scrollIntoView({behavior: "instant", block: "center"});
                redoBtn.click();
                
                // Wait briefly for sub-menu if it exists
                await new Promise(r => setTimeout(r, 1000));
                
                let tryAgain = findByText("try again");
                if (tryAgain) {
                    tryAgain.click();
                    return "REDO_WITH_TRY_AGAIN";
                }
                return "REDO_CLICKED";
            }
            return "NOT_FOUND";
        }''')

        if result != "NOT_FOUND":
            self._log_debug(f"Redo triggered: {result}")
            # Ensure the UI has transitioned to 'generating' before returning
            for _ in range(15):
                await asyncio.sleep(0.5)
                is_gen = await self._page.evaluate('''() => {
                    return !!document.querySelector('mat-progress-bar') || 
                           !!document.querySelector('mat-icon[data-mat-icon-name="stop"]') || 
                           !!document.querySelector('section.processing-state_container--processing');
                }''')
                if is_gen:
                    break
            return {"status": "success", "message": f"Redo action sent ({result})."}
        else:
            self._log_debug("Redo button not found.")
            return {"status": "error", "message": "Redo button not found on page."}

    async def download_images(self, save_dir, naming_cfg, extra_meta=None):
        """
        Downloads images from the last response and enriches metadata.
        naming_cfg: {prefix, padding, start}
        extra_meta: {prompt, url, upload_path}
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        os.makedirs(save_dir, exist_ok=True)
        
        # 1. Identify images in last response using top-level filtering (matches success monitor)
        last_resp_handle = await self._page.evaluate_handle('''() => {
            const allResps = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
            const responses = allResps.filter(el => !allResps.some(parent => parent !== el && parent.contains(el)));
            return responses.length > 0 ? responses[responses.length - 1] : null;
        }''')
        last_response = last_resp_handle.as_element()
        if not last_response:
            return {"status": "error", "message": "No response found to download from."}

        valid_imgs = []
        for retry in range(20):  # 20 * 0.5s = 10s max
            imgs = await last_response.query_selector_all('single-image img, img.generated-image, .generated-image img, .image-container img, img[alt*="generated" i], img[src^="blob:"]')
            if not imgs:
                imgs = await last_response.query_selector_all('img')
            valid_imgs = []
            seen_positions = []
            seen_srcs = set()
            for img in imgs:
                # Use evaluate to get complete, naturalWidth and boundingClientRect info
                # so it works robustly in minimized/background/headless states
                img_info = await img.evaluate('''el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        width: rect.width, height: rect.height,
                        x: rect.left + window.scrollX, y: rect.top + window.scrollY,
                        complete: el.complete, naturalW: el.naturalWidth,
                        visible: el.offsetWidth > 0 || el.offsetHeight > 0 || (el.complete && el.naturalWidth > 50)
                    };
                }''')
                
                if not img_info.get("visible"):
                    continue
                
                # Check dimensions
                w = img_info.get("width", 0)
                natural_w = img_info.get("naturalW", 0)
                
                src = await img.get_attribute('src')
                src = src.strip() if src else ""
                
                # We want large images (e.g. width > 250 or naturalWidth > 250)
                # and must ensure a valid src is present and the image has loaded (naturalWidth > 50)
                if src and (natural_w > 250 or (w > 250 and natural_w > 50)):
                    cx = img_info.get("x", 0) + w / 2
                    cy = img_info.get("y", 0) + img_info.get("height", 0) / 2
                    
                    norm_src = src
                    if "googleusercontent.com" in src and "=" in src:
                        norm_src = src.split("=")[0]
                    
                    is_dup = False
                    if norm_src and not norm_src.startswith("data:"):
                        if norm_src in seen_srcs:
                            is_dup = True
                    
                    if not is_dup:
                        for sx, sy, scx, scy in seen_positions:
                            if (abs(img_info.get("x", 0) - sx) < 15 and abs(img_info.get("y", 0) - sy) < 15) or \
                               (abs(cx - scx) < 15 and abs(cy - scy) < 15):
                                is_dup = True
                                break
                    
                    self._log_debug(f"IMG-DETECTION: src={src[:60]}, w={w:.1f}, natural_w={natural_w}, dup={is_dup}")
                    
                    if not is_dup:
                        valid_imgs.append(img)
                        seen_positions.append((img_info.get("x", 0), img_info.get("y", 0), cx, cy))
                        if norm_src and not norm_src.startswith("data:"):
                            seen_srcs.add(norm_src)
            
            if valid_imgs:
                break
            await asyncio.sleep(0.5)

        if not valid_imgs:
            return {"status": "ignored", "message": "No valid large images found."}

        self._log_debug(f"Downloading {len(valid_imgs)} images...")
        
        prefix = naming_cfg.get("prefix", "")
        padding = naming_cfg.get("padding", 2)
        start_idx = naming_cfg.get("start", 1)
        
        cfg = load_config()
        if cfg.get("track_last_file_num", False):
            import re
            max_num = -1
            prefix_escaped = re.escape(prefix)
            pattern = re.compile(rf"^{prefix_escaped}(\d+)\.[a-zA-Z0-9]+$", re.IGNORECASE)
            try:
                for filename in os.listdir(save_dir):
                    match = pattern.match(filename)
                    if match:
                        try:
                            num = int(match.group(1))
                            if num > max_num:
                                max_num = num
                        except ValueError:
                            pass
            except Exception as scan_err:
                self._log_debug(f"Error scanning save_dir: {scan_err}")
            
            if max_num != -1:
                self._log_debug(f"Auto-track: found max number {max_num} in folder. Next number: {max_num + 1}")
                start_idx = max_num + 1
            else:
                self._log_debug(f"Auto-track: no existing files found. Using start number: {start_idx}")
        
        from PIL import Image
        import io
        import hashlib
        from processing_utils import save_with_metadata
        seen_hashes = set()

        def get_image_ahash(path):
            try:
                with Image.open(path) as img:
                    small = img.resize((8, 8), Image.Resampling.BILINEAR).convert('L')
                    try:
                        pixels = list(small.getdata())
                    except Exception:
                        pixels = list(small.get_flattened_data())
                    avg = sum(pixels) / 64.0
                    bits = ''.join(['1' if p >= avg else '0' for p in pixels])
                    return int(bits, 2)
            except Exception as e:
                self._log_debug(f"Failed to calculate aHash: {e}")
                return None

        dl_count = 0
        saved_paths = []

        for idx in range(len(valid_imgs)):
            try:
                # ── STEP 1: Wait for image to fully load & render ──
                img_ready = False
                img = None
                img_info = {}
                for _load_wait in range(20):  # 20 * 0.5s = 10s max
                    # Dynamically re-locate the image to avoid stale element (Element is not attached to the DOM)
                    try:
                        last_resp_handle = await self._page.evaluate_handle('''() => {
                            const allResps = Array.from(document.querySelectorAll('model-response, structured-content-container.model-response-text, message-content'));
                            const responses = allResps.filter(el => !allResps.some(parent => parent !== el && parent.contains(el)));
                            return responses.length > 0 ? responses[responses.length - 1] : null;
                        }''')
                        last_response = last_resp_handle.as_element()
                        if last_response:
                            imgs = await last_response.query_selector_all('single-image img, img.generated-image, .generated-image img, .image-container img, img[alt*="generated" i], img[src^="blob:"]')
                            if not imgs:
                                imgs = await last_response.query_selector_all('img')
                            if idx < len(imgs):
                                img = imgs[idx]
                    except Exception as re_locate_err:
                        self._log_debug(f"DL-DIAG: Error re-locating image at index {idx}: {re_locate_err}")

                    if img is None:
                        await asyncio.sleep(0.5)
                        continue

                    img_info = await img.evaluate('''el => {
                        const rect = el.getBoundingClientRect();
                        return {
                            width: rect.width, height: rect.height,
                            complete: el.complete, naturalW: el.naturalWidth
                        };
                    }''')
                    # Support minimized/background windows where getBoundingClientRect() returns 0, but complete and naturalW are valid
                    if (img_info.get("height", 0) > 50 and img_info.get("width", 0) > 50) or (img_info.get("complete") and img_info.get("naturalW", 0) > 50):
                        img_ready = True
                        break
                    await asyncio.sleep(0.5)
                
                if not img_ready or img is None:
                    h_val = img_info.get('height') if img else None
                    w_val = img_info.get('width') if img else None
                    comp_val = img_info.get('complete') if img else None
                    nat_val = img_info.get('naturalW') if img else None
                    self._log_debug(f"DL-DIAG: Image not ready after 10s (h={h_val}, w={w_val}, complete={comp_val}, naturalW={nat_val}). Skipping.")
                    continue
                
                # ── STEP 1b: Scroll & Viewport Verification ──
                await img.evaluate('el => el.scrollIntoView({behavior: "instant", block: "center"})')
                await asyncio.sleep(0.5)
                
                viewport_info = await img.evaluate('''el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        inViewport: rect.width > 0 && rect.height > 0 &&
                                    rect.top >= 0 && rect.left >= 0 && 
                                    rect.bottom <= window.innerHeight && rect.right <= window.innerWidth,
                        imgRect: {t: Math.round(rect.top), l: Math.round(rect.left), 
                                  b: Math.round(rect.bottom), r: Math.round(rect.right)},
                        windowSize: {w: window.innerWidth, h: window.innerHeight}
                    };
                }''')
                self._log_debug(f"DL-DIAG: viewport={viewport_info}")
                
                if not viewport_info.get("inViewport"):
                    await self._page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(0.3)
                    await img.evaluate('el => el.scrollIntoView({behavior: "instant", block: "center"})')
                    await asyncio.sleep(0.5)
                
                # ── STEP 2: Click image to open lightbox/dialog ──
                await img.click(force=True)
                await asyncio.sleep(1.5)
                
                # ── STEP 3: Diagnose what dialog opened ──
                dialog_diag = await self._page.evaluate('''(imgEl) => {
                    const result = {dialogFound: false, dialogType: "none", dlBtnFound: false, 
                                    dlBtnInfo: null, allButtons: [], dialogClasses: ""};
                    
                    // Check for mat-dialog (Pro editing dialog or standard preview)
                    const dialog = document.querySelector('mat-dialog-container');
                    if (dialog) {
                        result.dialogFound = true;
                        const content = dialog.querySelector('mat-dialog-content');
                        result.dialogClasses = content ? content.className : dialog.className;
                        
                        if (content && content.className.includes('trusted-image-dialog')) {
                            result.dialogType = "pro_editing";
                        } else {
                            result.dialogType = "standard";
                        }
                        
                        // Check for dialog image (high-res)
                        const dialogImg = dialog.querySelector('img[data-test-id="trusted-image"], img.generated-image');
                        if (dialogImg) {
                            result.dialogImgSrc = dialogImg.src ? dialogImg.src.substring(0, 80) : "none";
                        }
                    }
                    
                    // Check for cdk-overlay (Angular Material overlay)
                    const overlay = document.querySelector('.cdk-overlay-container .cdk-overlay-pane');
                    if (overlay && !result.dialogFound) {
                        result.dialogFound = true;
                        result.dialogType = "cdk_overlay";
                        result.dialogClasses = overlay.className;
                    }
                    
                    // Scope search for download buttons
                    const searchScope = dialog || overlay || (imgEl ? imgEl.closest('single-image') || imgEl.closest('.image-container') || imgEl.parentElement : document);
                    
                    // Scan mat-icon elements inside the scoped container
                    const matIcons = Array.from(searchScope.querySelectorAll('mat-icon'));
                    const dlIcons = matIcons.filter(i => {
                        const name = i.getAttribute('data-mat-icon-name') || '';
                        const font = i.getAttribute('fonticon') || '';
                        return name === 'download' || font === 'download';
                    });
                    
                    if (dlIcons.length > 0) {
                        result.dlBtnFound = true;
                        const icon = dlIcons[0];
                        const parentBtn = icon.closest('button');
                        result.dlBtnInfo = {
                            tagName: parentBtn ? 'button' : icon.tagName,
                            visible: parentBtn ? (parentBtn.offsetParent !== null) : (icon.offsetParent !== null),
                            disabled: parentBtn ? parentBtn.disabled : false,
                            ariaLabel: parentBtn ? (parentBtn.ariaLabel || '') : '',
                            iconName: icon.getAttribute('data-mat-icon-name') || '',
                            fonticon: icon.getAttribute('fonticon') || '',
                            classes: parentBtn ? parentBtn.className.substring(0, 120) : ''
                        };
                    }
                    
                    // Fallback: scan buttons for Download text in scoped container
                    if (!result.dlBtnFound) {
                        const textBtn = Array.from(searchScope.querySelectorAll('button'))
                            .find(x => (x.ariaLabel || '').toLowerCase().includes('download') ||
                                       (x.title || '').toLowerCase().includes('download') ||
                                       x.innerText.toLowerCase().includes('download'));
                        if (textBtn) {
                            result.dlBtnFound = true;
                            result.dlBtnInfo = {
                                tagName: 'button', visible: textBtn.offsetParent !== null,
                                disabled: textBtn.disabled,
                                ariaLabel: textBtn.ariaLabel || '', 
                                innerText: textBtn.innerText.substring(0, 50),
                                classes: textBtn.className.substring(0, 120)
                            };
                        }
                    }
                    
                    // Collect summary of ALL buttons in search scope for debugging
                    const btns = Array.from(searchScope.querySelectorAll('button'));
                    result.allButtons = btns.slice(0, 15).map(b => ({
                        ariaLabel: (b.ariaLabel || '').substring(0, 40),
                        text: b.innerText.substring(0, 30).replace(/\\n/g, ' '),
                        icon: (() => {
                            const mi = b.querySelector('mat-icon');
                            if (!mi) return '';
                            return mi.getAttribute('data-mat-icon-name') || 
                                   mi.getAttribute('fonticon') || 
                                   mi.innerText.substring(0, 20);
                        })(),
                        visible: b.offsetParent !== null,
                        disabled: b.disabled
                    }));
                    
                    return result;
                }''', img)
                self._log_debug(f"DL-DIAG: dialog={dialog_diag}")
                
                # ── STEP 4: Wait for Download button if not yet visible ──
                dl_btn_found = dialog_diag.get("dlBtnFound", False)
                
                if not dl_btn_found:
                    # Poll for the button (dialog might still be animating)
                    for _wait in range(12):  # 12 * 0.5s = 6s max
                        dl_btn_found = await self._page.evaluate('''(imgEl) => {
                            const dialog = document.querySelector('mat-dialog-container');
                            const overlay = document.querySelector('.cdk-overlay-container .cdk-overlay-pane');
                            const searchScope = dialog || overlay || (imgEl ? imgEl.closest('single-image') || imgEl.closest('.image-container') || imgEl.parentElement : document);
                            
                            const matIcon = searchScope.querySelector('mat-icon[data-mat-icon-name="download"]');
                            if (matIcon) return true;
                            const fontIcon = searchScope.querySelector('mat-icon[fonticon="download"]');
                            if (fontIcon) return true;
                            const btn = Array.from(searchScope.querySelectorAll('button'))
                                         .find(x => (x.ariaLabel || '').toLowerCase().includes('download') ||
                                                   (x.title || '').toLowerCase().includes('download') ||
                                                   x.innerText.toLowerCase().includes('download'));
                            if (btn) return true;
                            return false;
                        }''', img)
                        if dl_btn_found:
                            self._log_debug(f"DL-DIAG: button appeared after {(_wait+1)*0.5:.1f}s polling")
                            break
                        await asyncio.sleep(0.5)

                # ── STEP 5: Execute download (button path or blob fallback) ──
                if not dl_btn_found:
                    self._log_debug("DL-DIAG: No download button found after polling. Using canvas extraction.")
                    # Wait for the dialog's high-res image to fully load before drawing to canvas.
                    # The dialog img (trusted-image) loads asynchronously after the dialog opens.
                    # We poll until naturalWidth > 512 (full-res), up to 10s.
                    for _hires_wait in range(20):  # 20 * 0.5s = 10s max
                        hires_nw = await self._page.evaluate('''() => {
                            const dialogImg = document.querySelector('img[data-test-id="trusted-image"]') ||
                                              document.querySelector('mat-dialog-container img.generated-image') ||
                                              document.querySelector('mat-dialog-container img');
                            return dialogImg ? dialogImg.naturalWidth : 0;
                        }''')
                        if hires_nw > 512:
                            self._log_debug(f"DL-DIAG: Dialog hi-res image ready (naturalWidth={hires_nw}) after {_hires_wait * 0.5:.1f}s.")
                            break
                        self._log_debug(f"DL-DIAG: Waiting for dialog hi-res image... (naturalWidth={hires_nw}, attempt {_hires_wait+1}/20)")
                        await asyncio.sleep(0.5)
                    else:
                        self._log_debug("DL-DIAG: Dialog hi-res image did not load after 10s. Aborting canvas extraction.")
                        await self._page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                        continue

                    # Extract image pixels directly via canvas while dialog is still open
                    img_bytes = await self._page.evaluate('''async (imgEl) => {
                        try {
                            const dialogImg = document.querySelector('img[data-test-id="trusted-image"]') ||
                                              document.querySelector('mat-dialog-container img.generated-image') ||
                                              document.querySelector('mat-dialog-container img') ||
                                              imgEl;
                            if (!dialogImg || !dialogImg.naturalWidth) return null;
                            
                            const canvas = document.createElement('canvas');
                            canvas.width = dialogImg.naturalWidth;
                            canvas.height = dialogImg.naturalHeight;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(dialogImg, 0, 0);
                            
                            const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
                            if (!blob) return null;
                            const buf = await blob.arrayBuffer();
                            return Array.from(new Uint8Array(buf));
                        } catch(e) {
                            return null;
                        }
                    }''', img)
                    
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
                    
                    if img_bytes:
                        while True:
                            save_name = f"{prefix}{str(start_idx).zfill(padding)}.png"
                            save_path = os.path.join(save_dir, save_name)
                            if not os.path.exists(save_path):
                                break
                            start_idx += 1
                        
                        raw = bytes(img_bytes)
                        self._log_debug(f"DL-DIAG: Canvas extracted {len(raw)} bytes ({len(raw)/1024:.0f}KB)")
                        with Image.open(io.BytesIO(raw)) as pil_img:
                            save_with_metadata(pil_img, pil_img, save_path, extra_meta=extra_meta)
                        
                        # Size validation: file must be >= 1MB, else the dialog hi-res img wasn't ready.
                        file_sz = os.path.getsize(save_path)
                        if file_sz < 1024 * 1024:
                            self._log_debug(f"DL-DIAG: Canvas result still too small ({file_sz/1024:.1f}KB < 1MB). Low-res placeholder captured. Discarding.")
                            try:
                                os.remove(save_path)
                            except Exception as rm_err:
                                self._log_debug(f"Failed to delete small file: {rm_err}")
                            continue
                        
                        # Check PIL average hash to prevent duplicate saving (robust to scaling/canvas rescue)
                        is_pixel_dup = False
                        new_ahash = get_image_ahash(save_path)
                        if new_ahash is not None:
                            for old_ahash in seen_hashes:
                                distance = bin(new_ahash ^ old_ahash).count('1')
                                if distance <= 3:
                                    self._log_debug(f"Duplicate image content detected (aHash={hex(new_ahash)}, distance={distance}). Deleting duplicate: {save_name}")
                                    try:
                                        os.remove(save_path)
                                    except Exception as rm_err:
                                        self._log_debug(f"Failed to remove duplicate file: {rm_err}")
                                    is_pixel_dup = True
                                    break
                            if not is_pixel_dup:
                                seen_hashes.add(new_ahash)
                        
                        if is_pixel_dup:
                            continue
                            
                        saved_paths.append(save_path)
                        start_idx += 1
                        dl_count += 1
                        self._log_debug(f"Saved (canvas fallback): {save_name}")
                        continue
                    else:
                        self._log_debug("DL-DIAG: Canvas extraction returned null. Skipping.")
                        await self._page.keyboard.press("Escape")
                        continue
                
                # ── Button-based download path using Playwright expect_download ──────────────
                try:
                    self._log_debug("DL-DIAG: Triggering download and waiting for Playwright download event...")
                    async with self._page.expect_download(timeout=90000) as download_info:
                        btn_clicked = await self._page.evaluate('''() => {
                            const dialog = document.querySelector('mat-dialog-container');
                            const overlay = document.querySelector('.cdk-overlay-container .cdk-overlay-pane');
                            const scope = dialog || overlay || document;

                            // Most specific: aria-label="Download full-sized image"
                            let btn = scope.querySelector('button[aria-label="Download full-sized image"]');
                            if (!btn) {
                                // Fallback: closest button to a download mat-icon
                                const icon = scope.querySelector(
                                    'mat-icon[data-mat-icon-name="download"], mat-icon[fonticon="download"]');
                                if (icon) btn = icon.closest('button') || icon;
                            }
                            if (!btn) {
                                btn = Array.from(scope.querySelectorAll('button'))
                                    .find(x => (x.ariaLabel || '').toLowerCase().includes('download') ||
                                               x.innerText.toLowerCase().includes('download'));
                            }
                            if (btn) { btn.click(); return true; }
                            return false;
                        }''')

                        if not btn_clicked:
                            self._log_debug("DL-DIAG: click_result=not_found")
                            raise Exception("Download button vanished between detection and click")

                    download = await download_info.value
                    
                    # Determine save filename
                    while True:
                        save_name = f"{prefix}{str(start_idx).zfill(padding)}.png"
                        save_path = os.path.join(save_dir, save_name)
                        if not os.path.exists(save_path):
                            break
                        start_idx += 1

                    await download.save_as(save_path)
                    self._log_debug(f"DL-DIAG: Native download completed and saved directly: {save_path}")

                    # Read original image into memory to avoid Windows file lock issues when saving metadata
                    with open(save_path, "rb") as f:
                        img_data = f.read()

                    with Image.open(io.BytesIO(img_data)) as original_img:
                        save_with_metadata(original_img, original_img, save_path, extra_meta=extra_meta)

                except Exception as dl_err:
                    self._log_debug(f"DL-DIAG: Native download failed: {dl_err}")
                    raise dl_err

                # Size validation: saved file must be >= 1MB
                file_sz = os.path.getsize(save_path)
                if file_sz < 1024 * 1024:
                    self._log_debug(f"DL-DIAG: Downloaded file too small ({file_sz/1024:.1f}KB < 1MB). Discarding.")
                    try:
                        os.remove(save_path)
                    except Exception:
                        pass
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(1.0)
                    continue

                # Check PIL average hash to prevent duplicate saving
                is_pixel_dup = False
                new_ahash = get_image_ahash(save_path)
                if new_ahash is not None:
                    for old_ahash in seen_hashes:
                        distance = bin(new_ahash ^ old_ahash).count('1')
                        if distance <= 3:
                            self._log_debug(f"Duplicate detected (aHash={hex(new_ahash)}, d={distance}). Deleting: {save_name}")
                            try:
                                os.remove(save_path)
                            except Exception:
                                pass
                            is_pixel_dup = True
                            break
                    if not is_pixel_dup:
                        seen_hashes.add(new_ahash)

                if is_pixel_dup:
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(1.0)
                    continue

                saved_paths.append(save_path)
                start_idx += 1
                dl_count += 1
                self._log_debug(f"Saved: {save_name}")

                await self._page.keyboard.press("Escape")
                await asyncio.sleep(1.0)
            except Exception as e:
                self._log_debug(f"Download skip: {e}")
                # Blob rescue: the dialog image src is a blob: URL containing the full-res image.
                # fetch(blobUrl) gives us the ORIGINAL bytes without canvas re-encoding loss.
                # Blob URLs are revoked when the dialog closes — must extract before pressing Escape.
                try:
                    self._log_debug("DL-DIAG: Attempting blob fetch rescue (dialog still open)...")
                    img_data = await self._page.evaluate('''async (imgEl) => {
                        try {
                            const dialogImg = document.querySelector('img[data-test-id="trusted-image"]') ||
                                              document.querySelector('mat-dialog-container img.generated-image') ||
                                              document.querySelector('mat-dialog-container img') ||
                                              imgEl;
                            if (!dialogImg) return {error: 'no_img'};
                            const src = dialogImg.src || '';
                            const nw = dialogImg.naturalWidth || 0;
                            const nh = dialogImg.naturalHeight || 0;
                            if (!src) return {error: 'no_src', nw, nh};

                            let fetchError = null;
                            // Strategy A: fetch the blob URL directly (lossless, original bytes)
                            if (src.startsWith('blob:')) {
                                try {
                                    const resp = await fetch(src);
                                    const buf = await resp.arrayBuffer();
                                    return {bytes: Array.from(new Uint8Array(buf)), nw, nh, method: 'blob_fetch'};
                                } catch (fetchErr) {
                                    fetchError = String(fetchErr);
                                    // Blob may have been revoked — fall through to canvas
                                }
                            }

                            // Strategy B: canvas fallback (only if blob fetch failed)
                            if (!nw) return {error: `not_loaded (fetch_err: ${fetchError})`, nw, nh};
                            const canvas = document.createElement('canvas');
                            canvas.width = nw;
                            canvas.height = nh;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(dialogImg, 0, 0);
                            const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
                            if (!blob) return {error: `canvas_null (fetch_err: ${fetchError})`, nw, nh};
                            const buf2 = await blob.arrayBuffer();
                            return {bytes: Array.from(new Uint8Array(buf2)), nw, nh, method: 'canvas', fetch_error: fetchError};
                        } catch(e) {
                            return {error: String(e)};
                        }
                    }''', img)

                    # Close dialog after extraction
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)

                    if img_data and img_data.get('bytes'):
                        nw = img_data.get('nw', 0)
                        method = img_data.get('method', 'unknown')
                        raw = bytes(img_data['bytes'])
                        fetch_err = img_data.get('fetch_error')
                        if fetch_err:
                            self._log_debug(f"DL-DIAG: Blob fetch failed with error: {fetch_err}")
                        self._log_debug(f"DL-DIAG: Rescued {len(raw)} bytes ({len(raw)/1024:.0f}KB) via {method}, naturalWidth={nw}")

                        # Reject if the image dimensions are too small (indicates a thumbnail)
                        if nw < 512:
                            self._log_debug(f"DL-DIAG: Image too small (naturalWidth={nw} < 512). Likely a thumbnail. Skipping.")
                            continue

                        while True:
                            save_name = f"{prefix}{str(start_idx).zfill(padding)}.png"
                            save_path = os.path.join(save_dir, save_name)
                            if not os.path.exists(save_path):
                                break
                            start_idx += 1

                        with Image.open(io.BytesIO(raw)) as pil_img:
                            save_with_metadata(pil_img, pil_img, save_path, extra_meta=extra_meta)

                        # Duplicate check via perceptual hash
                        is_pixel_dup = False
                        new_ahash = get_image_ahash(save_path)
                        if new_ahash is not None:
                            for old_ahash in seen_hashes:
                                distance = bin(new_ahash ^ old_ahash).count('1')
                                if distance <= 3:
                                    self._log_debug(f"Duplicate detected (aHash={hex(new_ahash)}, d={distance}). Deleting: {save_name}")
                                    try:
                                        os.remove(save_path)
                                    except Exception:
                                        pass
                                    is_pixel_dup = True
                                    break
                            if not is_pixel_dup:
                                seen_hashes.add(new_ahash)

                        if is_pixel_dup:
                            continue

                        saved_paths.append(save_path)
                        start_idx += 1
                        dl_count += 1
                        self._log_debug(f"Saved (blob rescue): {save_name}")
                    else:
                        err = img_data.get('error', 'unknown') if img_data else 'null_response'
                        self._log_debug(f"DL-DIAG: Blob rescue failed: {err}")
                except Exception as fallback_err:
                    self._log_debug(f"Blob rescue exception: {fallback_err}")
                    try:
                        await self._page.keyboard.press("Escape")
                    except:
                        pass

        if dl_count == 0:
            return {"status": "ignored", "message": "Images detected but all downloads failed.", "saved_paths": []}

        return {
            "status": "success", 
            "count": dl_count, 
            "next_start": start_idx,
            "saved_paths": saved_paths
        }

    async def stop_response(self):
        """
        Clicks the 'Stop' button (square icon) if it exists.
        Returns immediately without waiting for confirmation to keep the UI responsive.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        self._log_debug("Attempting to stop response via 'stop' icon...")
        
        stopped = await self._page.evaluate('''() => {
            const stopIcon = document.querySelector('mat-icon[data-mat-icon-name="stop"]');
            if (stopIcon) {
                const btn = stopIcon.closest('button');
                if (btn) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }''')

        if stopped:
            self._log_debug("Stop command sent successfully.")
            return {"status": "success", "message": "Response stop command triggered."}
        else:
            self._log_debug("Stop icon not found.")
            return {"status": "ignored", "message": "No active 'stop' icon found to click."}

    async def new_chat(self, target_url: str = None):
        """
        Clicks the 'New chat' button in the Gemini sidebar.
        If target_url is a Gem URL, it navigates directly instead.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        # Mark the start of the new session logs (for debug_dump tracking)
        engine_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "engine.log"))
        if os.path.exists(engine_log_path):
            self._engine_log_last_pos = os.path.getsize(engine_log_path)
        else:
            self._engine_log_last_pos = 0


        # 1. Smarter Navigation for Gems
        current_target = target_url
        if not current_target:
            # Try to read from config if not provided using standard utility
            try:
                cfg = load_config()
                current_target = cfg.get("browser_url")
            except: pass

        if current_target and "gemini.google.com/gem/" in current_target:
            self._log_debug(f"Gem URL detected: {current_target}")
            await self.navigate(current_target)
            await asyncio.sleep(2.0)
            return {"status": "success", "message": "Navigated to Gem URL directly."}

        self._log_debug("Attempting to trigger New Chat via UI...")
        
        # Try finding the element using a robust set of selectors (handling wrapper tag changes)
        result = await self._page.evaluate('''() => {
            const btn = document.querySelector('[data-test-id="new-chat-button"]') ||
                        document.querySelector('side-nav-action-button[data-test-id="new-chat-button"]') ||
                        document.querySelector('gem-nav-list-item[data-test-id="new-chat-button"]');
            if (btn) {
                // The actual clickable element might be the anchor or button inside
                const link = btn.querySelector('a[aria-label="New chat"]') || 
                             btn.querySelector('a') || 
                             btn.querySelector('button');
                if (link) {
                    link.click();
                    return "CLICKED_INNER_ELEMENT";
                }
                btn.click();
                return "CLICKED_CONTAINER";
            }
            // Fallback: search globally for any anchor or button with "New chat" aria-label
            const fallbackLink = document.querySelector('a[aria-label="New chat"]') || 
                                 document.querySelector('button[aria-label="New chat"]');
            if (fallbackLink) {
                fallbackLink.click();
                return "CLICKED_FALLBACK_LINK";
            }
            return "NOT_FOUND";
        }''')

        if result != "NOT_FOUND":
            self._log_debug(f"New Chat triggered: {result}")
            # Wait for navigation/reset
            await asyncio.sleep(1.0)
            return {"status": "success", "message": f"New Chat triggered ({result})."}
        else:
            self._log_debug("New Chat button not found. Falling back to default URL.")
            await self.navigate("https://gemini.google.com/app")
            return {"status": "success", "message": "Navigated to default app as fallback."}

    async def delete_activity_history(self, range_name: str = "Last hour"):
        """
        Navigates to the Gemini Activity page and deletes activity based on the specified range.
        range_name: 'Last hour', 'Last day', 'Always'
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        self._log_debug(f"Initiating history deletion: {range_name}")
        
        try:
            # 1. Direct navigation to the Gemini activity page
            await self.navigate("https://myactivity.google.com/product/gemini?utm_source=gemini")
            await asyncio.sleep(2.0)
            
            # --- [NEW] Pre-deletion: Handle initial warnings, tours, or banners ---
            # These can block the 'Delete' button or other interactions
            pre_dismiss_selectors = [
                'button:has-text("Dismiss")',
                'button:has-text("Got it")',
                'button[aria-label="Dismiss"]',
                '.xPkBGb:has-text("Dismiss")', # Specific selector for "Safer with Google" banner
                'div[role="dialog"] button:has-text("OK")'
            ]
            
            # Attempt to clear up to 2 distinct banners/popups
            for _ in range(2):
                dismiss_found = False
                for selector in pre_dismiss_selectors:
                    btn = self._page.locator(selector).first
                    if await btn.is_visible():
                        btn_text = await btn.inner_text() or selector
                        self._log_debug(f"Pre-deletion: Dismissing blocker ({btn_text})...")
                        await btn.click()
                        await asyncio.sleep(1.0)
                        dismiss_found = True
                        break # Check for next banner if any
                if not dismiss_found:
                    break

            # 2. Find and click the 'Delete' button
            delete_btn = self._page.locator('button[aria-label="Delete"]').first
            if not await delete_btn.is_visible():
                self._log_debug("Delete button not visible. Trying to scroll or force dismiss any overlays...")
                await self._page.mouse.click(10, 10) # Click corner to lose focus/dismiss lightboxes
                await self._page.keyboard.press("PageDown")
                await asyncio.sleep(1.0)
                if not await delete_btn.is_visible():
                    self._log_debug("Delete button still not visible on activity page.")
                    # Final attempt: click by coordinates if possible or log failure
                    return {"status": "error", "message": "Delete button not visible"}
            
            await delete_btn.click()
            await asyncio.sleep(1.0)
            
            # 3. Select the range option
            # Map user-friendly names to selectors/text
            range_map = {
                "Last hour": "Last hour",
                "Last day": "Last day",
                "All time": "Always"
            }
            target_text = range_map.get(range_name, "Last hour")
            
            # Use a more flexible locator to handle 'Always' vs 'All time' variants
            import re
            if range_name == "All time":
                self._log_debug("Searching for 'Always' or 'All time' option...")
                option = self._page.locator('li[role="menuitem"]').filter(has_text=re.compile(r"^(Always|All time)$", re.I)).first
            else:
                option = self._page.locator(f'li[role="menuitem"]:has-text("{target_text}")')

            if not await option.is_visible():
                return {"status": "error", "message": f"Option '{target_text}' not found"}
            
            await option.click()
            await asyncio.sleep(2.0)
            
            # 4. Handle Confirmation or "Got it" dialogs
            # These can appear for "Always" range or as a one-time warning/info
            # The USER reported: "Confirm that you would like to delete the following activity -> delete or close"
            dialog_selectors = [
                'button:has-text("Delete")',
                'button:has-text("Got it")',
                'button:has-text("Confirm")',
                'button.VfPpkd-LgbsSe:has-text("Delete")',
                'button.VfPpkd-LgbsSe:has-text("Got it")',
                'button:has-text("Close")'
            ]
            
            self._log_debug("Checking for post-selection dialogs...")
            for _ in range(4):
                dialog_handled = False
                
                modal = self._page.locator('div.llhEMd, div.VfPpkd-Sx9N0d').first
                if await modal.is_visible():
                    # Case 1: Detect "No activity" text inside modal
                    no_activity_text = modal.locator('text="You have no selected activity"').first
                    if await no_activity_text.is_visible():
                        close_btn = modal.locator('button:has-text("Close"), button:has-text("Got it")').first
                        if await close_btn.is_visible():
                            self._log_debug("Gemini Activity: No activity found to delete. Closing...")
                            await close_btn.click(force=True)
                            await asyncio.sleep(1.0)
                            return {"status": "success", "message": "No activity to delete"}

                    # Case 2: Detect "Delete" button inside modal
                    modal_delete_btn = modal.locator('button:has-text("Delete"), button[jsname="nUV0Pd"]').first
                    if await modal_delete_btn.is_visible():
                        self._log_debug("Gemini Activity: Deleting confirmed items...")
                        await modal_delete_btn.click(force=True)
                        await asyncio.sleep(2.0)
                        dialog_handled = True
                        continue

                    # Generic "Got it" or "OK" inside modal
                    modal_got_it_btn = modal.locator('button:has-text("Got it"), button:has-text("OK")').first
                    if await modal_got_it_btn.is_visible():
                        await modal_got_it_btn.click(force=True)
                        await asyncio.sleep(1.0)
                        dialog_handled = True
                        continue

                # Fallback to general selectors if modal check didn't catch it
                for selector in dialog_selectors:
                    btn = self._page.locator(selector).first
                    if await btn.is_visible():
                        btn_text = await btn.inner_text() or selector
                        await btn.click(force=True)
                        await asyncio.sleep(1.5)
                        dialog_handled = True
                        break 
                
                if not dialog_handled:
                    break
            
            # 5. Monitor Snackbar Feedback
            self._log_debug("Monitoring for snackbar feedback...")
            # Locator for snackbar/alert
            snackbar = self._page.locator('[role="alert"], [role="status"]').first
            
            # Monitoring loop for snackbar messages
            for _ in range(10): # 10 seconds timeout
                if await snackbar.is_visible():
                    msg = await snackbar.inner_text()
                    if msg:
                        flat_msg = " ".join(msg.strip().split())
                        self._log_debug(f"Gemini Activity: {flat_msg}")
                        
                        # Stop if we see a completion message
                        if any(x in flat_msg.lower() for x in ["deleted", "complete", "removed"]):
                            break
                await asyncio.sleep(1.0)
                
            return {"status": "success", "message": f"History deletion ({range_name}) completed."}
            
        except Exception as e:
            self._log_debug(f"Error during history deletion: {e}")
            return {"status": "error", "message": str(e)}

    async def stop_automation(self):
        """Signals the automation loop to stop and attempts to stop current page activity."""
        self._stop_automation_event.set()
        self._log_debug("Automation stop signaled. Attempting browser halt...")
        try:
            # Propagate stop to the actual browser button
            await self.stop_response()
        except:
            pass

    async def run_automation_loop(self, settings: dict):
        """
        Main automation loop.
        settings: {mode: 'rounds'|'images', goal: int, config: dict}
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        self._stop_automation_event.clear()
        self.automation_status["is_running"] = True
        
        # We NO LONGER reset cycles/successes here because this function is called per-round.
        # Initialization happens in engine_service or via a dedicated reset call.
        if self.automation_status.get("start_time") is None:
            from datetime import datetime
            self.automation_status["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            # Reset session lost flag for this run
            self._session_lost = False
            
            # Start Watchdog Task
            cfg = settings.get("config", {})
            if not cfg:
                self._log_debug("ERROR: Missing config in settings.")
                return {"status": "error", "message": "Missing config"}
                
            target_user = cfg.get("active_user")
            if self._watchdog_task is None:
                self._watchdog_task = asyncio.create_task(self._run_account_watchdog(target_user=target_user))

            self.automation_status["is_running"] = True
            self._log_debug(f"--- [AUTO] RUNNING ROUND: {self.automation_status.get('cycles', 0) + 1} ---")
            
            while self.automation_status.get("is_running", False):
                # Proactive Watchdog Check: if previous iteration (or watchdog) flagged session loss
                if getattr(self, "_session_lost", False):
                    self._log_debug("Watchdog: Critical session loss detected. Aborting loop.")
                    return {"status": "quota", "message": "Session lost or account mismatch."}

                if self._stop_automation_event.is_set():
                    break


                # Refresh cycles and stats from status in each iteration
                mode = self.automation_status.get("mode", "rounds")
                goal = self.automation_status.get("goal", 0)
                cycles = self.automation_status.get("cycles", 0)
                successes = self.automation_status.get("successes", 0)

                if mode == "rounds":
                    if cycles >= goal: break
                else: # images
                    if successes >= goal: break

                # 2. Cycle Strategy â€” record start time for this cycle
                if getattr(self, '_cycle_start_time', None) is None:
                    self._cycle_start_time = time.time()
                    self.automation_status["current_cycle_start_ts"] = self._cycle_start_time
                    self.automation_status["inter_cycle_start_ts"] = None  # exit watermark phase
                if getattr(self, '_lc_cycle_start_time', None) is None:
                    self._lc_cycle_start_time = time.time()
                is_initial = (cycles == 0) or getattr(self, "_automation_needs_new_chat", True)
                
                try:
                    # 3. Execution
                    if is_initial:
                        target_url = cfg.get("browser_url")
                        self._log_debug(f"Cycle #{cycles + 1}: Starting Fresh Setup (Navigating to: {target_url or 'New Chat'})...")
                        await self.new_chat(target_url=target_url)
                        if self._stop_automation_event.is_set(): break
                        await asyncio.sleep(2.0)
                        
                        await self.apply_settings(model_name=cfg.get("selected_model"), tool_name=cfg.get("selected_tool"))
                        if self._stop_automation_event.is_set(): break
                        
                        has_files = bool(cfg.get("selected_files"))
                        if has_files:
                            await self.attach_files(cfg.get("selected_files"))
                        if self._stop_automation_event.is_set(): break
                        
                        resp = await self.submit_response(text=cfg.get("prompt"), expect_attachments=has_files)
                        if self._stop_automation_event.is_set(): break
                        self._automation_needs_new_chat = False
                    else:
                        self._log_debug(f"Cycle #{cycles + 1}: Triggering Redo...")
                        resp = await self.redo_response()
                        if resp and resp.get("status") == "success":
                            resp = await self.submit_response(text=None) 
                        else:
                            # If Redo button not found, check if it's because of a reset
                            self._log_debug(f"Redo trigger failed: {resp.get('message') if resp else 'No response'}")
                            snapshot_data = await self._page.evaluate('''(args) => {
                                const responses = Array.from(document.querySelectorAll('model-response'));
                                if (responses.length === 0) return "reset";
                                return "error";
                            }''')
                            if snapshot_data == "reset":
                                resp = {"status": "reset", "message": "Reset detected during Redo attempt."}

                    # 4. Analyze Final Cycle Result
                    if not resp:
                        self._log_debug("ERROR: No response object after execution.")
                        return {"status": "error", "message": "Empty response"}

                    status = resp.get("status")
                    
                    if status == "success":
                        self.automation_status["cycles"] += 1
                        # NOTE: successes is NOT incremented here yet.
                        # It is only counted AFTER download_images confirms files are on disk.
                        # This prevents the count from inflating when a Reset occurs mid-download.
                        
                        naming = {
                            "prefix": cfg.get("name_prefix", ""), 
                            "padding": cfg.get("name_padding", 2), 
                            "start": cfg.get("name_start", 1)
                        }
                        
                        # Safety for join
                        selected_files = cfg.get("selected_files") or []
                        meta = {
                            "aspect_ratio": cfg.get("aspect_ratio", ""),
                            # Use clean prompt (without "Aspect Ratio: ..." prefix) for metadata
                            "prompt": cfg.get("prompt_clean", cfg.get("prompt", "")), 
                            "url": self.current_url or "", 
                            "upload_path": ", ".join(selected_files) if isinstance(selected_files, list) else str(selected_files)
                        }

                        # In "modify image" mode the UI stores the reference image's original
                        # PNG metadata in image_ref_source_meta.  Use those values so the newly
                        # downloaded image inherits the correct provenance (prompt/url/upload_path
                        # from the original reference image, not the ephemeral prefix prompt).
                        ref_source_meta = cfg.get("image_ref_source_meta")
                        if ref_source_meta and isinstance(ref_source_meta, dict):
                            for _k in ("aspect_ratio", "prompt", "url", "upload_path"):
                                if ref_source_meta.get(_k):
                                    if _k == "prompt":
                                        import re
                                        meta[_k] = re.sub(r"^Aspect Ratio:.*?\n\n", "", ref_source_meta[_k], flags=re.DOTALL)
                                    else:
                                        meta[_k] = ref_source_meta[_k]
                        
                        dl_resp = await self.download_images(cfg.get("save_dir"), naming, meta)
                        saved_paths = []
                        if dl_resp and dl_resp.get("status") == "success":
                            saved_paths = dl_resp.get("saved_paths", [])
                            
                        if saved_paths:
                            new_start = dl_resp.get("next_start", cfg.get("name_start"))
                            cfg["name_start"] = new_start
                            self._update_config_start(new_start)
                            
                            self.automation_status["successes"] += 1
                            self.automation_status["cycles"] += 1
                            
                            # Write per-image reject stat record
                            cycle_end = time.time()
                            cycle_dur = cycle_end - self._cycle_start_time if self._cycle_start_time else 0
                            for i, sp in enumerate(saved_paths):
                                self._write_reject_stat(
                                    filename=os.path.basename(sp),
                                    duration_sec=cycle_dur / max(len(saved_paths), 1),
                                    refused_count=self._pending_refused if i == 0 else 0,
                                    reset_count=self._pending_resets if i == 0 else 0
                                )
                            # Snapshot pending counters BEFORE zeroing
                            cycle_refused_snap = getattr(self, '_pending_refused', 0)
                            cycle_resets_snap  = getattr(self, '_pending_resets', 0)
                            lc_cycle_refused_snap = getattr(self, '_lc_pending_refused', 0)
                            lc_cycle_resets_snap = getattr(self, '_lc_pending_resets', 0)
                            
                            lc_cycle_end = time.time()
                            lc_cycle_dur = lc_cycle_end - self._lc_cycle_start_time if getattr(self, '_lc_cycle_start_time', None) else 0

                            # Reset global pending counters and mark cycle end cleanly.
                            self._pending_refused = 0
                            self._pending_resets = 0
                            self.automation_status["pending_refused"] = 0
                            self.automation_status["pending_resets"] = 0
                            self._cycle_start_time = None
                            self.automation_status["current_cycle_start_ts"] = None
                            # Mark the inter-cycle phase start (watermark / post-processing period)
                            self.automation_status["inter_cycle_start_ts"] = time.time()
                            
                            # Reset loop control pending counters.
                            self._lc_pending_refused = 0
                            self._lc_pending_resets = 0
                            self._lc_cycle_start_time = None
                        else:
                            # Download failed (e.g. Reset mid-download). Do NOT count as success.
                            self._log_debug("Download failed after image detected. Success NOT counted. Forcing New Chat.")
                            self.automation_status["resets"] += 1
                            self._pending_resets = getattr(self, '_pending_resets', 0) + 1
                            self.automation_status["pending_resets"] = self._pending_resets
                            self._lc_pending_resets = getattr(self, '_lc_pending_resets', 0) + 1
                            self._automation_needs_new_chat = True
                            
                            cycle_refused_snap = 0
                            cycle_resets_snap  = 0
                            lc_cycle_refused_snap = 0
                            lc_cycle_resets_snap = 0
                            cycle_dur          = 0
                            lc_cycle_dur       = 0
                        
                        # Cycle complete â€” expose cycle stats for loop-control threshold check
                        return {
                            "status": "success",
                            "saved_paths": saved_paths,
                            "cycle_duration_sec": cycle_dur,
                            "cycle_refused": cycle_refused_snap,
                            "cycle_resets":  cycle_resets_snap,
                            "lc_cycle_duration_sec": lc_cycle_dur,
                            "lc_cycle_refused": lc_cycle_refused_snap,
                            "lc_cycle_resets": lc_cycle_resets_snap,
                        }
                        
                    elif status == "refused":
                        self.automation_status["cycles"] += 1
                        self.automation_status["refusals"] += 1
                        self._pending_refused = getattr(self, '_pending_refused', 0) + 1
                        self.automation_status["pending_refused"] = self._pending_refused
                        self._lc_pending_refused = getattr(self, '_lc_pending_refused', 0) + 1
                        return {"status": "refused"}
                        
                    elif status == "reset":
                        self.automation_status["resets"] += 1
                        self.automation_status["cycles"] += 1
                        self._pending_resets = getattr(self, '_pending_resets', 0) + 1
                        self.automation_status["pending_resets"] = self._pending_resets
                        self._lc_pending_resets = getattr(self, '_lc_pending_resets', 0) + 1
                        self._log_debug(f"Reset detected in Cycle #{self.automation_status['cycles']}. Counting and forcing New Chat.")
                        self._automation_needs_new_chat = True
                        return {"status": "reset"}
                        
                    elif status in ["error", "timeout"]:
                        if status == "error" and "quota" in str(resp.get("message", "")).lower():
                            self._log_debug("QUOTA EXCEEDED.")
                            await self.stop()
                            self.automation_status["is_running"] = False
                            self._automation_needs_new_chat = True
                            return {"status": "quota", "message": "Quota reached."}
                        else:
                            self._log_debug(f"Automation loop encountered an issue: {resp.get('message')}")
                            self.automation_status["cycles"] += 1
                            self.automation_status["resets"] += 1
                            self._pending_resets += 1
                            self.automation_status["pending_resets"] = self._pending_resets
                            self._lc_pending_resets = getattr(self, '_lc_pending_resets', 0) + 1
                            self._automation_needs_new_chat = True
                            return {"status": status, "message": resp.get("message", "Unknown issue occurred")}

                    elif status == "stopped":
                        # Stop signal received during submit_response.
                        # Break immediately to avoid re-iterating the while loop with is_initial=True,
                        # which would duplicate setup steps (new_chat, apply_settings) and
                        # potentially re-download the same image from the previous response.
                        break

                    await asyncio.sleep(2)
                    

                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    self._log_debug(f"Automation Error in Cycle #{self.automation_status['cycles']+1}:\n{tb}")
                    # Treat as a recoverable reset instead of breaking the entire loop.
                    # This allows engine_service to continue with the next round or switch accounts.
                    self.automation_status["cycles"] += 1
                    self.automation_status["resets"] += 1
                    self._pending_resets = getattr(self, '_pending_resets', 0) + 1
                    self.automation_status["pending_resets"] = self._pending_resets
                    self._lc_pending_resets = getattr(self, '_lc_pending_resets', 0) + 1
                    self._automation_needs_new_chat = True
                    self._log_debug("Recoverable error â€” will retry with New Chat on next round.")
                    return {"status": "reset", "message": f"Automation error (recovered): {e}"}

            # NOTE: We NO LONGER clear _cycle_start_time here...
            self.automation_status["is_running"] = False
            self._log_debug(f"Automation Finished. Final Stats: {self.automation_status}")
            
            # --- FINAL EXIT RECORDING REMOVED FROM HERE ---
            # Now handled by engine_service.py's finally block for better session-wide accuracy.
            
            final_status = "finished"
            if getattr(self, "_session_lost", False):
                final_status = "quota"
            elif self._stop_automation_event.is_set():
                final_status = "stopped"
                
            return {"status": final_status, "stats": self.automation_status}
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log_debug(f"CRITICAL CRASH in run_automation_loop:\n{tb}")
            self.automation_status["is_running"] = False
            return {"status": "error", "message": str(e)}
        finally:
            # Lifecycle: Ensure watchdog is killed when automation loop ends
            if self._watchdog_task:
                # Silently cancel the watchdog - no log needed for routine teardown
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except asyncio.CancelledError:
                    pass
                self._watchdog_task = None

    async def _run_account_watchdog(self, target_user: str = None):
        """
        Independent background task to periodically verify login status.
        Runs until _stop_automation_event is set.
        """
        # Fully silent start - anomalies only are logged
        try:
            # Initial cooldown to let first-page navigation settle.
            # Read from config; default 20s to cover Gem URL load + model/tool apply.
            try:
                _cfg = load_config()
                initial_delay = _cfg.get("watchdog_initial_delay", 20)
            except Exception:
                initial_delay = 20
            await asyncio.sleep(initial_delay)
            
            while not self._stop_automation_event.is_set():
                if not self.is_running or not self._page:
                    break
                
                try:
                    # Non-invasive account check
                    acc = await self.get_account_info()
                    
                    # 1. Detection: Not Logged In
                    if not acc.get("logged_in"):
                        self._log_watchdog("CRITICAL - Session lost (Guest detected).", to_ui=True)
                        self._session_lost = True
                        self._stop_automation_event.set()
                        break
                    
                    # 2. Detection: Account Mismatch (if target_user provided as email)
                    current_acc = acc.get("account_id")
                    if target_user and "@" in target_user and current_acc:
                        if target_user.lower() != current_acc.lower() and current_acc != "Unknown Account":
                            self._log_watchdog(f"CRITICAL - Account mismatch! Expected {target_user}, found {current_acc}.", to_ui=True)
                            self._session_lost = True
                            self._stop_automation_event.set()
                            break

                except Exception as e:
                    self._log_watchdog(f"Anomaly: Check failed ({e}). Retrying in 30s...")

                # Periodic check interval
                await asyncio.sleep(45)
                
        except asyncio.CancelledError:
            pass # Clean exit
        except Exception as e:
            self._log_watchdog(f"Critical Watchdog Internal Error: {e}")
        # No finally log - fully silent on normal end

    def _update_config_start(self, next_start):
        """Helper to persist the next available start number using config_utils."""
        try:
            save_config({"name_start": next_start})
            self._log_debug(f"Persistence: next start number updated to {next_start}.")
        except Exception as e:
            self._log_debug(f"Persistence Error: Failed to update config: {e}")

    async def get_account_info(self):
        """Checks the browser's top-right login status via DOM selectors.
        Returns a dict: {logged_in: bool, account_id: str|None, status: str}
        Based on the proven check_signin.py reference pattern.
        """
        if not self.is_running:
            raise Exception("Browser Engine not started")

        import re

        # Brief stability wait, then try network idle
        await self._page.wait_for_timeout(200)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass  # Proceed even if network idle times out

        # Selector 1: Google Account button (logged-in indicator)
        avatar_selectors = 'a[href*="accounts.google.com/SignOut"], [aria-label*="Google Account"], img.mavatar-image, img.gb_n, img[src*="googleusercontent.com/a/"]'
        avatar_locators = self._page.locator(avatar_selectors)

        # Selector 2: Sign-in button (not-logged-in indicator)
        signin_selectors = 'a[href*="accounts.google.com/ServiceLogin"], button:has-text("Sign in"), a:has-text("Sign in")'
        signin_locators = self._page.locator(signin_selectors)

        # Find the first VISIBLE element for avatar
        is_logged_in = False
        target_avatar = None
        count_avatar = await avatar_locators.count()
        for i in range(count_avatar):
            if await avatar_locators.nth(i).is_visible():
                is_logged_in = True
                target_avatar = avatar_locators.nth(i)
                break

        # Find the first VISIBLE element for sign in
        is_not_logged_in = False
        target_signin = None
        count_signin = await signin_locators.count()
        for i in range(count_signin):
            if await signin_locators.nth(i).is_visible():
                is_not_logged_in = True
                target_signin = signin_locators.nth(i)
                break

        if is_logged_in and target_avatar:
            account_id = "Unknown Account"
            try:
                # Traverse up from the element to find an aria-label or title with the email
                aria_label = await target_avatar.evaluate('''el => {
                    let current = el;
                    for (let i = 0; i < 5; i++) {
                        if (!current) break;
                        let label = current.getAttribute('aria-label') || current.getAttribute('title') || current.getAttribute('data-tooltip');
                        if (label && label.includes('@')) return label;
                        if (label && label.toLowerCase().includes('google account')) return label;
                        current = current.parentElement;
                    }
                    return null;
                }''')
                if aria_label:
                    import re
                    match_email = re.search(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b", aria_label)
                    match_name = re.search(r"Google Account:\s*(.*?)\s*\(", aria_label, re.I)
                    if match_email:
                        account_id = match_email.group(1)
                    elif match_name:
                        account_id = match_name.group(1)
                    else:
                        account_id = aria_label.split(':')[-1].strip()
            except Exception:
                pass
            
            self.automation_status["current_account_id"] = account_id
            return {"logged_in": True, "account_id": account_id, "status": "logged_in"}

        elif is_not_logged_in:
            self.automation_status["current_account_id"] = None
            return {"logged_in": False, "account_id": None, "status": "not_logged_in"}

        else:
            # Fallback: check Gemini sidebar conversations list
            chat_list = self._page.locator('div[data-test-id="conversations-list"]').first
            if await chat_list.is_visible():
                account_id = "Unknown (sidebar detected)"
                self.automation_status["current_account_id"] = account_id
                return {"logged_in": True, "account_id": account_id, "status": "logged_in"}
            
            self.automation_status["current_account_id"] = None
            return {"logged_in": False, "account_id": None, "status": "unknown"}

    async def debug_dump(self, action_name):
        """Dumps a window of engine.log + current page HTML into data/debug.log.

        Each call captures only the engine.log lines written SINCE the previous
        debug_dump call, so every section in debug.log corresponds exactly to
        what the engine was doing during that action (new chat / submit / redo).

        On 'new chat': overwrites debug.log (new session) and resets the read
        position to the current end of engine.log, so only future entries are
        captured.
        """
        try:
            import os
            import json
            from datetime import datetime

            # 1. Check if debug logging is enabled in config
            config_path = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "config.json"))
            if not os.path.exists(config_path):
                return
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not cfg.get("debug_logging_enabled", False):
                return

            engine_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "engine.log"))
            debug_log_path  = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "debug.log"))
            os.makedirs(os.path.dirname(debug_log_path), exist_ok=True)

            is_new_chat = (action_name == "new chat")
            write_mode  = "w" if is_new_chat else "a"

            # 2. Read ONLY the new engine.log lines since the last dump.
            #    _engine_log_last_pos tracks the byte offset after the previous read.
            if is_new_chat:
                # On new chat: read logs generated since self._engine_log_last_pos was set in new_chat()
                last_pos = getattr(self, '_engine_log_last_pos', None)
                if last_pos is None:
                    if os.path.exists(engine_log_path):
                        last_pos = os.path.getsize(engine_log_path)
                    else:
                        last_pos = 0
                new_log_lines = ""
                if os.path.exists(engine_log_path):
                    try:
                        with open(engine_log_path, "r", encoding="utf-8", errors="replace") as lf:
                            lf.seek(last_pos)
                            new_log_lines = lf.read()
                            self._engine_log_last_pos = lf.tell()
                    except Exception as le:
                        new_log_lines = f"Error reading engine log: {le}"

            else:
                last_pos = getattr(self, '_engine_log_last_pos', 0)
                new_log_lines = ""
                if os.path.exists(engine_log_path):
                    try:
                        with open(engine_log_path, "r", encoding="utf-8", errors="replace") as lf:
                            lf.seek(last_pos)
                            new_log_lines = lf.read()
                            self._engine_log_last_pos = lf.tell()
                    except Exception as le:
                        new_log_lines = f"Error reading engine log: {le}"

            # 3. Get the page HTML
            page_content = ""
            if self._page:
                try:
                    page_content = await self._page.content()
                except Exception as pe:
                    page_content = f"Error retrieving page content: {pe}"
            else:
                page_content = "Browser not started or page not available."

            # 4. Write to debug.log
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header = (
                "\n" + "="*80 +
                f"\nDEBUG DUMP: Action '{action_name}' at {timestamp}\n" +
                "="*80 + "\n"
            )

            with open(debug_log_path, write_mode, encoding="utf-8") as df:
                df.write(header)
                if new_log_lines:
                    df.write("--- ENGINE LOG (this action) ---\n")
                    df.write(new_log_lines)
                    df.write("\n")
                df.write(f"--- BROWSER PAGE HTML ({action_name}) ---\n")
                df.write(page_content)
                df.write("\n")

            self._log_debug(f"Debug logging: Dumped DOM and logs for '{action_name}' to data/debug.log (mode={write_mode})")
        except Exception as e:
            if hasattr(self, '_log_debug'):
                self._log_debug(f"Debug logging failed: {e}")



    async def test_connection(self):
        """Simple test to verify Playwright installation."""
        try:
            await self.start()
            status = await self.navigate("https://www.google.com")
            print(f"Connection Test: Google returned {status}")
            await self.get_screenshot("browser_screen_capture/test_google.png")
            await self.stop()
            return True
        except Exception as e:
            print(f"Connection Test Failed: {e}")
            return False

if __name__ == "__main__":
    # Test script
    engine = BrowserEngine()
    asyncio.run(engine.test_connection())



