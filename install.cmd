@echo off
rem One-time setup on Windows. In this folder run:  .\install.cmd   then:  .\atf.cmd
setlocal
cd /d "%~dp0"

rem Tested Pythons: 3.9 - 3.14. A brand-new Python (e.g. 3.15 in its first months) has no ready-built PyYAML etc.
rem yet, and pip then needs a C++ compiler, so it is never picked unless ATF_PYTHON names it.
set "FITS=import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 14) else 1)"
set "PY="
if defined ATF_PYTHON set "PY=%ATF_PYTHON%"
for %%v in (3.13 3.14 3.12 3.11 3.10 3.9) do if not defined PY call :trypy %%v
if not defined PY python -c "%FITS%" >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython

set "WANT="
set "HAVE="
for /f "delims=" %%v in ('%PY% --version') do set "WANT=%%v"
if exist .venv\Scripts\python.exe for /f "delims=" %%v in ('.venv\Scripts\python.exe --version 2^>nul') do set "HAVE=%%v"
echo Using %WANT%
rem A .venv made by another Python (e.g. an earlier attempt with 3.15) is rebuilt
if exist .venv if not "%HAVE%"=="%WANT%" echo Rebuilding the .venv folder for %WANT% & rmdir /s /q .venv

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

rem Use "py -<version>" only if the py launcher lists that version (so it never offers to download one)
:trypy
py -0 2>nul | findstr /l /c:"%1" >nul || exit /b 0
py -%1 -c "%FITS%" >nul 2>&1 && set "PY=py -%1"
exit /b 0

:nopython
set "NEWER="
python -c "import sys; sys.exit(0 if sys.version_info[:2] > (3, 14) else 1)" >nul 2>&1 && set "NEWER=1"
py -0 2>nul | findstr /r /c:"3\.1[5-9]" >nul && set "NEWER=1"
if defined NEWER goto :toonew
echo Python 3.9 to 3.14 is needed. Install Python 3.13 from https://www.python.org/downloads/windows/
echo and tick "Add python.exe to PATH" in the installer, then run install.cmd again.
goto :fail

:toonew
echo Your Python is newer than this tool supports yet: brand-new versions like 3.15 do not have
echo ready-built copies of all the packages it uses. Install Python 3.13 next to it; your other Python stays.
echo   Either run:   py install 3.13
echo   or download 3.13 from https://www.python.org/downloads/windows/
echo Then run install.cmd again. It picks 3.13 by itself.
goto :fail

:fail
echo.
echo Install did not finish; see the messages above.
if not defined CI pause
exit /b 1
