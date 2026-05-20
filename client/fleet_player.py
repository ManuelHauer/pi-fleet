#!/usr/bin/env python3
"""
Ars Festival Media Player — runs on each Raspberry Pi.

Sole responsibility: own the mpv lifecycle. Read what to play from
/opt/fleet-media/playlist.current and what state overlay to draw from
/opt/fleet-media/osd.json. Everything else (manifests, network, USB pin,
heartbeats) is handled by fleet_client.py — these two daemons share state
through files on disk, not through IPC or sockets.

This split exists so mpv crashes recover in seconds (via systemd Restart=always)
without taking the management daemon down with it.

Files watched:
  /opt/fleet-media/playlist.current   plain text, one media path per line
                                      missing/empty → play idle screen
  /opt/fleet-media/osd.json           {"message": str, "expires_at": ISO,
                                       "force_until": ISO?, "kind": "info|warn|ok"}
                                      missing → no overlay
  /opt/fleet-media/.restart-player    touch this mtime to force mpv restart

Files produced:
  /opt/fleet-media/system/idle.png    auto-regenerated idle screen
"""
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Configuration ──

MEDIA_BASE = Path("/opt/fleet-media")
PLAYLIST_FILE = MEDIA_BASE / "playlist.current"
OSD_FILE = MEDIA_BASE / "osd.json"
RESTART_TRIGGER = MEDIA_BASE / ".restart-player"
SYSTEM_DIR = MEDIA_BASE / "system"
IDLE_IMAGE = SYSTEM_DIR / "idle.png"
LOCAL_STATE = Path("/etc/fleet-client/local-state.json")
MPV_IPC_SOCKET = "/tmp/fleet-mpv-ipc"

# Idle screen redraw interval (seconds) — picks up new IP after Wi-Fi reconnect
IDLE_REDRAW_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("fleet-player")


# ── Device info (for idle screen) ──

def _read_device_id() -> str:
    p = Path("/etc/fleet-client/device-id")
    if p.exists():
        return p.read_text().strip()
    return "pi-unknown"


