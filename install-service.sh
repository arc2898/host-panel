#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ "$(id -u)" == 0 ]]; then echo "Run this as your normal OS user, not root."; exit 1; fi
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "Python 3 is required."; exit 1; }
if [[ ! -x .venv/bin/python ]]; then "$PYTHON" -m venv .venv; fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
ROOT_ESC="$(printf '%s' "$ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
cat > "$UNIT_DIR/hostpanel.service" <<EOF
[Unit]
Description=HostPanel self-hosted server workspace
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory="$ROOT_ESC"
Environment=HOSTPANEL_BIND=127.0.0.1
Environment=HOSTPANEL_PORT=8765
ExecStart="$ROOT_ESC/.venv/bin/python" "$ROOT_ESC/start.py"
Restart=always
RestartSec=5
TimeoutStopSec=15
KillSignal=SIGINT

[Install]
WantedBy=default.target
EOF
if command -v loginctl >/dev/null 2>&1; then
  if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)" != "yes" ]]; then
    echo "Enabling user lingering so HostPanel starts at boot and survives logout (sudo may prompt once)..."
    if command -v sudo >/dev/null 2>&1; then
      sudo loginctl enable-linger "$USER" || echo "Could not enable linger automatically. Run: sudo loginctl enable-linger $USER"
    else
      echo "For boot-time startup after logout, run once as an administrator: sudo loginctl enable-linger $USER"
    fi
  fi
fi
systemctl --user daemon-reload
systemctl --user enable --now hostpanel.service
systemctl --user --no-pager --full status hostpanel.service || true
echo
echo "HostPanel is installed as a persistent user service. It restarts after failures and starts at boot when lingering is enabled."
echo "Local URL: http://127.0.0.1:8765"
echo "Logs: journalctl --user -u hostpanel.service -f"
echo "For private remote access, run: ./enable-tailnet.sh"
