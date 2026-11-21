#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(id -u)" == "0" && "${HOSTPANEL_ALLOW_ROOT:-0}" != "1" ]]; then
  echo "HostPanel must run as your normal user, not root. Use sudo only inside its terminal."
  exit 1
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Error: $PYTHON was not found. Install Python 3.10+ and try again."
  exit 1
fi

if [[ ! -x ".venv/bin/python" || ! -f ".venv/bin/activate" ]]; then
  if [[ -e .venv ]]; then echo "Repairing incomplete virtual environment..."; rm -rf .venv; fi
  echo "Creating Python virtual environment..."
  if ! "$PYTHON" -m venv .venv; then
    echo "Could not create the virtual environment. Debian/Ubuntu: sudo apt install python3-venv"
    exit 1
  fi
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export HOSTPANEL_BIND="${HOSTPANEL_BIND:-127.0.0.1}"
exec python start.py
