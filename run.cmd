@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto ready
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
echo Open http://127.0.0.1:8765 in your browser. Keep this window open.
".venv\Scripts\python.exe" server.py
goto end
:failed
echo Install Python 3.11 or newer and run this file again. The first setup needs internet.
:end
pause
