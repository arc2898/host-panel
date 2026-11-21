from __future__ import annotations

import asyncio
import json
import mimetypes
import hashlib
import os
import platform
try:
    import pwd
except ImportError:  # Windows
    pwd = None
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import psutil
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LEGACY_TOKEN_FILE = ROOT / ".admin-token"
SESSION_COOKIE = "hostpanel_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # Persistent sign-in cookie: 30 days.
SHORT_SESSION_MAX_AGE = 12 * 60 * 60
PASSWORD_ITERATIONS = 600_000
MAX_UPLOAD_BYTES = int(os.environ.get("HOSTPANEL_MAX_UPLOAD_MB", "512")) * 1024 * 1024
MAX_TERMINAL_SESSIONS = 4


def _app_data_dir() -> Path:
    override = os.environ.get("HOSTPANEL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "HostPanel"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "HostPanel"
    return Path.home() / ".local" / "share" / "hostpanel"


DATA_DIR = _app_data_dir()
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
SIGNING_KEY_FILE = DATA_DIR / "session.key"
BOOTSTRAP_PASSWORD_FILE = DATA_DIR / "initial-password.txt"


def _write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(content, encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    os.replace(temp, path)
    try:
        path.chmod(0o600)
        if os.name != "nt":
            path.parent.chmod(0o700)
    except OSError:
        pass


def _password_hash(password: str, salt: bytes, iterations: int = PASSWORD_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _load_or_create_credentials() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass
    if CREDENTIALS_FILE.exists():
        try:
            CREDENTIALS_FILE.chmod(0o600)
        except OSError:
            pass
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            if data.get("salt") and data.get("password_hash"):
                return data
        except (OSError, ValueError, TypeError):
            raise RuntimeError(f"Cannot read HostPanel credentials at {CREDENTIALS_FILE}; restore the file or remove it to create a new initial password.")

    # Upgrade existing installations without changing the credential: their old
    # administrator token becomes the initial password, stored only as a hash.
    initial = os.environ.get("HOSTPANEL_TOKEN", "").strip()
    legacy_used = False
    if not initial and LEGACY_TOKEN_FILE.exists():
        initial = LEGACY_TOKEN_FILE.read_text(encoding="utf-8").strip()
        legacy_used = bool(initial)
    generated = False
    if not initial:
        initial = secrets.token_urlsafe(24)
        generated = True
    salt = secrets.token_bytes(32)
    data = {"salt": salt.hex(), "password_hash": _password_hash(initial, salt),
            "iterations": PASSWORD_ITERATIONS, "version": 1, "created_at": int(time.time())}
    _write_private_file(CREDENTIALS_FILE, json.dumps(data, indent=2) + "\n")
    if generated:
        _write_private_file(BOOTSTRAP_PASSWORD_FILE, initial + "\n")
        print(f"\nHostPanel initial password saved to: {BOOTSTRAP_PASSWORD_FILE}")
        print("Read it once, sign in, then change it in Settings → Security.\n")
    elif legacy_used:
        # Preserve access for upgrades: move the old token into the protected
        # bootstrap-secret file until the user chooses a new password.
        _write_private_file(BOOTSTRAP_PASSWORD_FILE, initial + "\n")
        print("\nHostPanel migrated your existing .admin-token as the initial password.")
        print(f"Read the protected temporary copy at: {BOOTSTRAP_PASSWORD_FILE}")
        print("The old project token file is removed; change your password in Settings → Security.\n")
        try:
            LEGACY_TOKEN_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return data


def _verify_password(password: str) -> bool:
    try:
        salt = bytes.fromhex(CREDENTIALS["salt"])
        iterations = int(CREDENTIALS.get("iterations", PASSWORD_ITERATIONS))
        expected = str(CREDENTIALS["password_hash"])
        actual = _password_hash(password, salt, iterations)
        return secrets.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


def _save_password(password: str) -> None:
    salt = secrets.token_bytes(32)
    CREDENTIALS.update({"salt": salt.hex(), "password_hash": _password_hash(password, salt),
                        "iterations": PASSWORD_ITERATIONS, "version": int(CREDENTIALS.get("version", 1)) + 1,
                        "updated_at": int(time.time())})
    _write_private_file(CREDENTIALS_FILE, json.dumps(CREDENTIALS, indent=2) + "\n")
    try:
        BOOTSTRAP_PASSWORD_FILE.unlink(missing_ok=True)
        LEGACY_TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


if not SIGNING_KEY_FILE.exists():
    _write_private_file(SIGNING_KEY_FILE, secrets.token_urlsafe(48) + "\n")
try:
    SIGNING_KEY_FILE.chmod(0o600)
    SESSION_SECRET = SIGNING_KEY_FILE.read_text(encoding="utf-8").strip()
except OSError as exc:
    raise RuntimeError(f"Cannot read persistent HostPanel session key: {exc}") from exc
if len(SESSION_SECRET) < 32:
    raise RuntimeError("HostPanel session key is invalid; remove the key file to regenerate it.")
CREDENTIALS = _load_or_create_credentials()
SESSION_SIGNER = URLSafeTimedSerializer(SESSION_SECRET, salt="hostpanel-session-v2")

app = FastAPI(title="HostPanel", version="0.8.0", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=WEB), name="assets")

_login_failures: dict[str, list[float]] = {}
_terminal_pids: set[int] = set()


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    remember: bool = True


class PasswordChangeBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=255)


class RenameBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=255)


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class NewFileBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=262144)


