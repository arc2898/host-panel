#!/usr/bin/env bash
set -euo pipefail
if command -v tailscale >/dev/null 2>&1; then tailscale serve reset; else echo "Tailscale is not installed."; fi
