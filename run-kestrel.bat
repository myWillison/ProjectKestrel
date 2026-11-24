@echo off
REM Local dev launcher: starts the pywebview desktop app with the project venv.
REM Logs stay visible in this console. Pass --cli "<folder>" to run headless instead.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [kestrel] .venv not found. Create it with:
  echo   py -3.11 -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" analyzer\visualizer.py %*
if errorlevel 1 (
  echo.
  echo [kestrel] exited with error code %errorlevel%.
  pause
)