def _valid_session(cookie: Optional[str]) -> bool:
    if not cookie:
        return False
    try:
        data = SESSION_SIGNER.loads(cookie, max_age=SESSION_MAX_AGE)
        return (data.get("role") == "admin" and
                int(data.get("exp", 0)) >= int(time.time()) and
                int(data.get("version", -1)) == int(CREDENTIALS.get("version", 1)))
    except (BadSignature, SignatureExpired, TypeError, ValueError, AttributeError):
        return False


def _request_is_authenticated(request: Request) -> bool:
    return _valid_session(request.cookies.get(SESSION_COOKIE))


def _set_session_cookie(response: JSONResponse, request: Request, max_age: int) -> None:
    expires = int(time.time()) + max_age
    response.set_cookie(
        SESSION_COOKIE,
        SESSION_SIGNER.dumps({"role": "admin", "exp": expires, "version": int(CREDENTIALS.get("version", 1))}),
        max_age=max_age,
        httponly=True,
        secure=(request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"),
        samesite="strict",
        path="/",
    )


def require_auth(request: Request) -> None:
    if not _request_is_authenticated(request):
        raise HTTPException(status_code=401, detail="Session expired or not authenticated")


def _origin_matches_headers(headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    # Reverse proxies such as Tailscale Serve may preserve the browser's host
    # in X-Forwarded-Host while the upstream Host is localhost:8765.
    allowed_hosts = {headers.get("host", "").lower(), headers.get("x-forwarded-host", "").lower()}
    allowed_hosts.discard("")
    return bool(origin_host and origin_host in allowed_hosts)


def require_same_origin(request: Request) -> None:
    if not _origin_matches_headers(request.headers):
        raise HTTPException(status_code=403, detail="Cross-origin request blocked")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; font-src 'self' data: https://cdn.jsdelivr.net; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    require_same_origin(request)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [stamp for stamp in _login_failures.get(ip, []) if now - stamp < 300]
    if len(recent) >= 8:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Wait five minutes and try again.")
    if not _verify_password(body.password):
        recent.append(now)
        _login_failures[ip] = recent
        raise HTTPException(status_code=401, detail="Incorrect administrator password")
    _login_failures.pop(ip, None)
    max_age = SESSION_MAX_AGE if body.remember else SHORT_SESSION_MAX_AGE
    response = JSONResponse({"ok": True, "expires_in": max_age})
    _set_session_cookie(response, request, max_age)
    return response


@app.post("/api/auth/password")
def change_password(body: PasswordChangeBody, request: Request):
    require_same_origin(request)
    require_auth(request)
    if not _verify_password(body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Choose a different password")
    _save_password(body.new_password)
    response = JSONResponse({"ok": True, "message": "Administrator password updated", "expires_in": SESSION_MAX_AGE})
    _set_session_cookie(response, request, SESSION_MAX_AGE)
    return response


@app.post("/api/auth/logout")
def logout(request: Request):
    require_same_origin(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return response


@app.get("/api/auth/session")
def session(request: Request):
    require_auth(request)
    try:
        payload = SESSION_SIGNER.loads(request.cookies.get(SESSION_COOKIE, ""), max_age=SESSION_MAX_AGE)
        remaining = max(0, int(payload.get("exp", 0)) - int(time.time()))
    except (BadSignature, SignatureExpired, TypeError, ValueError, AttributeError):
        remaining = 0
    return {"authenticated": True, "expires_in": remaining}


def configured_home() -> Path:
    return Path.home().resolve()


def _is_linux_system_mount(mount: Path) -> bool:
    value = str(mount)
    # UDisks commonly mounts removable media below /run/media/<user>.
    if value.startswith("/run/media/"):
        return False
    blocked = ("/proc", "/sys", "/dev", "/run", "/boot", "/etc", "/usr", "/var", "/opt", "/root", "/lib", "/lib64", "/snap")
    return value == "/" or any(value == item or value.startswith(item + "/") for item in blocked)


def _is_macos_system_mount(mount: Path) -> bool:
    value = str(mount)
    blocked = ("/System/Volumes", "/private/var", "/private/tmp", "/private/etc")
    return value == "/" or any(value == item or value.startswith(item + "/") for item in blocked)


LINUX_VIRTUAL_FILESYSTEMS = {
    "squashfs", "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2",
    "overlay", "nsfs", "tracefs", "debugfs", "securityfs", "pstore", "bpf",
    "fusectl", "mqueue", "hugetlbfs", "rpc_pipefs", "autofs",
}


def _is_linux_virtual_mount(mount: Path, filesystem: str = "", device: str = "", kind: str = "") -> bool:
    value = str(mount)
    fs = (filesystem or "").casefold()
    dev = (device or "").casefold()
    item_type = (kind or "").casefold()
    if fs in LINUX_VIRTUAL_FILESYSTEMS or item_type == "loop" or dev.startswith("/dev/loop"):
        return True
    # /run/media is a legitimate automount location on many Linux desktops;
    # hide other volatile /run mounts but keep mounted USB/data drives there.
    if value.startswith("/run/media/"):
        return False
    virtual_prefixes = (
        "/snap", "/var/lib/snapd", "/proc", "/sys", "/dev", "/run",
        "/boot", "/etc", "/usr", "/var", "/opt", "/root", "/lib", "/lib64",
    )
    return any(value == item or value.startswith(item + "/") for item in virtual_prefixes)


def _lsblk_records() -> list[dict]:
    if platform.system() != "Linux" or not shutil.which("lsblk"):
        return []
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS,RM,RO"],
            capture_output=True, text=True, timeout=4, check=False,
        )
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    records: list[dict] = []

    def visit(items):
        for item in items or []:
            kind = item.get("type") or ""
            device = item.get("path") or item.get("name") or ""
            filesystem = item.get("fstype") or ""
            mounts = item.get("mountpoints") or ([item["mountpoint"]] if item.get("mountpoint") else [])
            for mount in mounts:
                if not mount:
                    continue
                resolved_mount = Path(mount).resolve()
                # Snap applications are mounted as squashfs loop devices. They are not
                # user data volumes and must not pollute the Devices section.
                if _is_linux_virtual_mount(resolved_mount, filesystem, device, kind) and resolved_mount != Path("/"):
                    continue
                records.append({
                    "device": device, "mountpoint": str(resolved_mount),
                    "fstype": filesystem, "label": item.get("label") or "",
                    "size": int(item.get("size") or 0), "removable": bool(item.get("rm")),
                    "readonly": bool(item.get("ro")), "type": kind,
                })
            visit(item.get("children"))

    visit(data.get("blockdevices"))
    return records


def _volume_label(mount: Path, device: str, label: str = "") -> str:
    if label and label.strip():
        return label.strip()
    if mount == Path("/"):
        return "System volume"
    # Prefer a friendly mount directory (e.g. /media/arc/Backup -> Backup)
    if mount.name and not mount.name.isdigit():
        return mount.name.replace("_", " ").replace("-", " ").title()
    return Path(device).name or "Mounted volume"


def volume_records() -> list[dict]:
    system = platform.system()
    home = configured_home()
    records: dict[str, dict] = {}
    if system == "Linux":
        for item in _lsblk_records():
            mount = Path(item["mountpoint"]).resolve()
            if not mount.exists():
                continue
            if mount != Path("/") and _is_linux_virtual_mount(mount, item.get("fstype", ""), item.get("device", ""), item.get("type", "")):
                continue
            # Do not expose a parent /home partition: the account's home folder is
            # already a safe root and sibling accounts should not be listed.
            is_home_parent = home != mount and mount in home.parents
            if is_home_parent:
                continue
            is_system = mount == Path("/") or _is_linux_system_mount(mount)
            browseable = not is_system
            try:
                usage = psutil.disk_usage(str(mount))
                total, used, free, percent = usage.total, usage.used, usage.free, usage.percent
            except OSError:
                total = int(item.get("size") or 0); used = free = percent = 0
            records[str(mount)] = {
                "device": item["device"], "mountpoint": str(mount), "filesystem": item["fstype"],
                "label": _volume_label(mount, item["device"], item.get("label", "")), "total": total,
                "used": used, "free": free, "percent": percent, "removable": item["removable"],
                "readonly": item["readonly"], "browseable": browseable, "system": is_system,
            }
        # psutil is a fallback for filesystems not reported by lsblk (for example
        # FUSE/NTFS mounts). Apply exactly the same virtual-mount filtering.
        try:
            fallback_parts = psutil.disk_partitions(all=False)
        except Exception:
            fallback_parts = []
        for part in fallback_parts:
            try:
                mount = Path(part.mountpoint).resolve()
                if not mount.exists() or str(mount) in records:
                    continue
                if mount != Path("/") and _is_linux_virtual_mount(mount, part.fstype, part.device):
                    continue
                if mount != Path("/") and _is_linux_system_mount(mount):
                    continue
                is_home_parent = home != mount and mount in home.parents
                if is_home_parent:
                    continue
                is_system = mount == Path("/")
                usage = psutil.disk_usage(str(mount))
                records[str(mount)] = {
                    "device": part.device, "mountpoint": str(mount), "filesystem": part.fstype,
                    "label": _volume_label(mount, part.device), "total": usage.total,
                    "used": usage.used, "free": usage.free, "percent": usage.percent,
                    "removable": str(mount).startswith(("/media/", "/run/media/")),
                    "readonly": "ro" in part.opts.split(","), "browseable": not is_system,
                    "system": is_system,
                }
            except OSError:
                continue
    elif system == "Darwin":
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            partitions = []
        for part in partitions:
            try:
                mount = Path(part.mountpoint).resolve()
                if not mount.exists() or (mount != Path("/") and _is_macos_system_mount(mount)):
                    continue
                is_home_parent = home != mount and mount in home.parents
                if is_home_parent:
                    continue
                is_system = mount == Path("/")
                usage = psutil.disk_usage(str(mount))
                records[str(mount)] = {
                    "device": part.device, "mountpoint": str(mount), "filesystem": part.fstype,
                    "label": _volume_label(mount, part.device), "total": usage.total, "used": usage.used,
                    "free": usage.free, "percent": usage.percent,
                    "removable": str(mount).startswith("/Volumes/"),
                    "readonly": "ro" in part.opts.split(","), "browseable": not is_system,
                    "system": is_system,
                }
            except OSError:
                continue
    elif system == "Windows":
        for part in psutil.disk_partitions(all=False):
            try:
                mount = Path(part.mountpoint).resolve()
                usage = psutil.disk_usage(str(mount))
                drive = str(mount).rstrip("\\/")
                system_anchor = str(Path(home.anchor).resolve()).casefold() if home.anchor else ""
                is_system = bool(system_anchor and str(mount).casefold() == system_anchor)
                records[str(mount)] = {
                    "device": part.device, "mountpoint": str(mount), "filesystem": part.fstype,
                    "label": "System drive" if is_system else (drive or "Mounted volume"),
                    "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent,
                    "removable": "removable" in part.opts.casefold(),
                    "readonly": "ro" in part.opts.split(","), "browseable": not is_system, "system": is_system,
                }
            except OSError:
                continue
    # Keep one restricted system-volume entry, never every internal OS mount.
    if system in {"Linux", "Darwin"} and "/" not in records:
        try:
            usage = psutil.disk_usage("/")
            records["/"] = {
                "device": "System", "mountpoint": "/", "filesystem": "system", "label": "System volume",
                "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent,
                "removable": False, "readonly": False, "browseable": False, "system": True,
            }
        except OSError:
            pass
    # Home is always present as a friendly entry even if it shares the system disk.
    try:
        usage = psutil.disk_usage(str(home))
        if str(home) in records:
            records[str(home)].update({"label": "Home directory", "browseable": True, "system": False})
        else:
            records[str(home)] = {
                "device": "Home", "mountpoint": str(home), "filesystem": "home", "label": "Home directory",
                "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent,
                "removable": False, "readonly": False, "browseable": True, "system": False,
            }
    except OSError:
        pass
    # Home first, then mounted data drives, then the single restricted OS volume.
    return sorted(records.values(), key=lambda v: (
        0 if v["mountpoint"] == str(home) else 2 if v["system"] else 1,
        v["label"].casefold(), v["mountpoint"].casefold(),
    ))


def allowed_roots() -> list[Path]:
    roots = [configured_home()]
    for volume in volume_records():
        if volume["browseable"]:
            p = Path(volume["mountpoint"]).resolve()
            if p not in roots:
                roots.append(p)
    return roots


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_user_path(value: Optional[str], *, must_exist: bool = True) -> Path:
    text = (value or str(configured_home())).strip()
    raw = Path(text).expanduser()
    if not raw.is_absolute():
        raw = configured_home() / raw
    try:
        resolved = raw.resolve(strict=must_exist)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Path does not exist")
    roots = allowed_roots()
    if not any(_under(resolved, root) for root in roots):
        raise HTTPException(status_code=403, detail="This location is outside your home folder and mounted volumes")
    if must_exist and not resolved.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    return resolved


def resolve_user_entry(value: str) -> Path:
    """Resolve an item while preserving its final symlink, but validate its target."""
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = configured_home() / raw
    parent = resolve_user_path(str(raw.parent))
    candidate = parent / raw.name
    try:
        resolved_target = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Item not found")
    if not any(_under(resolved_target, root) for root in allowed_roots()):
        raise HTTPException(status_code=403, detail="This item resolves outside allowed locations")
    return candidate


def _safe_name(name: str) -> str:
    if name in {"", ".", ".."} or "\x00" in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="File name is too long")
    return name



def interactive_shell_args(executable: str) -> list[str]:
    """Arguments that start a login shell in interactive mode so rc/themes load."""
    shell_name = Path(executable).name.casefold()
    if shell_name.startswith("zsh"):
        return ["-l", "-i"]
    if shell_name.startswith("bash"):
        return ["--login", "-i"]
    if shell_name.startswith("fish"):
        return ["--login", "--interactive"]
    return ["-i"]

def available_shells() -> list[dict]:
    system = platform.system()
    candidates: list[tuple[str, Optional[str]]] = []
    if system == "Windows":
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        cmd = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if pwsh: candidates.append(("PowerShell 7", pwsh))
        if powershell and powershell != pwsh: candidates.append(("Windows PowerShell", powershell))
        if cmd: candidates.append(("Command Prompt", cmd))
    else:
        preferred = os.environ.get("SHELL")
        try:
            configured = pwd.getpwuid(os.getuid()).pw_shell if pwd else None
        except Exception:
            configured = None
        preferred = preferred or configured
        if preferred and Path(preferred).is_file():
            candidates.append((f"Default · {Path(preferred).name}", preferred))
        for name in ("zsh", "bash", "fish", "sh"):
            executable = shutil.which(name)
            if executable:
                candidates.append((name, executable))
    found = []
    seen = set()
    for name, executable in candidates:
        if executable and executable not in seen and Path(executable).exists():
            seen.add(executable)
            found.append({"name": name, "path": executable})
    return found


def _locations() -> list[dict]:
    home = configured_home()
    names = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]
    result = [{"name": "Home", "path": str(home), "icon": "home", "exists": True}]
    for name in names:
        path = home / name
        result.append({"name": name, "path": str(path), "icon": name.lower(), "exists": path.is_dir()})
    return result


@app.get("/api/info")
def info(request: Request):
    require_auth(request)
    vm = psutil.virtual_memory()
    uname = platform.uname()
    try:
        user = pwd.getpwuid(os.getuid()).pw_name if pwd and os.name != "nt" else os.environ.get("USERNAME", "user")
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return {
        "host": socket.gethostname(), "os": platform.system(), "release": platform.release(),
        "arch": platform.machine(), "cpu": psutil.cpu_count(logical=True),
        "cpu_physical": psutil.cpu_count(logical=False), "ram_total": vm.total,
        "python": platform.python_version(), "uptime": time.time() - psutil.boot_time(),
        "home": str(configured_home()), "user": user, "shells": available_shells(),
        "locations": _locations(), "volumes": volume_records(),
        "media_root": str(configured_home()), "security": {"session_minutes": SESSION_MAX_AGE // 60,
            "file_scope": "home and mounted volumes", "terminal_user": user},
    }


@app.get("/api/volumes")
def volumes(request: Request):
    require_auth(request)
    return volume_records()


@app.get("/api/metrics")
def metrics(request: Request):
    require_auth(request)
    vm = psutil.virtual_memory(); sw = psutil.swap_memory()
    home = configured_home()
    try: du = psutil.disk_usage(str(home))
    except OSError: du = psutil.disk_usage(str(home.anchor or "/"))
    net = psutil.net_io_counters()
    try:
        temperatures = psutil.sensors_temperatures()
        temp = [{"name": k, "current": v.current} for k, values in temperatures.items() for v in values if v.current is not None]
    except Exception:
        temp = []
    return {"time": time.time(), "cpu": psutil.cpu_percent(interval=None), "ram": vm.percent,
        "ram_used": vm.used, "ram_total": vm.total, "swap": sw.percent, "disk": du.percent,
        "disk_used": du.used, "disk_total": du.total, "net_sent": net.bytes_sent,
        "net_recv": net.bytes_recv, "temperature": temp}


@app.get("/api/services")
def services(request: Request):
    require_auth(request); out = []
    if platform.system() == "Windows":
        try:
            for service in psutil.win_service_iter():
                try:
                    item = service.as_dict()
                    out.append({"name": item.get("name"), "display": item.get("display_name"), "status": item.get("status")})
                except Exception:
                    continue
        except Exception:
            pass
    elif platform.system() == "Linux":
        if shutil.which("systemctl"):
            try:
                p = subprocess.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
                    capture_output=True, text=True, timeout=5, check=False)
                for line in p.stdout.splitlines():
                    fields = line.split(None, 4)
                    if len(fields) >= 4:
                        out.append({"name": fields[0], "status": fields[3], "display": fields[4] if len(fields) > 4 else ""})
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        try:
            p = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5, check=False)
            for line in p.stdout.splitlines()[1:]:
                fields = line.split(None, 2)
                if len(fields) == 3:
                    out.append({"name": fields[2], "status": "loaded", "display": fields[2]})
        except (OSError, subprocess.SubprocessError):
            pass
    return out[:800]


