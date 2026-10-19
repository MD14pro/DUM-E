@echo off
cd /d "%~dp0"
title DUM-E Autonomous Library Station Launcher
color 0A

echo ================================================================
echo        DUM-E Autonomous Library Station Launcher (v2.6)
echo        [Student I-Card Auth + Web Kiosk + CoppeliaSim]
echo ================================================================
echo.

:: Remove accidental 0-byte 'python' file if present in project directory
if exist "%~dp0python" del /f /q "%~dp0python" >nul 2>&1

:: Free port 8000 if an old backend instance is still listening
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

:: 1. Sync CoppeliaSim scene with persistent library.db state
echo [1/2] Syncing 3D books in CoppeliaSim with library.db...
python -u sync_simulation.py
echo.

:: 2. Start Unified Web Dashboard (http://127.0.0.1:8000/) + DUM-E Robot Controller (Auth Enabled)
echo [2/2] Starting Web Dashboard (http://127.0.0.1:8000/) and DUM-E Robot Controller...
echo       Verify Student I-Card in Step 1 on the Web Kiosk to unlock Issue/Return!
echo.
python -u main_system.py

pause