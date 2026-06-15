@echo off
title Gemi Engine Control — TUI
cd /d "%~dp0"

python tui/app.py

:: Fallback: kill service if TUI exited without cleanup (Ctrl+C, crash, etc.)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :18800 ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%a > nul 2>&1
)
