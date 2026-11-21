#!/usr/bin/env bash
set -euo pipefail
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is not installed. Install it on the server and each device you want to use."
  exit 1
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not connected. Run: sudo tailscale up"
  exit 1
fi
tailscale serve --bg 8765
echo
echo "HostPanel is now available privately to devices in your Tailscale network."
echo "Use the HTTPS hostname shown below (do not enable Funnel for this admin panel):"
tailscale serve status
