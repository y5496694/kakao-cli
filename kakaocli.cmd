@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python environment is missing. Run install.ps1 first. 1>&2
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m kakaocli_win.cli %*
exit /b %errorlevel%
