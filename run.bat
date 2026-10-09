@echo off
REM Daily run: Open scene in CoppeliaSim, START Simulation, then run this file.
cd /d "%~dp0"
start "Backend + UI" cmd /k python -m uvicorn app:app --port 8000
timeout /t 3 >nul
start "" http://127.0.0.1:8000
start "Robot worker" cmd /k python main_system.py --mode worker --camera 0
