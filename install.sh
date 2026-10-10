#!/bin/sh
# One-time setup on a Mac. Run from this folder:  ./install.sh   then:  ./atf
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
  echo "tools), then run ./install.sh again."
  exit 1
fi
echo "Using $("$PY" --version)"

# A .venv made by another Python (e.g. an earlier attempt with a newer one) is rebuilt
if [ -d .venv ] && [ "$(.venv/bin/python --version 2>/dev/null)" != "$("$PY" --version)" ]; then
  echo "Rebuilding the .venv folder for $("$PY" --version)"
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m playwright install chromium
mkdir -p data out forms
chmod +x atf
echo
echo "Installed. Run ./atf to set up your trust (first run) and file."