@app.get("/api/files")
def files(request: Request, path: Optional[str] = None, hidden: bool = False):
    require_auth(request)
    folder = resolve_user_path(path)
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="This path is not a folder")
    items = []
    try:
        children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        for entry in children[:1500]:
            if not hidden and entry.name.startswith("."):
                continue
            try:
                resolved = entry.resolve(strict=True)
                if not any(_under(resolved, root) for root in allowed_roots()):
                    continue
                stat = entry.lstat()
                is_symlink = entry.is_symlink()
                is_dir = resolved.is_dir()
                items.append({"name": entry.name, "path": str(entry), "directory": is_dir,
                    "symlink": is_symlink, "size": stat.st_size, "modified": stat.st_mtime,
                    "extension": entry.suffix.lower()})
            except (PermissionError, OSError, RuntimeError):
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    parent = str(folder.parent) if any(_under(folder.parent, root) for root in allowed_roots()) else None
    return {"path": str(folder), "parent": parent, "home": str(configured_home()), "items": items}


@app.post("/api/files/folder")
def create_folder(body: FolderBody, request: Request):
    require_auth(request); require_same_origin(request)
    parent = resolve_user_path(body.path)
    if not parent.is_dir(): raise HTTPException(status_code=400, detail="Target is not a folder")
    name = _safe_name(body.name); target = (parent / name).resolve(strict=False)
    if not any(_under(target, root) for root in allowed_roots()): raise HTTPException(status_code=403, detail="Target outside allowed locations")
    try: target.mkdir()
    except FileExistsError: raise HTTPException(status_code=409, detail="An item with that name already exists")
    except PermissionError: raise HTTPException(status_code=403, detail="Permission denied")
    return {"ok": True, "path": str(target)}


