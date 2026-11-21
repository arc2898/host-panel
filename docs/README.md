# HostPanel documentation

This directory contains the interface screenshots and supporting project documentation.

## Screenshots

These are captured views of the HostPanel desktop interface.

| Image | What it shows |
|---|---|
| [Overview](assets/overview.png) | Live CPU, memory, home-storage and network telemetry, host identity and mounted volumes |
| [File manager](assets/file-manager.png) | Home-first navigation, common folders, mounted devices and file browsing |
| [Terminal](assets/terminal.png) | Interactive shell workspace and OS-permission boundary |
| [Media studio](assets/media-studio.png) | Media library browsing and in-page audio/video playback |

The [main README](../README.md) embeds these screenshots and explains installation, persistent authentication, background services, secure remote access, file management, terminal use and media playback.

## Project layout

```text
.
├── docs/
│   ├── README.md
│   └── assets/
│       ├── overview.png
│       ├── file-manager.png
│       ├── terminal.png
│       └── media-studio.png
├── server/
│   └── app.py
├── web/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── requirements.txt
├── start.py
├── start.sh
├── start.command
└── start.bat
```

## Operational notes

- Screenshots are static captures, not interactive demos.
- HostPanel runs with the permissions of the OS account that starts it.
- Keep the HostPanel data directory private; it contains authentication material and the session-signing key.
- For remote administration, use private VPN access such as Tailscale Serve. Do not expose port 8765 directly to the public internet.

