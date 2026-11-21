#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now hostpanel.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/hostpanel.service"
systemctl --user daemon-reload
systemctl --user reset-failed hostpanel.service 2>/dev/null || true
echo "HostPanel service removed. App data, password, and project files were kept."
echo "To also stop it from starting after logout, run: sudo loginctl disable-linger $USER"
