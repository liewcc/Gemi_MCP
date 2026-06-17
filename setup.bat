@echo off
title Gemi MCP - Setup
cd /d "%~dp0"

echo ============================================
echo  Gemi MCP Setup
echo ============================================
echo.

echo [1/4] Initializing engine submodule (Gemi_Engine)...
git submodule update --init --recursive
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: git submodule init failed.
    echo Make sure Git is installed and you cloned this repo (not just downloaded a ZIP).
    pause
    exit /b 1
)

echo.
echo [2/4] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [3/4] Installing Playwright browser (Chromium)...
playwright install chromium
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: playwright install failed.
    echo Try running: python -m playwright install chromium
    pause
    exit /b 1
)

echo.
echo [4/4] Creating required runtime directories...
if not exist "data" mkdir "data"
if not exist "core\browser_user_data" mkdir "core\browser_user_data"
if not exist "core\browser_screen_capture" mkdir "core\browser_screen_capture"

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Double-click run.bat to open the control panel (TUI).
echo  2. Go to the Accounts tab - Add account (registration mode).
echo     A browser window opens - log in to your Google account, then
echo     press ctrl+r to reload.
echo  3. (Optional) Connect the MCP server to your AI client:
echo       python mcp/server.py
echo ============================================
echo.
pause