def _read_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _read_ip() -> str:
    """Return the first non-loopback, non-AP IPv4."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("169.254") and not ip.startswith("192.168.4"):
            return ip
    except Exception:
        pass
    # Fallback: parse hostname -I
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=5).strip()
        for ip in out.split():
            if not ip.startswith("169.254") and not ip.startswith("192.168.4"):
                return ip
    except Exception:
        pass
    return "no-ip"


# ── Idle screen (PIL) ──

def render_idle_screen() -> bool:
    """Generate the 1920x1080 idle PNG showing device info.

    No branding — pure informational. Tech installs a Pi, sees this immediately,
    knows the install worked and what address to point the dashboard at.

    Returns True if image was written, False if PIL is unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.error("PIL not installed — cannot render idle screen. "
                  "Install with: apt install python3-pil")
        return False

    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)

    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), (10, 10, 15))
    draw = ImageDraw.Draw(img)

    # Try common font paths on Pi OS, fall back to default
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_path = next((p for p in font_paths if Path(p).exists()), None)

    def font(size: int):
        if font_path:
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()

    device_id = _read_device_id()
    hostname = _read_hostname()
    ip = _read_ip()

    # Title (small, top-center)
    title = "Ars Festival Media Player"
    f_title = font(42)
    bbox = draw.textbbox((0, 0), title, font=f_title)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, 80), title, fill=(180, 180, 200), font=f_title)

    # Status line (large, center)
    status = "WAITING FOR MEDIA"
    f_status = font(72)
    bbox = draw.textbbox((0, 0), status, font=f_status)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, (H // 2) - 100), status, fill=(108, 92, 231), font=f_status)

    # Subtitle (medium, below status)
    sub = "Insert USB stick or assign in dashboard"
    f_sub = font(28)
    bbox = draw.textbbox((0, 0), sub, font=f_sub)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, (H // 2) + 10), sub, fill=(136, 136, 160), font=f_sub)

    # Device info block (bottom, monospace-ish)
    f_info = font(28)
    f_label = font(20)
    lines = [
        ("Device ID", device_id),
        ("Hostname", hostname),
        ("IP", ip),
    ]
    base_y = H - 280
    label_x = 120
    value_x = 360
    for label, value in lines:
        draw.text((label_x, base_y), label, fill=(136, 136, 160), font=f_label)
        draw.text((value_x, base_y - 4), value, fill=(224, 224, 232), font=f_info)
        base_y += 56

    # Bottom-right footer
    foot = "Local control: http://{}:8080".format(ip)
    f_foot = font(22)
    bbox = draw.textbbox((0, 0), foot, font=f_foot)
    tw = bbox[2] - bbox[0]
    draw.text((W - tw - 40, H - 60), foot, fill=(136, 136, 160), font=f_foot)

    tmp = IDLE_IMAGE.with_suffix(".png.tmp")
    img.save(tmp)
    tmp.rename(IDLE_IMAGE)
    log.info(f"Idle screen rendered ({device_id} / {hostname} / {ip})")
    return True


# ── MPV IPC ──

def _ipc_send(cmd: dict, timeout: float = 2.0) -> Optional[dict]:
    """Send a single JSON command to mpv via Unix socket, return first response."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(MPV_IPC_SOCKET)
        s.sendall((json.dumps(cmd) + "\n").encode())
        resp = s.recv(4096).decode(errors="replace")
        s.close()
        for line in resp.strip().split("\n"):
            try:
                d = json.loads(line)
                if "error" in d or "data" in d:
                    return d
            except json.JSONDecodeError:
                continue
        return None
    except Exception:
        return None


def _ipc_alive() -> bool:
    """Cheap check that mpv IPC is responsive."""
    return _ipc_send({"command": ["get_property", "mpv-version"]}, timeout=1.0) is not None


# ── Playlist & volume ──

def _load_saved_volume() -> int:
    """Read persisted volume (0–100). Default 100."""
    try:
        if LOCAL_STATE.exists():
            data = json.loads(LOCAL_STATE.read_text())
            v = data.get("volume_pct")
            if isinstance(v, (int, float)):
                return max(0, min(200, int(v)))
            # Legacy VLC scale (0–512) → 0–100
            legacy = data.get("volume")
            if isinstance(legacy, (int, float)):
                return max(0, min(100, round(legacy / 256 * 100)))
    except Exception:
        pass
    return 100


def _read_playlist() -> list:
    """Read playlist.current as list of file paths. Empty list if missing/empty."""
    if not PLAYLIST_FILE.exists():
        return []
    try:
        lines = PLAYLIST_FILE.read_text().splitlines()
        return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    except Exception as e:
        log.warning(f"Could not read playlist: {e}")
        return []


def _playlist_hash() -> str:
    """Stable hash of current playlist content + restart trigger mtime."""
    h = hashlib.sha256()
    if PLAYLIST_FILE.exists():
        try:
            h.update(PLAYLIST_FILE.read_bytes())
        except Exception:
            pass
    if RESTART_TRIGGER.exists():
        try:
            h.update(str(RESTART_TRIGGER.stat().st_mtime).encode())
        except Exception:
            pass
    return h.hexdigest()


# ── MPV lifecycle ──

class MpvProcess:
    """Wraps a single mpv subprocess and the playlist it was started with."""

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.started_at = 0.0
        self.playlist_hash = ""

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            except Exception as e:
                log.warning(f"mpv stop error: {e}")
        # Also nuke any orphan mpv just in case
        subprocess.run(["pkill", "-9", "-f", "mpv.*fleet-mpv-ipc"],
                       capture_output=True, timeout=5)
        try:
            os.unlink(MPV_IPC_SOCKET)
        except OSError:
            pass
        self.proc = None

    def start(self, files: list):
        """Launch mpv playing the given files, looping forever via DRM/KMS."""
        if not files:
            # Idle path — render and play idle image
            if not IDLE_IMAGE.exists():
                render_idle_screen()
            files = [str(IDLE_IMAGE)] if IDLE_IMAGE.exists() else []

        if not files:
            log.error("No files to play and no idle image available")
            return

        volume = _load_saved_volume()
        # Build a temp playlist file mpv will read line-by-line
        pl = MEDIA_BASE / "mpv-playlist.tmp"
        pl.write_text("\n".join(files) + "\n")

        is_idle = files == [str(IDLE_IMAGE)]

        cmd = [
            "mpv",
            "--vo=drm",
            "--ao=alsa",
            "--fullscreen",
            "--loop-playlist=inf",
            "--no-terminal",
            "--force-window=yes",
            "--keep-open=yes",
            "--idle=yes",  # don't exit on playlist end
            f"--input-ipc-server={MPV_IPC_SOCKET}",
            f"--volume={volume}",
            "--osd-font-size=28",
            "--osd-border-size=2",
            "--osd-color=#FFFFFFFF",
            "--osd-border-color=#80000000",
        ]
        if is_idle:
            cmd.append("--image-display-duration=inf")
        else:
            cmd.append("--image-display-duration=10")
        cmd.append(f"--playlist={pl}")

        log.info(f"Starting mpv: {len(files)} item(s), idle={is_idle}, vol={volume}")
        try:
            self.stop()
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.started_at = time.time()
            self.playlist_hash = _playlist_hash()
            # Give mpv a moment to bind the IPC socket
            for _ in range(30):
                if _ipc_alive():
                    break
                time.sleep(0.1)
            log.info(f"mpv started (pid={self.proc.pid})")
        except Exception as e:
            log.error(f"mpv start failed: {e}")
            self.proc = None


# ── OSD overlay ──

def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _read_osd() -> dict:
    if not OSD_FILE.exists():
        return {}
    try:
        return json.loads(OSD_FILE.read_text())
    except Exception:
        return {}


def _push_osd():
    """Read osd.json and push the current state to mpv. Called every 1s."""
    osd = _read_osd()
    if not osd:
        return  # nothing to draw; mpv naturally shows no overlay

    msg = osd.get("message", "")
    if not msg:
        return

    now = datetime.now(timezone.utc)
    force_until = _parse_iso(osd.get("force_until"))
    expires_at = _parse_iso(osd.get("expires_at"))

    # Force mode wins, ignore countdown
    if force_until and now < force_until:
        text = msg
    elif expires_at and now < expires_at:
        remaining = int((expires_at - now).total_seconds())
        mm = remaining // 60
        ss = remaining % 60
        text = f"{msg} · auto-hide {mm:02d}:{ss:02d}"
    else:
        return  # expired, draw nothing

    # Show for ~1.5s; we'll refresh next tick so the countdown ticks down smoothly
    _ipc_send({"command": ["show-text", text, 1500, 1]})  # 1 = level (always show)


# ── Main loop ──

def main():
    log.info("=" * 50)
    log.info("Fleet player starting")
    log.info("=" * 50)

    MEDIA_BASE.mkdir(parents=True, exist_ok=True)
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)

    # Always (re)render idle screen on startup — picks up the latest IP/hostname
    render_idle_screen()
    last_idle_render = time.time()

    mpv = MpvProcess()
    mpv.start(_read_playlist())

    last_osd_tick = 0.0

    while True:
        try:
            now = time.time()

            # 1. Has the playlist (or restart trigger) changed?
            current_hash = _playlist_hash()
            if current_hash != mpv.playlist_hash:
                log.info("Playlist changed — restarting mpv")
                mpv.start(_read_playlist())

            # 2. Did mpv die unexpectedly?
            elif not mpv.is_alive():
                # 5s debounce so we don't spin if mpv refuses to start
                if now - mpv.started_at > 5:
                    log.warning("mpv died — restarting")
                    mpv.start(_read_playlist())

            # 3. OSD overlay every 1s
            if now - last_osd_tick >= 1.0:
                _push_osd()
                last_osd_tick = now

            # 4. Re-render idle screen periodically (in case IP changed)
            if now - last_idle_render > IDLE_REDRAW_INTERVAL:
                # Only re-render when actually showing idle (cheap optimization)
                if not _read_playlist():
                    render_idle_screen()
                last_idle_render = now

            time.sleep(0.5)

        except KeyboardInterrupt:
            log.info("Shutdown requested")
            mpv.stop()
            sys.exit(0)
        except Exception as e:
            log.error(f"Player loop error: {e}", exc_info=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
