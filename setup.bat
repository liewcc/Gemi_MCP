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
echo [4/6] Creating required runtime directories...
if not exist "data" mkdir "data"
if not exist "core\browser_user_data" mkdir "core\browser_user_data"
if not exist "core\browser_screen_capture" mkdir "core\browser_screen_capture"

echo.
echo [5/6] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Gemi MCP.lnk'); $lnk.TargetPath = '%~dp0run.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0img\logo.ico'; $lnk.Save()"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Could not create desktop shortcut.
) else (
    echo   Shortcut created: Desktop\Gemi MCP.lnk
)

echo.
echo [6/6] Registering with Claude Code...
where claude >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo   WARNING: claude CLI not found in PATH.
    echo   Run this command manually after installing Claude Code:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :done
)

set /p REGISTER_MCP="  Register gemi-mcp with Claude Code now? (y/n): "
if /i not "%REGISTER_MCP%"=="y" (
    echo   Skipped. Run manually when ready:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :done
)

claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Registration failed. Try running manually:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :done
)

echo.
echo   Verifying registration...
claude mcp list | findstr "gemi-mcp" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] gemi-mcp registered successfully.
) else (
    echo   WARNING: Could not verify registration. Check with: claude mcp list
)

:done
echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Double-click "Gemi MCP" on your desktop to open the control panel.
echo  2. Go to the Accounts tab - Add account (registration mode).
echo     A browser window opens - log in to your Google account, then
echo     press ctrl+r to reload.
echo ============================================
echo.
pause
