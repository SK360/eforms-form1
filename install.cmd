@echo off
rem One-time setup on Windows. In this folder run:  .\install.cmd   then:  .\atf.cmd
setlocal
cd /d "%~dp0"

set "PY="
rem ATF_PYTHON (optional) picks a specific Python, e.g. for testing; otherwise the newest 3.9+ is used
if defined ATF_PYTHON set "PY=%ATF_PYTHON%"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" in the installer^), then run install.cmd again.
  goto :fail
)
for /f "delims=" %%v in ('%PY% --version') do echo Using %%v

if not exist .venv\Scripts\python.exe (
  %PY% -m venv .venv || goto :fail
)
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip || goto :fail
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || goto :fail
.venv\Scripts\python.exe -m playwright install chromium || goto :fail
if not exist data mkdir data
if not exist out mkdir out
if not exist forms mkdir forms
echo.
echo Installed. Run .\atf.cmd to set up your trust (first run) and file.
if not defined CI pause
exit /b 0

:fail
echo.
echo Install did not finish; see the messages above.
if not defined CI pause
exit /b 1
