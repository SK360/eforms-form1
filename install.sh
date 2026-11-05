#!/bin/sh
# One-time setup on a Mac or Linux. Run from this folder:  ./install.sh   then:  ./atf
set -e
cd "$(dirname "$0")"

# Tested Pythons: 3.9 - 3.14. A brand-new Python (e.g. 3.15 in its first months) may lack ready-built packages
# (pip would then need a compiler), so it is never picked unless ATF_PYTHON (e.g. for testing) names it.
FITS='import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 14) else 1)'
PY="${ATF_PYTHON:-}"
[ -n "$PY" ] || for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "$FITS" >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 to 3.14 is needed (a brand-new Python may not have all the packages yet). Install 3.13 from"
  echo "https://www.python.org/downloads/ (or accept the macOS prompt to install the command line developer"
  echo "tools; on Linux, your distribution's python3 package), then run ./install.sh again."
  exit 1
fi
echo "Using $("$PY" --version)"

# A .venv made by another Python (e.g. an earlier attempt with a newer one) is rebuilt
if [ -d .venv ] && [ "$(.venv/bin/python --version 2>/dev/null)" != "$("$PY" --version)" ]; then
  echo "Rebuilding the .venv folder for $("$PY" --version)"
  rm -rf .venv
fi
if [ ! -d .venv ] && ! "$PY" -m venv .venv; then
  rm -rf .venv
  echo
  echo "Couldn't make the .venv folder. On Debian/Ubuntu the venv module is a separate package:"
  echo "  sudo apt install python3-venv     (or python3.X-venv for the Python above), then run ./install.sh again."
  exit 1
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m playwright install chromium

# Linux: Chromium also needs some system libraries that a fresh machine may not have. Playwright installs them on
# Debian/Ubuntu (with apt, through sudo: it asks for your password); other distributions install them by hand.
STARTS='from playwright.sync_api import sync_playwright
with sync_playwright() as pw: pw.chromium.launch().close()'
if [ "$(uname -s)" = Linux ] && ! .venv/bin/python -c "$STARTS" >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    echo
    echo "Chromium needs some system libraries. Installing them (sudo may ask for your password)..."
    .venv/bin/python -m playwright install-deps chromium || true
  fi
  if ! .venv/bin/python -c "$STARTS" >/dev/null 2>&1; then
    echo
    echo "Chromium can't start: system libraries are missing. On Debian/Ubuntu run"
    echo "  sudo .venv/bin/python -m playwright install-deps chromium"
    echo "(other distributions: install Chromium's libraries with your package manager), then run ./install.sh again."
    echo "What Chromium said:"
    .venv/bin/python -c "$STARTS" 2>&1 | grep -v '^ *File \|^Traceback' | tail -n 15 || true
    exit 1
  fi
fi
mkdir -p data out forms
chmod +x atf
echo
echo "Installed. Run ./atf to set up your trust (first run) and file."
