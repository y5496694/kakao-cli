@echo off
setlocal
set PYTHONUTF8=1
if not defined KAKAOCLI_DATA_DIR set KAKAOCLI_DATA_DIR=D:\Codex\apps\kakao-cli
if exist D:\Codex\tmp set TEMP=D:\Codex\tmp
if exist D:\Codex\tmp set TMP=D:\Codex\tmp
"%~dp0.venv\Scripts\kakaocli.exe" %*
exit /b %errorlevel%
