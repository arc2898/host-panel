# HostPanel 0.8

A cross-platform self-hosted server workspace: live hardware metrics, background-service inventory, a Finder/Deepin-inspired file manager, mounted-volume discovery, an interactive OS terminal, and a private media library.

## Start in the foreground

- Linux: `chmod +x start.sh && ./start.sh`
- macOS: `chmod +x start.command && ./start.command`
- Windows: double-click `start.bat`

Python 3.10+ is required. The launcher creates `.venv` and installs dependencies. On Debian/Ubuntu, install `python3-venv` if needed.

## Permanent administrator password

HostPanel now creates a persistent administrator password on first run.

- On an existing installation, the old `.admin-token` is migrated as the initial password, copied to a protected `initial-password.txt` in the private HostPanel data directory, and removed from the project folder so you can still retrieve it after the upgrade.
- On a fresh install, a random initial password is stored in the same protected `initial-password.txt`. The launcher prints its path. Read it once, sign in, then change it in **Settings → Security & access**; changing the password deletes the temporary plaintext copy.
- Your chosen password is persisted as a salted PBKDF2-HMAC-SHA256 hash (600,000 iterations), never as plaintext. Changing it invalidates existing signed-in sessions.
- HostPanel keeps credentials and its session-signing key outside the project, so updating/replacing the project ZIP does not reset your login.

Persistent data locations:
- Linux: `~/.local/share/hostpanel/`
- macOS: `~/Library/Application Support/HostPanel/`
- Windows: `%LOCALAPPDATA%\HostPanel\`

Keep this directory private and back it up securely. If you delete `credentials.json` or `session.key`, you'll need to set up a new initial password and existing browser sessions will be invalidated.

## Long-running background service

HostPanel cannot run through power loss, sleep, OS shutdown, or a network outage, but its service installers keep it running in the background, restart it after process failures, and arrange automatic startup according to the platform's service manager.

### Linux (systemd user service)

Run these from the extracted project directory as your normal user:

```bash
chmod +x install-service.sh enable-tailnet.sh uninstall-service.sh
./install-service.sh
```

The installer creates `~/.config/systemd/user/hostpanel.service` with `Restart=always`. It attempts to enable systemd user lingering, which lets the user's service manager start at boot and continue after logout. This may prompt once for `sudo` to run `loginctl enable-linger <user>`. See `systemctl --user status hostpanel.service` and `journalctl --user -u hostpanel.service -f`.

Remove the service without deleting your password or data:

```bash
./uninstall-service.sh
```

### macOS (LaunchAgent)

Run `./install-service.command`. HostPanel starts when your user logs in and launchd restarts it if it exits. A LaunchAgent runs in your logged-in user session. Remove it with `./uninstall-service.command`.

### Windows (Task Scheduler)

Run PowerShell as your normal user in the project directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-service.ps1
```

It creates a limited-privilege scheduled task at user logon and a supervisor loop that restarts HostPanel after exit. Remove it with `.\uninstall-service.ps1`. Windows interactive-user tasks run after that user signs in.

## Secure access from anywhere (recommended)

HostPanel binds to `127.0.0.1` by default, so it is not directly exposed to your LAN or the public internet. For access from your own devices while away from home, use **Tailscale Serve**:

1. Install Tailscale on the HostPanel machine and on each phone/computer you will use.
2. Sign in to the same private tailnet on each device.
3. On Linux, run `./enable-tailnet.sh`; on macOS/Windows, run `tailscale serve --bg 8765` in a terminal.
4. Open the HTTPS tailnet hostname shown by `tailscale serve status` from a connected device, then sign in using your HostPanel password.

Tailscale Serve proxies the local HostPanel service over HTTPS to devices in your tailnet; it is not public access. Keep Tailscale Funnel disabled. If the host is offline, asleep, or disconnected from Tailscale, it cannot be reached. Tailscale Serve requires HTTPS certificates to be enabled for your tailnet and obeys your tailnet access-control rules. Official docs: https://tailscale.com/docs/features/tailscale-serve

For a plain LAN-only setup, you can opt in to listening on all interfaces with `HOSTPANEL_BIND=0.0.0.0 ./start.sh`, then restrict the firewall to your trusted subnet. This uses HTTP; do not use it across untrusted networks. Do not port-forward 8765 directly to the internet.

## Sessions and security

The password persists until you change it. The browser can remember a signed-in session for 30 days (unchecked: 12 hours); password change invalidates prior sessions. Cookies are HttpOnly, SameSite=Strict and Secure when HTTPS is detected. Login attempts are rate-limited. WebSocket terminal/metrics access uses the signed session cookie and same-origin checks.

The administrator password grants full HostPanel access, including terminal commands as the OS account running HostPanel and file operations allowed to that account. Run HostPanel as your regular account, never as root. `sudo` inside the terminal uses the operating system's own prompt; HostPanel does not capture or store sudo passwords.

## File manager, terminal and media

- Default browsing root is the current OS user's home folder.
- Mounted data volumes are listed separately; Snap loop devices and kernel pseudo-filesystems are filtered.
- Filesystem operations stay within the home folder and allowed mounted data volumes, subject to OS permissions.
- Linux/macOS use a real PTY and interactive login shells. Zsh, Bash and Fish startup files load normally.
- The browser terminal loads xterm.js and MesloLGS NF (Powerlevel10k-compatible glyph font) from jsDelivr. The content security policy explicitly permits that font CDN. Browsers need CDN access; if blocked, shell prompt icons may not display correctly. Only regular and bold font weights are preloaded to reduce bandwidth.
- Audio/video streaming supports HTTP byte ranges. Image and FFmpeg video thumbnails are cached; install FFmpeg on the host for video thumbnails.

## Notes

No process can run for literally unlimited time: hardware power, sleep, OS updates, network connectivity and service-manager failures remain external dependencies. The service installers provide automatic restart and startup, not a guarantee against host downtime.
