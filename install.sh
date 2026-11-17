#!/bin/sh
# One-time setup on a Mac or Linux. Run from this folder:  ./install.sh   then:  ./atf
set -e
cd "$(dirname "$0")"

# Tested Pythons: 3.9 - 3.14. A brand-new Python (e.g. 3.15 in its first months) may lack ready-built packages
# (pip would then need a compiler), so it is never picked unless ATF_PYTHON (e.g. for testing) names it.
FITS='import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 14) else 1)'

# No Python on this computer? Then uv (pinned, checked against the SHA-256 below) installs a private Python 3.13
# inside this folder (.uv). Existing setups are left alone: a working .venv is kept, nothing is downloaded.
# For testing only: ATF_NO_UV=1 (skip uv, as ATF_PYTHON does), ATF_UV_BASE=<url> (where uv is downloaded from).
UV_VERSION=0.13.6
UV_BASE="${ATF_UV_BASE:-https://github.com/astral-sh/uv/releases/download}"
UV_WHY=""

# Sets UV_ASSET and UV_SHA for this computer; returns 1 (and UV_WHY) if there is no uv for it.
uv_asset() {
  case "$(uname -s)" in
    Darwin) uv_os=apple-darwin ;;
    Linux)
      uv_os=unknown-linux-gnu
      case "$(ldd --version 2>&1)" in *musl*) uv_os=unknown-linux-musl ;; esac ;;
    *) UV_WHY="uv has no download for $(uname -s)"; return 1 ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) uv_cpu=aarch64 ;;
    x86_64|amd64) uv_cpu=x86_64 ;;
    *) UV_WHY="uv has no download for $(uname -m)"; return 1 ;;
  esac
  UV_ASSET="uv-$uv_cpu-$uv_os.tar.gz"
  case "$uv_cpu-$uv_os" in
    aarch64-apple-darwin) UV_SHA=60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472 ;;
    x86_64-apple-darwin) UV_SHA=63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02 ;;
    aarch64-unknown-linux-gnu) UV_SHA=229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d ;;
    x86_64-unknown-linux-gnu) UV_SHA=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645 ;;
    aarch64-unknown-linux-musl) UV_SHA=819692e404d23e5aae6bc44c0e260f1f5492fd1910ab4693b2c650f926dbe800 ;;
    x86_64-unknown-linux-musl) UV_SHA=5d61f26b6c7213779eda90edc6971ea6b0b796bfb8d49a574c8ad69b59838548 ;;
  esac
}

# Downloads uv into .uv/bin/uv. A wrong checksum stops everything; any other problem returns 1 (and UV_WHY).
uv_get() {
  uv_asset || return 1
  if [ -x .uv/bin/uv ] && [ "$(.uv/bin/uv --version 2>/dev/null)" = "uv $UV_VERSION" ]; then return 0; fi
  echo "Getting uv $UV_VERSION (it installs a private Python for this folder)..."
  uv_tmp="$(mktemp -d)" || { UV_WHY="couldn't make a temporary folder"; return 1; }
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$uv_tmp/uv.tar.gz" "$UV_BASE/$UV_VERSION/$UV_ASSET" || { rm -rf "$uv_tmp"; UV_WHY="couldn't download uv"; return 1; }
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$uv_tmp/uv.tar.gz" "$UV_BASE/$UV_VERSION/$UV_ASSET" || { rm -rf "$uv_tmp"; UV_WHY="couldn't download uv"; return 1; }
  else
    rm -rf "$uv_tmp"; UV_WHY="neither curl nor wget is installed"; return 1
  fi
  if command -v shasum >/dev/null 2>&1; then
    uv_sum="$(shasum -a 256 "$uv_tmp/uv.tar.gz" | cut -d ' ' -f 1)"
  elif command -v sha256sum >/dev/null 2>&1; then
    uv_sum="$(sha256sum "$uv_tmp/uv.tar.gz" | cut -d ' ' -f 1)"
  else
    rm -rf "$uv_tmp"; UV_WHY="no tool to check the download (shasum or sha256sum)"; return 1
  fi
  if [ "$uv_sum" != "$UV_SHA" ]; then
    rm -rf "$uv_tmp"
    echo "The uv download did not match its expected checksum, so it was thrown away. Nothing was installed."
    echo "Try again later or on another network; to use the Python on this computer instead, run:  ATF_NO_UV=1 ./install.sh"
    exit 1
  fi
  mkdir -p .uv/bin
  if ! tar -xzf "$uv_tmp/uv.tar.gz" -C "$uv_tmp" || ! cp "$uv_tmp"/uv-*/uv .uv/bin/uv; then
    rm -rf "$uv_tmp" .uv/bin/uv; UV_WHY="couldn't unpack uv"; return 1
  fi
  chmod +x .uv/bin/uv
  rm -rf "$uv_tmp"
}

# Makes .venv with a private Python 3.13 (pip inside: the updater needs it); returns 1 (and UV_WHY) if it can't.
uv_venv() {
  uv_get || return 1
  echo "Getting Python 3.13 for this folder..."
  if ! env UV_PYTHON_INSTALL_DIR="$PWD/.uv/python" UV_CACHE_DIR="$PWD/.uv/cache" UV_NO_CONFIG=1 \
      .uv/bin/uv venv --python 3.13 --python-preference only-managed --seed .venv; then
    rm -rf .venv; UV_WHY="uv couldn't set up Python"; return 1
  fi
  if ! .venv/bin/python -c "$FITS" >/dev/null 2>&1; then
    rm -rf .venv; UV_WHY="the Python uv made doesn't run"; return 1
  fi
  # the download cache is only wasted space
  env UV_CACHE_DIR="$PWD/.uv/cache" UV_NO_CONFIG=1 .uv/bin/uv cache clean >/dev/null 2>&1 || true
  rm -rf .uv/cache
}

HAVE_VENV=0
if [ -z "${ATF_PYTHON:-}" ] && [ "${ATF_NO_UV:-}" != 1 ]; then
  if [ -d .venv ] && .venv/bin/python -c "$FITS" >/dev/null 2>&1; then
    HAVE_VENV=1
    echo "Using the existing .venv ($(.venv/bin/python --version))"
  else
    if [ -d .venv ]; then
      echo "Rebuilding the .venv folder"
      rm -rf .venv
    fi
    if uv_venv; then
      HAVE_VENV=1
      echo "Using $(.venv/bin/python --version) (private to this folder)"
    else
      echo "$UV_WHY; using the Python on this computer instead."
    fi
  fi
fi

# The Python on this computer (also what ATF_PYTHON / ATF_NO_UV and any uv trouble end up using)
if [ "$HAVE_VENV" = 0 ]; then
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
