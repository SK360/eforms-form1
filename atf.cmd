@echo off
rem eForms Form 1 helper (Windows). ".\atf.cmd" opens the menu; ".\atf.cmd <command> ..." runs the plain CLI (src\atf.py).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Not installed yet: run .\install.cmd first.
  exit /b 1
)
rem One line with "& exit /b": cmd reads this whole line before running it and never reads on, so an update that
rem replaces this file while it runs can't make cmd run part of the new file.
.venv\Scripts\python.exe src\ui.py %* & exit /b