@app.post("/api/files/file")
def create_file(body: NewFileBody, request: Request):
    """Create a new text file without following an existing symlink or overwriting."""
    require_auth(request)
    require_same_origin(request)
    parent = resolve_user_path(body.path)
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="Target is not a folder")
    name = _safe_name(body.name)
    target = parent / name
    content = body.content.encode("utf-8")
    if len(content) > 262144:
        raise HTTPException(status_code=413, detail="New file content is limited to 256 KB")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="An item with that name already exists")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not create file: {exc.strerror or 'OS error'}")
    return {"ok": True, "path": str(target), "name": name, "size": len(content)}


@app.post("/api/files/rename")
def rename_item(body: RenameBody, request: Request):
    require_auth(request); require_same_origin(request)
    source = resolve_user_entry(body.path)
    name = _safe_name(body.name)
    target = (source.parent / name).resolve(strict=False)
    if not any(_under(source.parent, root) for root in allowed_roots()) or not any(_under(target, root) for root in allowed_roots()):
        raise HTTPException(status_code=403, detail="Target outside allowed locations")
    if target.exists() or target.is_symlink(): raise HTTPException(status_code=409, detail="An item with that name already exists")
    try: source.rename(target)
    except PermissionError: raise HTTPException(status_code=403, detail="Permission denied")
    return {"ok": True, "path": str(target)}


