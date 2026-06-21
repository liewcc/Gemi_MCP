# Gemi_MCP

**English** | [简体中文](README.zh-CN.md)

Drive **Gemini & DeepSeek web UIs** with browser automation — **no API key, no billing**. It logs
into your normal accounts in a real browser and automatically processes chats, attachments, and settings for you.

It comes in two parts:

1. **The Engine + Control Panel** — a small app (a text-based control panel, "TUI") that opens
   a browser, logs into Gemini, and runs the automation.
2. **An MCP server** — an optional bridge so AI assistants like **Claude Code** or **Cursor**
   can ask Gemini to do work for them automatically.

> You can use part 1 on its own. Part 2 is only needed if you want another AI tool to control it.

---

## ✅ Before you start (requirements)

You only need two things installed on **Windows**:

| What | Why | How to check |
|------|-----|--------------|
| **Python 3.10 or newer** | Runs the program | Open a terminal, type `python --version` |
| **A Google account** | To log into Gemini | You log in inside the app later |

### Installing Python (if you don't have it)

1. Go to <https://www.python.org/downloads/>
2. Download the latest Windows installer and run it.
3. **IMPORTANT:** On the first screen, tick the box **“Add python.exe to PATH”** before
   clicking *Install Now*. If you skip this, the commands below won't work.
4. After installing, open a **new** terminal and run `python --version`. You should see
   something like `Python 3.12.x`.

---

## 🚀 Install (one time only)

1. **Download this project.**
   - Easiest way: on the GitHub page click the green **`< > Code`** button → **Download ZIP**,
     then unzip it somewhere simple like `D:\Gemi_MCP`.
   - Or, if you have Git: `git clone <repo-url>`

2. **Open the folder** you just unzipped.

3. **Double-click `setup.bat`.**
   A black window opens and installs everything automatically:
   - Python libraries (Playwright, FastAPI, etc.)
   - The Chromium browser that the automation drives
   - The folders it needs to run
   - **Automatic MCP registration:** The script detects and registers `gemi-mcp` with **Claude Code** and any installed **Antigravity** products (CLI, Desktop, IDE) — no manual config editing needed.

   This takes a few minutes the first time. When you see **`Setup complete!`**, you're done.
   You can close the window.

> 💡 If `setup.bat` reports an error about `pip` or `playwright`, it almost always means Python
> wasn't added to PATH. Reinstall Python with the **“Add to PATH”** box ticked (see above), then
> run `setup.bat` again.

---

## ▶️ Run it & log in (first time)

1. **Double-click `run.bat`.**
   A control panel opens in the terminal — this is where you turn things on and off.

2. **Log into Gemini:**
   - Use the arrow keys / mouse to open the **`Accounts`** tab.
   - Press **`+ Add account (registration mode)`**.
   - A **real browser window opens** — log into your Google account there, just like normal.
   - Once Gemini is open and working in that window, go back to the control panel and press
     **`Ctrl+R`** to reload. Your account now shows up in the list.

3. That's it. The engine will reuse this login next time — you won't have to log in again
   unless Google signs you out.

### 🤖 Switching between Gemini and DeepSeek

The engine supports two services: **Gemini** (default) and **DeepSeek**.

Switching is done via the MCP `switch_service` tool (your AI assistant calls this for you).
On first use, the engine opens a browser window so you can log into your DeepSeek account.
After that, the session is saved automatically — you won't be asked to log in again unless
DeepSeek signs you out.

### Daily use

- Just double-click **`run.bat`**. The engine starts and logs in automatically.
- The bottom status bar shows **`● online`** when the engine is ready.
- Press **`q`** to quit. Closing the window also shuts the engine down cleanly.

The control panel tabs let you change models, output folders, image settings, automation loops,
and switch between accounts.

---

## 🔌 (Optional) Connect to Claude Code / Cursor / Antigravity

This lets another AI assistant send tasks to Gemini or DeepSeek through Gemi_MCP.

### ⚡ Automatic setup (recommended)

`setup.bat` automatically registers `gemi-mcp` with **Claude Code** and any installed
**Antigravity** products. If you approved those steps during setup, you are already connected.

### 🛠️ Manual config (Cursor & other clients)

Add this to your MCP client's config file. Replace the path with **your** project folder:

```json
{
  "mcpServers": {
    "gemi-mcp": {
      "command": "python",
      "args": ["D:\\Gemi_MCP\\mcp\\server.py"]
    }
  }
}
```

> Use **double backslashes** (`\\`) in the path on Windows, as shown above.

### 🚀 Usage

Make sure `run.bat` is running (showing **`● online`**) before your AI client makes any
requests. Your assistant gains tools like `send_chat`, `switch_service`, `attach_files`,
and `get_last_response`.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| `'python' is not recognized` | Python isn't on PATH. Reinstall Python with **“Add to PATH”** ticked, open a new terminal, try again. |
| `setup.bat` fails on `playwright install` | Run this manually in the project folder: `python -m playwright install chromium` |
| Control panel says `○ offline` | Wait ~10–20s after starting; the browser is logging in. If it stays offline, press the **Restart** button on the Engine tab. |
| It asks me to log in every time | Google may be signing you out. Re-run **Add account (registration mode)** and make sure you stay logged in / check “remember me”. |
| MCP client can't reach it | Make sure `run.bat` is open and shows **`● online`** before starting your AI client. |
| Headed browser window pops up during DeepSeek use | DeepSeek requires verification or a fresh login. Complete the action in the browser window, then retry your request. |
| Need to start fresh | Delete the `data\` folder and `core\browser_user_data\` folder, then re-run `setup.bat` and log in again. |

---

## 📁 What gets created on your machine

These are made automatically and **stay on your computer only** — they are never uploaded:

- `data\` — your settings and account list
- `core\browser_user_data\` — your logged-in browser profile (cookies)
- `gemini_outputs\` — where generated images are saved by default

> ⚠️ Never share these folders — they contain your login session.

---

## ℹ️ Notes & limits

- This automates the **free web version** of Gemini. It is subject to Gemini's normal usage
  limits and Terms of Service. Use your own account responsibly.
- Currently built and tested on **Windows**.
- This is an unofficial tool and is **not affiliated with Google**.
