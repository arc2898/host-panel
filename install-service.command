#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
LABEL="com.hostpanel.server"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$HOME/Library/Logs/HostPanel"
PLIST="$AGENTS/$LABEL.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$ROOT/.venv/bin/python</string><string>$ROOT/start.py</string></array>
<key>WorkingDirectory</key><string>$ROOT</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>EnvironmentVariables</key><dict><key>HOSTPANEL_BIND</key><string>127.0.0.1</string><key>HOSTPANEL_PORT</key><string>8765</string></dict>
<key>StandardOutPath</key><string>$HOME/Library/Logs/HostPanel/hostpanel.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Logs/HostPanel/hostpanel-error.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "HostPanel installed as a LaunchAgent. It starts at user login and restarts on exit."
echo "Local URL: http://127.0.0.1:8765"
echo "Logs: $HOME/Library/Logs/HostPanel"
echo "For remote access, use Tailscale Serve: tailscale serve --bg 8765"
