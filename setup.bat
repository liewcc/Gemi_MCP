@echo off
title Gemi MCP - Setup
cd /d "%~dp0"

echo ============================================
echo  Gemi MCP Setup
echo ============================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browser (Chromium)...
playwright install chromium
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: playwright install failed.
    echo Try running: python -m playwright install chromium
    pause
    exit /b 1
)

echo.
echo [3/3] Creating required runtime directories...
if not exist "data" mkdir "data"
if not exist "core\browser_user_data" mkdir "core\browser_user_data"
if not exist "core\browser_screen_capture" mkdir "core\browser_screen_capture"

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Run run_engine.bat to start the engine service
echo  2. A browser window will open - log in to your Google account
echo  3. Add the MCP server to your AI client:
echo       python mcp/server.py
echo ============================================
echo.
pause
