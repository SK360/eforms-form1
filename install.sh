#!/bin/sh
# One-time setup on a Mac. Run from this folder:  ./install.sh   then:  ./atf
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/ (or accept the"
  echo "macOS prompt to install the command line developer tools), then run ./install.sh again."
  exit 1
fi
echo "Using $("$PY" --version)"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m playwright install chromium
mkdir -p data out forms
chmod +x atf
echo
echo "Installed. Run ./atf to set up your trust (first run) and file."