@app.post("/api/files/delete")
def delete_item(body: PathBody, request: Request):
    require_auth(request); require_same_origin(request)
    target = resolve_user_entry(body.path)
    if target.resolve() == configured_home(): raise HTTPException(status_code=403, detail="Your home folder cannot be deleted")
    # Never recursively remove directories from the web UI.
    try:
        if target.is_symlink(): target.unlink()
        elif target.is_dir(): target.rmdir()
        else: target.unlink()
    except OSError as exc:
        if isinstance(exc, PermissionError): raise HTTPException(status_code=403, detail="Permission denied")
        raise HTTPException(status_code=409, detail="Folder is not empty or the item is in use")
    return {"ok": True}


@app.post("/api/files/upload")
async def upload_file(request: Request, path: str, file: UploadFile = File(...)):
    require_auth(request); require_same_origin(request)
    folder = resolve_user_path(path)
    if not folder.is_dir(): raise HTTPException(status_code=400, detail="Target is not a folder")
    name = _safe_name(Path(file.filename or "upload.bin").name)
    target = folder / name
    # O_EXCL prevents overwriting a file that appears between the check and write.
    if target.exists(): raise HTTPException(status_code=409, detail="An item with that name already exists")
    total = 0
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as dest:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
                dest.write(chunk)
    except Exception:
        try: target.unlink(missing_ok=True)
        except OSError: pass
        raise
    finally:
        await file.close()
    return {"ok": True, "name": name, "size": total, "path": str(target)}


