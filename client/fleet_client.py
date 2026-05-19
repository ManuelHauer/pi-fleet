#!/usr/bin/env python3
"""
Ars Festival Media Client — runs on each Raspberry Pi.
Periodically checks server for manifest updates, downloads new media,
performs atomic swap, and manages VLC playback loop.
"""
import hashlib
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError

# ── Configuration ──

CONFIG_PATH = Path("/etc/fleet-client/config.json")
DEFAULT_CONFIG = {
    "server_url": "http://192.168.0.62:8550",
    "device_psk": "aec-device-psk-2026",
    "group": "default",
    "poll_interval": 30,     # 30s
    "jitter_max": 5,         # 5s random jitter
    "media_base": "/opt/fleet-media",
    "label": "",
    "vlc_extra_args": [],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/fleet-client.log", mode="a"),
    ]
)
log = logging.getLogger("fleet-client")


class FleetClient:
    def __init__(self):
        self.config = self._load_config()
        self.device_id = self._get_device_id()
        self.media_base = Path(self.config["media_base"])
        self.releases_dir = self.media_base / "releases"
        self.current_link = self.media_base / "current"
        self.state_file = self.media_base / "state.json"
        self.player_process = None
        
        # Ensure directories
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.media_base.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    config.update(json.load(f))
            except Exception as e:
                log.warning(f"Config load error: {e}, using defaults")
        else:
            # Also check local config for dev/setup
            local = Path(__file__).parent / "config.json"
            if local.exists():
                with open(local) as f:
                    config.update(json.load(f))
        return config

    def _get_device_id(self) -> str:
        """Stable device ID derived from Pi SoC serial; survives SD cloning."""
        from identity import device_id
        return device_id()

    def _get_hw_info(self) -> dict:
        """Collect hardware info for registration."""
        info = {
            "hostname": socket.gethostname(),
            "hw_model": "unknown",
            "ip_address": "",
            "mac_address": "",
            "os_info": "",
        }
        try:
            # Pi model
            model_path = Path("/proc/device-tree/model")
            if model_path.exists():
                info["hw_model"] = model_path.read_text().strip().rstrip("\x00")
        except Exception:
            pass
        try:
            # IP — first non-loopback
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            info["ip_address"] = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        try:
            # OS info
            info["os_info"] = subprocess.check_output(
                ["cat", "/etc/os-release"], text=True, timeout=5
            ).split("\n")[0]
        except Exception:
            pass
        return info

    def _get_health_stats(self) -> dict:
        """Collect health metrics."""
        stats = {}
        try:
            temp = Path("/sys/class/thermal/thermal_zone0/temp")
            if temp.exists():
                stats["cpu_temp"] = int(temp.read_text().strip()) / 1000.0
        except Exception:
            pass
        try:
            st = os.statvfs("/")
            stats["disk_free_mb"] = (st.f_bavail * st.f_frsize) // (1024 * 1024)
        except Exception:
            pass
        try:
            with open("/proc/uptime") as f:
                stats["uptime_seconds"] = int(float(f.read().split()[0]))
        except Exception:
            pass
        stats["vlc_status"] = "running" if self._is_player_running() else "stopped"
        return stats

    def _api_call(self, method: str, path: str, data: dict = None) -> Optional[dict]:
        """Make API call to server."""
        url = f"{self.config['server_url'].rstrip('/')}{path}"
        headers = {"X-Device-PSK": self.config["device_psk"]}
        
        try:
            if method == "GET":
                req = Request(url, headers=headers)
            else:
                body = urlencode(data or {}).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                req = Request(url, data=body, headers=headers, method=method)
            
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except URLError as e:
            log.warning(f"API call failed {method} {path}: {e}")
            return None
        except Exception as e:
            log.error(f"API error {method} {path}: {e}")
            return None

    def _download_file(self, filename: str, dest: Path) -> bool:
        """Download a media file from server."""
        url = f"{self.config['server_url'].rstrip('/')}/media/file/{filename}"
        try:
            req = Request(url)
            with urlopen(req, timeout=300) as resp:
                with open(dest, "wb") as f:
                    shutil.copyfileobj(resp, f)
            return True
        except Exception as e:
            log.error(f"Download failed {filename}: {e}")
            return False

    def _verify_checksum(self, path: Path, expected: str) -> bool:
        """Verify SHA256 checksum."""
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        actual = sha.hexdigest()
        if actual != expected:
            log.error(f"Checksum mismatch for {path.name}: expected={expected[:16]}… got={actual[:16]}…")
            return False
        return True

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {"current_version": None}

    def _save_state(self, state: dict):
        self.state_file.write_text(json.dumps(state, indent=2))

    # ── Playback (mpv with DRM/KMS output) ──

    MPV_IPC_SOCKET = "/tmp/fleet-mpv-ipc"

    def _is_player_running(self) -> bool:
        try:
            result = subprocess.run(["pgrep", "-f", "mpv.*fleet-media"],
                                    capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _stop_player(self):
        log.info("Stopping mpv…")
        try:
            subprocess.run(["pkill", "-f", "mpv.*fleet-media"], timeout=10)
            time.sleep(1)
        except Exception as e:
            log.warning(f"mpv stop error: {e}")

    # Keep legacy names as aliases for command compatibility
    _is_vlc_running = _is_player_running
    _stop_vlc = _stop_player

    def _classify_media(self, files: list) -> str:
        """Determine if playlist is video, audio-only, image-only, or mixed."""
        VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
        AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".aac"}
        IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        has_video = any(Path(f).suffix.lower() in VIDEO_EXT for f in files)
        has_audio = any(Path(f).suffix.lower() in AUDIO_EXT for f in files)
        has_image = any(Path(f).suffix.lower() in IMAGE_EXT for f in files)
        if has_video:
            return "video"
        if has_audio and not has_image:
            return "audio_only"
        if has_image and not has_audio:
            return "image_only"
        return "mixed"

    def _ensure_logo(self) -> str:
        """Ensure the Ars Electronica logo placeholder exists for audio-only mode."""
        logo_path = self.media_base / "aec_logo_screen.png"
        if not logo_path.exists():
            try:
                subprocess.run([
                    "python3", "-c", f"""
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (1920, 1080), (0, 0, 0))
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 24)
except Exception:
    font = ImageFont.load_default()
text = 'ars electronica'
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((1920 - tw) // 2, (1080 - th) // 2 + 300), text, fill=(180, 180, 180), font=font)
img.save('{logo_path}')
"""
                ], capture_output=True, timeout=10)
            except Exception:
                logo_path.touch()
        return str(logo_path)

    def _mpv_command(self, cmd_dict: dict) -> Optional[dict]:
        """Send JSON IPC command to mpv via socket."""
        import socket as _sock
        try:
            s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
            s.settimeout(3)
            s.connect(self.MPV_IPC_SOCKET)
            payload = json.dumps(cmd_dict) + "\n"
            s.sendall(payload.encode())
            resp = s.recv(4096).decode(errors="replace")
            s.close()
            for line in resp.strip().split("\n"):
                try:
                    d = json.loads(line)
                    if "data" in d or "error" in d:
                        return d
                except Exception:
                    pass
            return None
        except Exception:
            return None

    def _start_player(self):
        """Start mpv playing all media in current/ on loop via DRM/KMS.
        
        Behavior by media type:
        - Video/images: fullscreen loop on HDMI via --vo=drm
        - Audio-only: play audio with static Ars Electronica logo on screen
        """
        media_dir = self.current_link
        if not media_dir.exists():
            log.warning("No current media directory — player not started")
            return
        
        files = sorted([
            str(f) for f in media_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {
                ".mp4", ".mkv", ".avi", ".mov", ".webm",  # video
                ".mp3", ".wav", ".flac", ".ogg", ".aac",  # audio
                ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",  # images
            }
        ])
        
        if not files:
            log.warning(f"No playable media files found in {media_dir}")
            return

        media_type = self._classify_media(files)
        log.info(f"Media type detected: {media_type}")

        # Restore saved volume
        saved_volume = 100  # mpv uses 0-100 (percent)
        local_state = Path("/etc/fleet-client/local-state.json")
        if local_state.exists():
            try:
                raw_vol = json.loads(local_state.read_text()).get("volume", 256)
                # Convert from old VLC 0-512 scale to mpv 0-100
                saved_volume = max(0, min(100, round(raw_vol / 256 * 100)))
            except Exception:
                pass

        # Build playlist file for mpv
        playlist_path = self.media_base / "playlist.txt"
        with open(playlist_path, "w") as pf:
            for f in files:
                pf.write(f + "\n")

        # Base mpv command with DRM output and IPC socket
        cmd = [
            "mpv",
            "--vo=drm",                    # Direct KMS/DRM output (headless Pi)
            "--ao=alsa",                   # ALSA audio (PulseAudio not available)
            "--fullscreen",
            "--loop-playlist=inf",         # Loop forever
            f"--input-ipc-server={self.MPV_IPC_SOCKET}",
            f"--volume={saved_volume}",
            "--no-terminal",               # No TTY output
            "--force-window=yes",          # Always create video output
            "--keep-open=yes",             # Don't close on end (loop handles it)
        ]

        if media_type == "audio_only":
            logo = self._ensure_logo()
            cmd.extend([
                f"--external-file={logo}",
                "--image-display-duration=inf",
                "--lavfi-complex=[vid1]null[vo]",  # show logo
            ])
            log.info("Audio-only mode: displaying Ars Electronica logo")
        elif media_type == "image_only":
            cmd.extend([
                "--image-display-duration=10",  # 10s per image
            ])
        else:
            cmd.extend([
                "--image-display-duration=10",
            ])

        cmd.append(f"--playlist={playlist_path}")
        
        log.info(f"Starting mpv with {len(files)} files (type={media_type}, vol={saved_volume}%)")
        try:
            # Clean up stale socket
            try:
                os.unlink(self.MPV_IPC_SOCKET)
            except OSError:
                pass
            
            self.player_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            log.info(f"mpv started (pid={self.player_process.pid})")
        except Exception as e:
            log.error(f"mpv start failed: {e}")

    # Legacy aliases
    _start_vlc = _start_player

    def _restart_player(self):
        self._stop_player()
        self._start_player()

    _restart_vlc = _restart_player

    # ── Update logic ──

    def check_and_update(self) -> bool:
        """Check server for new manifest and update media if needed.
        Tries device-specific manifest first, falls back to group manifest."""
        # Try device-specific manifest first (from assignments)
        manifest = self._api_call("GET", f"/device/manifest/{self.device_id}")
        if manifest is None or not manifest.get("version"):
            # Fall back to group manifest
            group = self.config.get("group", "default")
            manifest = self._api_call("GET", f"/manifest/{group}")
        
        if manifest is None:
            log.warning("Cannot reach server — keeping current media")
            return False
        
        server_version = manifest.get("version")
        if not server_version:
            log.info("No manifest published for group yet")
            return False
        
        state = self._load_state()
        if state.get("current_version") == server_version:
            log.info(f"Already on latest version: {server_version}")
            return False
        
        log.info(f"New version available: {server_version} (current: {state.get('current_version')})")
        
        # Download all files to a staging directory
        staging = self.releases_dir / server_version
        staging.mkdir(parents=True, exist_ok=True)
        
        files = manifest.get("files", [])
        all_ok = True
        for finfo in files:
            filename = finfo["filename"]
            checksum = finfo["checksum"]
            dest = staging / finfo.get("filename", filename)
            
            # Skip if already downloaded + valid
            if dest.exists() and self._verify_checksum(dest, checksum):
                log.info(f"  ✓ {filename} (cached)")
                continue
            
            log.info(f"  ↓ Downloading {filename} ({finfo.get('size', '?')} bytes)")
            if not self._download_file(filename, dest):
                all_ok = False
                break
            if not self._verify_checksum(dest, checksum):
                all_ok = False
                break
            log.info(f"  ✓ {filename} verified")
        
        if not all_ok:
            log.error("Update failed — keeping current media")
            # Cleanup failed staging
            shutil.rmtree(staging, ignore_errors=True)
            return False
        
        # Atomic swap: update current symlink
        log.info(f"Activating version {server_version}")
        tmp_link = self.media_base / "current.tmp"
        try:
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            tmp_link.symlink_to(staging)
            tmp_link.rename(self.current_link)
        except Exception as e:
            log.error(f"Symlink swap failed: {e}")
            return False
        
        state["current_version"] = server_version
        state["last_update"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_state(state)
        
        # Restart VLC with new content
        self._restart_vlc()
        
        # Cleanup old releases (keep last 2)
        self._cleanup_old_releases(keep=2)
        
        log.info(f"✅ Update complete — now playing version {server_version}")
        return True

    def _cleanup_old_releases(self, keep: int = 2):
        """Remove old release directories, keeping the N most recent."""
        try:
            releases = sorted(self.releases_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            if len(releases) > keep:
                for old in releases[:-keep]:
                    if old.is_dir():
                        log.info(f"Cleaning old release: {old.name}")
                        shutil.rmtree(old, ignore_errors=True)
        except Exception as e:
            log.warning(f"Cleanup error: {e}")

    # ── Command execution ──

    def _execute_command(self, cmd: dict):
        """Execute a command from the server."""
        command = cmd.get("command")
        cmd_id = cmd.get("id")
        params = json.loads(cmd.get("params", "{}")) if isinstance(cmd.get("params"), str) else cmd.get("params", {})
        
        log.info(f"Executing command: {command} (id={cmd_id})")
        result = "ok"
        
        try:
            if command == "reboot":
                self._api_call("POST", f"/device/commands/{cmd_id}/ack", {"result": "rebooting"})
                subprocess.run(["sudo", "reboot"], timeout=10)
                return  # won't reach here
            
            elif command == "vlc_restart":
                self._restart_vlc()
                result = "vlc restarted"
            
            elif command == "update_now":
                updated = self.check_and_update()
                result = "updated" if updated else "already current"
            
            elif command == "health_probe":
                stats = self._get_health_stats()
                result = json.dumps(stats)
            
            elif command == "shell":
                shell_cmd = params.get("cmd", "echo no command")
                try:
                    out = subprocess.check_output(
                        shell_cmd, shell=True, text=True, timeout=30, stderr=subprocess.STDOUT
                    )
                    result = out[:500]
                except subprocess.CalledProcessError as e:
                    result = f"error: {e.output[:300]}"
            
            else:
                result = f"unknown command: {command}"
        
        except Exception as e:
            result = f"error: {str(e)[:200]}"
        
        self._api_call("POST", f"/device/commands/{cmd_id}/ack", {"result": result})

    # ── Registration ──

    def register(self):
        """Register this device with the server."""
        hw = self._get_hw_info()
        data = {
            "device_id": self.device_id,
            "hostname": hw["hostname"],
            "label": self.config.get("label") or hw["hostname"],
            "group_name": self.config.get("group", "default"),
            "hw_model": hw["hw_model"],
            "ip_address": hw["ip_address"],
            "mac_address": hw.get("mac_address", ""),
            "os_info": hw["os_info"],
        }
        result = self._api_call("POST", "/device/register", data)
        if result:
            log.info(f"Registered: {result}")
        else:
            log.warning("Registration failed — will retry on next heartbeat")

    # ── Heartbeat ──

    def send_heartbeat(self) -> list:
        """Send heartbeat and return any pending commands."""
        stats = self._get_health_stats()
        state = self._load_state()
        data = {
            "device_id": self.device_id,
            "manifest_version": state.get("current_version", ""),
            "vlc_status": stats.get("vlc_status", "unknown"),
            "cpu_temp": stats.get("cpu_temp", 0),
            "disk_free_mb": stats.get("disk_free_mb", 0),
            "uptime_seconds": stats.get("uptime_seconds", 0),
        }
        result = self._api_call("POST", "/device/heartbeat", data)
        if result and result.get("pending_commands"):
            return result["pending_commands"]
        return []

    # ── Main loop ──

    def run(self):
        """Main daemon loop."""
        log.info(f"Fleet client starting — device={self.device_id} group={self.config.get('group')}")
        
        # Initial registration
        self.register()
        
        # Start VLC with existing content if available
        if self.current_link.exists():
            self._start_vlc()
        
        # Initial update check
        self.check_and_update()
        
        while True:
            try:
                # Send heartbeat and process commands
                commands = self.send_heartbeat()
                for cmd in commands:
                    self._execute_command(cmd)
                
                # Check for updates
                self.check_and_update()
                
            except Exception as e:
                log.error(f"Main loop error: {e}")
            
            # Sleep with jitter
            interval = self.config.get("poll_interval", 43200)
            jitter = random.randint(0, self.config.get("jitter_max", 600))
            sleep_time = interval + jitter
            log.info(f"Next check in {sleep_time}s ({sleep_time/3600:.1f}h)")
            time.sleep(sleep_time)


if __name__ == "__main__":
    client = FleetClient()
    client.run()
