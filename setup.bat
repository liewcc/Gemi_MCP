@echo off
title Gemi MCP - Setup
cd /d "%~dp0"

echo ============================================
echo  Gemi MCP Setup
echo ============================================
echo.

echo [1/7] Initializing engine submodule (Gemi_Engine)...
git submodule update --init --recursive
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: git submodule init failed.
    echo Make sure Git is installed and you cloned this repo (not just downloaded a ZIP).
    pause
    exit /b 1
)

echo.
echo [2/7] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [3/7] Installing Playwright browser (Chromium)...
playwright install chromium
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: playwright install failed.
    echo Try running: python -m playwright install chromium
    pause
    exit /b 1
)

echo.
echo [4/7] Creating required runtime directories...
if not exist "data" mkdir "data"
if not exist "runtime\browser_user_data" mkdir "runtime\browser_user_data"
if not exist "runtime\browser_screen_capture" mkdir "runtime\browser_screen_capture"

echo.
echo [5/7] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Gemi MCP.lnk'); $lnk.TargetPath = '%~dp0run.vbs'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0img\logo.ico'; $lnk.Save()"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Could not create desktop shortcut.
) else (
    echo   Shortcut created: Desktop\Gemi MCP.lnk
)

echo.
echo [6/7] Registering with Claude Code...
where claude >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo   WARNING: claude CLI not found in PATH.
    echo   Run this command manually after installing Claude Code:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :register_antigravity
)

set /p REGISTER_MCP="  Register gemi-mcp with Claude Code now? (y/n): "
if /i not "%REGISTER_MCP%"=="y" (
    echo   Skipped. Run manually when ready:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :register_antigravity
)

claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Registration failed. Try running manually:
    echo     claude mcp add gemi-mcp -- python "%~dp0mcp\server.py"
    goto :register_antigravity
)

echo.
echo   Verifying registration...
claude mcp list | findstr "gemi-mcp" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] gemi-mcp registered successfully.
) else (
    echo   WARNING: Could not verify registration. Check with: claude mcp list
)

:register_antigravity
echo.
echo [7/7] Registering with Antigravity...
set "AGY_FOUND="
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" set "AGY_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe" set "AGY_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe" set "AGY_FOUND=1"

if not defined AGY_FOUND (
    echo   WARNING: Antigravity installation not found.
    echo   If you install it later, register manually by adding this to:
    echo     %%USERPROFILE%%\.gemini\config\mcp_config.json
    echo.
    echo   {
    echo     "mcpServers": {
    echo       "gemi-mcp": {
    echo         "command": "python",
    echo         "args": [
    echo           "PATH_TO_GEMI_MCP\mcp\server.py"
    echo         ]
    echo       }
    echo     }
    echo   }
    goto :done
)

python -c "import json, os; p=os.path.expandvars(r'%%USERPROFILE%%/.gemini/config/mcp_config.json'); exit(0 if os.path.exists(p) and 'gemi-mcp' in json.load(open(p, encoding='utf-8')).get('mcpServers', {}) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] gemi-mcp is already registered with Antigravity.
    goto :done
)

set /p REGISTER_AGY="  Register gemi-mcp with Antigravity now? (y/n): "
if /i not "%REGISTER_AGY%"=="y" (
    echo   Skipped. Run manually when ready.
    goto :done
)

python -c "import os; f=open(os.path.expandvars(r'%%TEMP%%\agy_mcp_register.py'), 'w', encoding='utf-8'); f.write('import sys, os, json\np = os.path.expandvars(r\'%%USERPROFILE%%/.gemini/config/mcp_config.json\')\nos.makedirs(os.path.dirname(p), exist_ok=True)\nd = {}\nif os.path.exists(p):\n    try:\n        with open(p, encoding=\'utf-8\') as f: d = json.load(f)\n    except: pass\nif \'mcpServers\' not in d: d[\'mcpServers\'] = {}\nd[\'mcpServers\'][\'gemi-mcp\'] = {\'command\': \'python\', \'args\': [sys.argv[1]]}\nwith open(p, \'w\', encoding=\'utf-8\') as f: json.dump(d, f, indent=2)\n')"
python "%TEMP%\agy_mcp_register.py" "%~dp0mcp\server.py"
del "%TEMP%\agy_mcp_register.py"

python -c "import json, os; p=os.path.expandvars(r'%%USERPROFILE%%/.gemini/config/mcp_config.json'); exit(0 if os.path.exists(p) and 'gemi-mcp' in json.load(open(p, encoding='utf-8')).get('mcpServers', {}) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] gemi-mcp registered successfully with Antigravity.
) else (
    echo   WARNING: Could not verify registration. Check %%USERPROFILE%%\.gemini\config\mcp_config.json
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