@app.get("/api/file")
def get_file(request: Request, path: str, preview: bool = False):
    require_auth(request)
    target = resolve_user_path(path)
    if not target.is_file(): raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=target.name,
        content_disposition_type="inline" if preview else "attachment")


THUMBNAIL_CACHE = Path(tempfile.gettempdir()) / (
    "hostpanel-thumbnails-" + hashlib.sha256(str(configured_home()).encode("utf-8", "replace")).hexdigest()[:14]
)
IMAGE_THUMB_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic"}
VIDEO_THUMB_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mpeg", ".mpg"}


def _thumbnail_cache_file(target: Path) -> Path:
    stat = target.stat()
    material = f"{target.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8", "replace")
    key = hashlib.sha256(material).hexdigest()
    THUMBNAIL_CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        THUMBNAIL_CACHE.chmod(0o700)
    except OSError:
        pass
    return THUMBNAIL_CACHE / f"{key}.jpg"


def _make_image_thumbnail(target: Path, destination: Path) -> bool:
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 80_000_000
        with Image.open(target) as source:
            source = ImageOps.exif_transpose(source)
            source.thumbnail((480, 320), Image.Resampling.LANCZOS)
            if source.mode in ("RGBA", "LA") or "transparency" in source.info:
                background = Image.new("RGB", source.size, (28, 32, 45))
                rgba = source.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = background
            else:
                output = source.convert("RGB")
            output.save(destination, "JPEG", quality=80, optimize=True)
        return True
    except Exception:
        destination.unlink(missing_ok=True)
        return False


