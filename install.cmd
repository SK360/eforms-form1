@echo off
rem One-time setup on Windows. In this folder run:  .\install.cmd   then:  .\atf.cmd
rem
rem Python: if .venv already works (Python 3.9-3.14) it is kept. Otherwise a pinned copy of uv is downloaded (checked
rem against a SHA-256 kept in tools\get-uv.ps1) and it installs a private Python 3.13 for this folder, in .uv.
rem Any problem getting uv or its Python = the Python already on this computer is used instead (the older way).
rem Testing only: ATF_PYTHON=<python> or ATF_NO_UV=1 use the older way straight away; ATF_UV_BASE=<url> replaces the
rem place uv is downloaded from.
setlocal
cd /d "%~dp0"

rem Tested Pythons: 3.9 - 3.14. A brand-new Python (e.g. 3.15 in its first months) has no ready-built PyYAML etc.
rem yet, and pip then needs a C++ compiler, so it is never picked unless ATF_PYTHON names it.
set "FITS=import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 14) else 1)"
set "PY="

if defined ATF_PYTHON goto :legacy
if "%ATF_NO_UV%"=="1" goto :legacy

rem A .venv that works and is Python 3.9-3.14 is kept, whatever made it
if exist .venv\Scripts\python.exe .venv\Scripts\python.exe -c "%FITS%" >nul 2>&1 && goto :packages
if exist .venv echo Rebuilding the .venv folder & rmdir /s /q .venv

echo Getting uv 0.13.6 (it installs a private Python for this folder)...
set "ARCH=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "ARCH=%PROCESSOR_ARCHITEW6432%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\get-uv.ps1" -Root "%CD%\." -Arch "%ARCH%"
set "RC=%errorlevel%"
if "%RC%"=="0" goto :uvready
if "%RC%"=="2" goto :uvmismatch
echo Could not get uv, so using the Python on this computer instead.
goto :legacy

:uvready
echo Getting Python 3.13 for this folder...
set "UV_PYTHON_INSTALL_DIR=%CD%\.uv\python"
set "UV_CACHE_DIR=%CD%\.uv\cache"
set "UV_NO_CONFIG=1"
"%CD%\.uv\bin\uv.exe" venv --python 3.13 --python-preference only-managed --seed .venv
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :uvfailed
if not exist .venv\Scripts\python.exe goto :uvfailed
"%CD%\.uv\bin\uv.exe" cache clean >nul 2>&1
set "UV_PYTHON_INSTALL_DIR="
set "UV_CACHE_DIR="
set "UV_NO_CONFIG="
goto :packages

:uvfailed
set "UV_PYTHON_INSTALL_DIR="
set "UV_CACHE_DIR="
set "UV_NO_CONFIG="
echo Could not get Python 3.13 with uv, so using the Python on this computer instead.
if exist .venv rmdir /s /q .venv
goto :legacy

:uvmismatch
if exist .uv\bin\uv.exe del /f /q .uv\bin\uv.exe
echo.
echo ERROR: the uv download did not match its expected checksum, so it was not used.
echo Something is wrong with the download or your network. Try again later. If it keeps happening,
echo run install.cmd with ATF_NO_UV=1 set to use a Python you installed yourself.
goto :fail

:legacy
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

:packages
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
