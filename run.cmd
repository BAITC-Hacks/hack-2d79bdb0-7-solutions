@echo off
cd /d "%~dp0"
set "APP_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%APP_PYTHON%" (
  "%APP_PYTHON%" server.py
) else (
  python server.py
)
pause