def _make_video_thumbnail(target: Path, destination: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    temp_output = destination.with_suffix(".tmp.jpg")
    for seek in ("00:00:01", "00:00:00"):
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", seek, "-i", str(target),
                 "-frames:v", "1", "-vf", "scale=480:320:force_original_aspect_ratio=decrease",
                 "-q:v", "4", "-y", str(temp_output)],
                capture_output=True, timeout=12, check=False,
            )
            if result.returncode == 0 and temp_output.exists() and temp_output.stat().st_size:
                temp_output.replace(destination)
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    temp_output.unlink(missing_ok=True)
    return False


@app.get("/api/thumbnail")
def get_thumbnail(request: Request, path: str):
    require_auth(request)
    target = resolve_user_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = target.suffix.casefold()
    if suffix not in IMAGE_THUMB_EXTENSIONS | VIDEO_THUMB_EXTENSIONS:
        raise HTTPException(status_code=415, detail="No thumbnail is available for this file type")
    try:
        cached = _thumbnail_cache_file(target)
    except OSError:
        raise HTTPException(status_code=404, detail="File is not available")
    if not cached.exists():
        pending = cached.with_name(cached.stem + f".{os.getpid()}.pending.jpg")
        try:
            created = _make_image_thumbnail(target, pending) if suffix in IMAGE_THUMB_EXTENSIONS else _make_video_thumbnail(target, pending)
            if not created:
                raise HTTPException(status_code=415, detail="Thumbnail could not be generated. Video previews need FFmpeg.")
            pending.replace(cached)
        finally:
            pending.unlink(missing_ok=True)
    return FileResponse(cached, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", "Vary": "Cookie"})


MEDIA_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac", ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
MEDIA_TYPES = {".mp3":"audio/mpeg", ".m4a":"audio/mp4", ".aac":"audio/aac", ".ogg":"audio/ogg", ".wav":"audio/wav", ".flac":"audio/flac", ".mp4":"video/mp4", ".m4v":"video/mp4", ".mov":"video/quicktime", ".webm":"video/webm", ".mkv":"video/x-matroska", ".avi":"video/x-msvideo", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".gif":"image/gif", ".webp":"image/webp", ".avif":"image/avif"}


@app.get("/api/media")
def media_list(request: Request, path: Optional[str] = None):
    require_auth(request)
    folder = resolve_user_path(path)
    if not folder.is_dir(): raise HTTPException(status_code=400, detail="Media location is not a folder")
    entries = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))[:1500]:
        try:
            resolved = entry.resolve(strict=True)
            if not any(_under(resolved, root) for root in allowed_roots()): continue
            is_dir = resolved.is_dir()
            ext = entry.suffix.lower()
            if is_dir or ext in MEDIA_EXTENSIONS:
                st = entry.stat()
                kind = "folder" if is_dir else ("image" if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"} else ("video" if ext in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"} else "audio"))
                entries.append({"name": entry.name, "path": str(entry), "directory": is_dir, "size": st.st_size, "kind": kind})
        except (OSError, PermissionError, RuntimeError): continue
    parent = str(folder.parent) if any(_under(folder.parent, root) for root in allowed_roots()) else None
    return {"path": str(folder), "parent": parent, "items": entries}


@app.get("/api/media/stream")
def media_stream(request: Request, path: str):
    require_auth(request)
    target = resolve_user_path(path)
    ext = target.suffix.lower()
    if not target.is_file() or ext not in MEDIA_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Supported media file not found")
    size = target.stat().st_size
    start, end, status = 0, max(0, size - 1), 200
    range_header = request.headers.get("range")
    if range_header and size:
        try:
            unit, value = range_header.split("=", 1)
            if unit.strip().lower() != "bytes": raise ValueError
            first, last = value.split("-", 1)
            if first:
                start = int(first); end = int(last) if last else size - 1
            else:
                suffix_length = int(last)
                if suffix_length <= 0: raise ValueError
                start = max(0, size - suffix_length); end = size - 1
            if start < 0 or end < start or start >= size: raise ValueError
            end = min(end, size - 1); status = 206
        except (ValueError, TypeError):
            raise HTTPException(status_code=416, detail="Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
    length = max(0, end - start + 1)
    def chunks():
        with target.open("rb") as handle:
            handle.seek(start); remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk: break
                remaining -= len(chunk)
                yield chunk
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if status == 206: headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type=MEDIA_TYPES.get(ext, "application/octet-stream"), headers=headers)


