@echo off
if "%1"=="--wt-launched" goto :run

where wt >nul 2>&1
if %ERRORLEVEL% equ 0 (
    wt --size 110,40 new-tab cmd /c "%~f0" --wt-launched
    exit /b
)

:run
title GEMI MCP
cd /d "%~dp0"

python tui/app.py

:: Fallback: kill service if TUI exited without cleanup (Ctrl+C, crash, etc.)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :18800 ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%a > nul 2>&1
)
