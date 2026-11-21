from __future__ import annotations
import os
import socket
import sys
from pathlib import Path

import psutil
import uvicorn

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("HOSTPANEL_ALLOW_ROOT") != "1":
    print("\nRefusing to run HostPanel as root.")
    print("Run it as your normal user; use sudo only inside the interactive terminal when needed.")
    print("If you deliberately understand the risk, HOSTPANEL_ALLOW_ROOT=1 overrides this safeguard.\n")
    sys.exit(1)

host = os.environ.get("HOSTPANEL_BIND", "127.0.0.1")
port = int(os.environ.get("HOSTPANEL_PORT", "8765"))
print(f"\nHostPanel listening on http://127.0.0.1:{port}")
try:
    from server.app import DATA_DIR, BOOTSTRAP_PASSWORD_FILE
    print(f"Persistent HostPanel data: {DATA_DIR}")
    if BOOTSTRAP_PASSWORD_FILE.exists():
        print(f"Initial administrator password: {BOOTSTRAP_PASSWORD_FILE}")
        command = "type" if os.name == "nt" else "cat"
        print(f'Read it with: {command} "{BOOTSTRAP_PASSWORD_FILE}"')
except Exception:
    pass
if host in ("0.0.0.0", "::"):
    try:
        addresses = sorted({addr.address.split("%",1)[0] for rows in psutil.net_if_addrs().values() for addr in rows
                            if addr.family == socket.AF_INET and not addr.address.startswith(("127.", "169.254."))})
    except Exception:
        addresses = []
    for address in addresses:
        print(f"LAN access: http://{address}:{port}")
print("Administrator password: configured once and stored as a salted hash in the HostPanel data directory.")
print("Remote access: use Tailscale Serve (private HTTPS tailnet); do not expose this port publicly.")
print("Press Ctrl+C once to stop cleanly.\n")
try:
    uvicorn.run("server.app:app", host=host, port=port, log_level="info", access_log=False)
except KeyboardInterrupt:
    print("\nHostPanel stopped cleanly.")