@app.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket):
    if not _valid_session(ws.cookies.get(SESSION_COOKIE)):
        await ws.close(code=1008); return
    if not _origin_matches_headers(ws.headers):
        await ws.close(code=1008); return
    await ws.accept()
    try:
        while True:
            vm = psutil.virtual_memory(); sw = psutil.swap_memory(); home = configured_home()
            try: disk = psutil.disk_usage(str(home))
            except OSError: disk = psutil.disk_usage(str(home.anchor or "/"))
            net = psutil.net_io_counters()
            await ws.send_json({"time": time.time(), "cpu": psutil.cpu_percent(interval=None), "ram": vm.percent,
                "ram_used": vm.used, "ram_total": vm.total, "swap": sw.percent, "disk": disk.percent,
                "disk_used": disk.used, "disk_total": disk.total, "net_sent": net.bytes_sent, "net_recv": net.bytes_recv})
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket, shell: str = ""):
    global _terminal_pids
    if not _valid_session(ws.cookies.get(SESSION_COOKIE)):
        await ws.close(code=1008); return
    if not _origin_matches_headers(ws.headers):
        await ws.close(code=1008); return
    if len(_terminal_pids) >= MAX_TERMINAL_SESSIONS:
        await ws.close(code=1013); return
    shell_map = {item["name"]: item["path"] for item in available_shells()}
    executable = shell_map.get(shell)
    if not executable:
        await ws.close(code=1008); return
    await ws.accept()
    home = configured_home()
    try:
        if os.name == "nt":
            try:
                from winpty import PtyProcess
            except ImportError:
                await ws.send_text("\r\nHostPanel terminal on Windows requires pywinpty. Restart the launcher to install it.\r\n")
                await ws.close(code=1011); return
            env = os.environ.copy(); env["TERM"] = "xterm-256color"
            proc = PtyProcess.spawn(f'"{executable}"', cwd=str(home), env=env)
            async def win_reader():
                while proc.isalive():
                    try:
                        output = await asyncio.to_thread(proc.read, 4096)
                        if output:
                            await ws.send_bytes(output.encode("utf-8", errors="replace"))
                        elif not proc.isalive():
                            break
                    except Exception:
                        break
                try:
                    await ws.close(code=1000, reason="Shell session ended")
                except RuntimeError:
                    pass
            reader_task = asyncio.create_task(win_reader())
            try:
                while True:
                    message = await ws.receive_text()
                    if message.startswith("{ ") or message.startswith("{"):
                        try:
                            data = json.loads(message)
                            if data.get("type") == "resize": proc.setwinsize(int(data["rows"]), int(data["cols"]))
                            continue
                        except (ValueError, KeyError, TypeError): pass
                    proc.write(message)
            finally:
                reader_task.cancel()
                try: proc.terminate()
                except Exception: pass
        else:
            import fcntl
            import pty
            import struct
            import termios
            pid, master_fd = pty.fork()
            if pid == 0:
                try:
                    os.chdir(home)
                    env = os.environ.copy()
                    env.update({"HOME": str(home), "PWD": str(home), "SHELL": executable,
                        "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "HostPanel",
                        "CLICOLOR": "1", "FORCE_COLOR": "1"})
                    env.setdefault("LANG", "C.UTF-8")
                    try:
                        username = pwd.getpwuid(os.getuid()).pw_name if pwd else os.environ.get("USERNAME", "user")
                        env.update({"USER": username, "LOGNAME": username})
                    except Exception: pass
                    # Themes and prompt frameworks (Oh My Zsh, Powerlevel10k,
                    # Starship, etc.) load from interactive startup files. A
                    # login-only shell skips ~/.zshrc and breaks their prompts.
                    shell_args = interactive_shell_args(executable)
                    os.execvpe(executable, [executable, *shell_args], env)
                except BaseException as exc:
                    try: os.write(2, (f"HostPanel could not start shell: {exc}\r\n").encode())
                    finally: os._exit(127)
            _terminal_pids.add(pid)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            os.set_blocking(master_fd, False)
            async def posix_reader():
                import select
                try:
                    while True:
                        try:
                            ready, _, _ = await asyncio.to_thread(select.select, [master_fd], [], [], 0.4)
                            if not ready:
                                continue
                            try:
                                data = os.read(master_fd, 65536)
                            except BlockingIOError:
                                continue
                            if not data:
                                break
                            await ws.send_bytes(data)
                        except (OSError, RuntimeError, WebSocketDisconnect):
                            break
                finally:
                    try:
                        await ws.close(code=1000, reason="Shell session ended")
                    except RuntimeError:
                        pass
            reader_task = asyncio.create_task(posix_reader())
            try:
                while True:
                    event = await ws.receive()
                    if event["type"] == "websocket.disconnect": break
                    text = event.get("text")
                    raw = event.get("bytes")
                    if text is not None:
                        if text.startswith("{"):
                            try:
                                data = json.loads(text)
                                if data.get("type") == "resize":
                                    rows = max(5, min(300, int(data.get("rows", 30))))
                                    cols = max(20, min(500, int(data.get("cols", 100))))
                                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                                    continue
                            except (json.JSONDecodeError, ValueError, TypeError): pass
                        payload = text.encode("utf-8")
                    elif raw is not None:
                        payload = raw
                    else:
                        continue
                    view = memoryview(payload)
                    while view:
                        written = os.write(master_fd, view)
                        view = view[written:]
            finally:
                reader_task.cancel()
                try: os.close(master_fd)
                except OSError: pass
                try:
                    os.killpg(pid, signal.SIGTERM)
                except OSError: pass
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        waited, _ = os.waitpid(pid, os.WNOHANG)
                        if waited: break
                    except ChildProcessError: break
                    await asyncio.sleep(0.05)
                else:
                    try: os.killpg(pid, signal.SIGKILL)
                    except OSError: pass
                    try: os.waitpid(pid, 0)
                    except ChildProcessError: pass
                _terminal_pids.discard(pid)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
