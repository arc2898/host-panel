#!/bin/bash
set -euo pipefail
LABEL="com.hostpanel.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "HostPanel LaunchAgent removed. Application data and password were kept."
