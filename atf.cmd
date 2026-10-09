@echo off
rem eForms Form 1 helper (Windows). ".\atf.cmd" opens the menu; ".\atf.cmd <command> ..." runs the plain CLI (src\atf.py).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Not installed yet: run .\install.cmd first.
  exit /b 1
)
.venv\Scripts\python.exe src\ui.py %*
