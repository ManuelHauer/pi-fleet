#!/usr/bin/env python3
"""
Ars Festival Media Client — management daemon (does NOT own mpv).

Responsibilities:
  - Probe the server every 10s, poll manifests every 30s when connected.
  - Atomically swap the `current` symlink on manifest update.
  - Maintain a derived state machine: NO_MEDIA / PLAYING_CONNECTED / PLAYING_OFFLINE.
  - Write the OSD overlay file for fleet_player.py to render.
  - Send heartbeats, handle pin state, accept admin commands.

mpv lifecycle is handled by fleet_player.py. The handoff is purely file-based:
  /opt/fleet-media/playlist.current   — one media path per line
  /opt/fleet-media/.restart-player    — mtime change forces player reload
  /opt/fleet-media/osd.json           — overlay state (message + timing)
"""
import hashlib
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
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
    "poll_interval": 30,
    "jitter_max": 5,
    "media_base": "/opt/fleet-media",
    "label": "",
}

MEDIA_BASE = Path("/opt/fleet-media")
PLAYLIST_FILE = MEDIA_BASE / "playlist.current"
RESTART_TRIGGER = MEDIA_BASE / ".restart-player"
OSD_FILE = MEDIA_BASE / "osd.json"

FAST_PROBE_INTERVAL = 10   # seconds — cheap HEAD /health
SLOW_POLL_INTERVAL = 30    # seconds — manifest + heartbeat
OFFLINE_OSD_DURATION_SEC = 5 * 60
CONNECTED_OSD_DURATION_SEC = 5

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

        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.media_base.mkdir(parents=True, exist_ok=True)

        # State machine memory
        self._server_reachable = False
        self._osd_clear_at: Optional[float] = None  # monotonic deadline to delete OSD file

    # ── Config & identity ──

    def _load_config(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        if CONFIG_PATH.exists():
            try:
                config.update(json.loads(CONFIG_PATH.read_text()))
            except Exception as e:
                log.warning(f"Config load error: {e}, using defaults")
        else:
            local = Path(__file__).parent / "config.json"
            if local.exists():
                config.update(json.loads(local.read_text()))
        return config

    def _get_device_id(self) -> str:
        """Stable device ID derived from Pi SoC serial; survives SD cloning."""
        from identity import device_id
        return device_id()

    # ── Hardware / health ──

    def _get_hw_info(self) -> dict:
        info = {
            "hostname": socket.gethostname(),
            "hw_model": "unknown",
            "ip_address": "",
            "mac_address": "",
            "os_info": "",
        }
        try:
            model_path = Path("/proc/device-tree/model")
            if model_path.exists():
                info["hw_model"] = model_path.read_text().strip().rstrip("\x00")
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            info["ip_address"] = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        try:
            info["os_info"] = subprocess.check_output(
                ["cat", "/etc/os-release"], text=True, timeout=5
            ).split("\n")[0]
        except Exception:
            pass
        return info

    def _is_player_running(self) -> bool:
        try:
            r = subprocess.run(["pgrep", "-f", "fleet_player.py"],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _get_health_stats(self) -> dict:
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

    # ── HTTP ──

    def _api_call(self, method: str, path: str, data: dict = None,
                  timeout: float = 30.0) -> Optional[dict]:
        url = f"{self.config['server_url'].rstrip('/')}{path}"
        headers = {"X-Device-PSK": self.config["device_psk"]}
        try:
            if method == "GET":
                req = Request(url, headers=headers)
            else:
                body = urlencode(data or {}).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                req = Request(url, data=body, headers=headers, method=method)
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except URLError as e:
            log.warning(f"API call failed {method} {path}: {e}")
            return None
        except Exception as e:
            log.error(f"API error {method} {path}: {e}")
            return None

    def _download_file(self, filename: str, dest: Path) -> bool:
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
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        actual = sha.hexdigest()
        if actual != expected:
            log.error(f"Checksum mismatch for {path.name}: expected={expected[:16]}… got={actual[:16]}…")
            return False
        return True

    # ── Local state ──

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {"current_version": None, "pinned": False}

    def _save_state(self, state: dict):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2))

    def _is_pinned(self) -> bool:
        return bool(self._load_state().get("pinned"))

    # ── State machine ──

    def _probe_server(self) -> bool:
        """Cheap reachability check: HEAD /health, 3s timeout."""
        url = f"{self.config['server_url'].rstrip('/')}/health"
        try:
            req = Request(url, method="HEAD")
            with urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 500
        except Exception:
            return False

    def _has_default_route(self) -> bool:
        try:
            with open("/proc/net/route") as f:
                next(f)  # skip header
                for line in f:
                    parts = line.split()
                    # dest=0x00000000 means default route
                    if len(parts) >= 2 and parts[1] == "00000000":
                        return True
        except Exception:
            pass
        return False

    def _tailscale_up(self) -> bool:
        try:
            r = subprocess.run(["tailscale", "ip", "-4"],
                               capture_output=True, text=True, timeout=3)
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            return False

    def _reason_for_offline(self) -> str:
        """Cheapest → most specific. One of: 'no wifi', 'no mesh', 'no server'."""
        if not self._has_default_route():
            return "no wifi"
        if not self._tailscale_up():
            return "no mesh"
        return "no server"

    def _compute_state(self) -> str:
        if not self.current_link.exists():
            return "NO_MEDIA"
        return "PLAYING_CONNECTED" if self._server_reachable else "PLAYING_OFFLINE"

    # ── OSD writer ──

    def _persist_state(self, state: str, offline_reason: Optional[str]):
        """Mirror the derived state into state.json so other readers
        (local_control UI, diag.sh) can show it without re-deriving."""
        s = self._load_state()
        s["state"] = state
        s["offline_reason"] = offline_reason or ""
        s["state_at"] = time.time()
        self._save_state(s)

    def _update_osd(self, state: str, prev_state: Optional[str]):
        """Write the overlay file consumed by fleet_player.py.

        Transitions:
          * → NO_MEDIA              : delete OSD file
          (any) → PLAYING_OFFLINE   : write 5-min countdown (once per episode)
          PLAYING_OFFLINE → PLAYING_CONNECTED : flash 5s '✓ CONNECTED'
        """
        if state == prev_state:
            return

        if state == "NO_MEDIA":
            if OSD_FILE.exists():
                try:
                    OSD_FILE.unlink()
                except Exception:
                    pass
            self._osd_clear_at = None
            return

        if state == "PLAYING_OFFLINE":
            reason = self._reason_for_offline()
            expires = datetime.now(timezone.utc) + timedelta(seconds=OFFLINE_OSD_DURATION_SEC)
            payload = {
                "message": f"⚠ OFFLINE · {reason}",
                "expires_at": expires.isoformat(),
                "kind": "warn",
            }
            try:
                OSD_FILE.parent.mkdir(parents=True, exist_ok=True)
                OSD_FILE.write_text(json.dumps(payload))
                log.info(f"OSD armed: OFFLINE ({reason}) for {OFFLINE_OSD_DURATION_SEC}s")
            except Exception as e:
                log.warning(f"OSD write failed: {e}")
            self._osd_clear_at = None
            return

        if state == "PLAYING_CONNECTED" and prev_state == "PLAYING_OFFLINE":
            force_until = datetime.now(timezone.utc) + timedelta(seconds=CONNECTED_OSD_DURATION_SEC)
            payload = {
                "message": "✓ CONNECTED",
                "force_until": force_until.isoformat(),
                "kind": "ok",
            }
            try:
                OSD_FILE.parent.mkdir(parents=True, exist_ok=True)
                OSD_FILE.write_text(json.dumps(payload))
                log.info("OSD: flashed CONNECTED")
            except Exception as e:
                log.warning(f"OSD write failed: {e}")
            self._osd_clear_at = time.monotonic() + CONNECTED_OSD_DURATION_SEC + 1

    def _tick_osd_cleanup(self):
        if self._osd_clear_at and time.monotonic() >= self._osd_clear_at:
            if OSD_FILE.exists():
                try:
                    OSD_FILE.unlink()
                except Exception:
                    pass
            self._osd_clear_at = None

    # ── Playlist → player handoff ──

    def _write_playlist(self):
        """Mirror the contents of `current/` into playlist.current, touch restart trigger."""
        if not self.current_link.exists():
            if PLAYLIST_FILE.exists():
                try:
                    PLAYLIST_FILE.unlink()
                except Exception:
                    pass
        else:
            files = sorted(self.current_link.glob("*"))
            files = [str(f) for f in files if f.is_file() and not f.name.startswith(".")]
            PLAYLIST_FILE.write_text("\n".join(files) + "\n")
        try:
            RESTART_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
            RESTART_TRIGGER.touch()
        except Exception as e:
            log.warning(f"Restart trigger touch failed: {e}")

    # ── Manifest update ──

    def _poll_manifest(self) -> bool:
        """Check server for new manifest, download + atomic swap if newer.
        Returns True if media changed."""
        # Try per-device manifest first; fall back to group manifest
        manifest = self._api_call("GET", f"/device/manifest/{self.device_id}")
        if manifest is None or not manifest.get("version"):
            group = self.config.get("group", "default")
            manifest = self._api_call("GET", f"/manifest/{group}")

        if manifest is None:
            log.warning("Cannot reach server — keeping current media")
            return False

        # Server may signal 'skip' when device is server-side pinned
        if manifest.get("skip") or manifest.get("version") == "pinned":
            log.info("Server signaled pinned; skipping manifest")
            return False

        server_version = manifest.get("version")
        if not server_version:
            return False

        state = self._load_state()
        if state.get("current_version") == server_version:
            return False

        log.info(f"New version available: {server_version} (current: {state.get('current_version')})")

        staging = self.releases_dir / server_version
        staging.mkdir(parents=True, exist_ok=True)

        files = manifest.get("files", [])
        for finfo in files:
            filename = finfo["filename"]
            checksum = finfo["checksum"]
            dest = staging / filename
            if dest.exists() and self._verify_checksum(dest, checksum):
                log.info(f"  ✓ {filename} (cached)")
                continue
            log.info(f"  ↓ Downloading {filename}")
            if not self._download_file(filename, dest):
                shutil.rmtree(staging, ignore_errors=True)
                return False
            if not self._verify_checksum(dest, checksum):
                shutil.rmtree(staging, ignore_errors=True)
                return False
            log.info(f"  ✓ {filename} verified")

        # Atomic symlink swap
        tmp_link = self.media_base / "current.new"
        try:
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            tmp_link.symlink_to(staging)
            os.replace(tmp_link, self.current_link)
        except Exception as e:
            log.error(f"Symlink swap failed: {e}")
            return False

        state["current_version"] = server_version
        state["last_update"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_state(state)

        self._write_playlist()
        self._cleanup_old_releases(keep=2)
        log.info(f"✅ Activated version {server_version}")
        return True

    def _cleanup_old_releases(self, keep: int = 2):
        try:
            current_target = self.current_link.resolve() if self.current_link.exists() else None
            releases = sorted(self.releases_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            old = releases[:-keep] if len(releases) > keep else []
            for r in old:
                if r.is_dir() and r.resolve() != current_target:
                    log.info(f"Cleaning old release: {r.name}")
                    shutil.rmtree(r, ignore_errors=True)
        except Exception as e:
            log.warning(f"Cleanup error: {e}")

    # ── Heartbeat & commands ──

    def register(self):
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

    def _heartbeat(self) -> list:
        stats = self._get_health_stats()
        state = self._load_state()
        data = {
            "device_id": self.device_id,
            "manifest_version": state.get("current_version", "") or "",
            "vlc_status": stats.get("vlc_status", "unknown"),
            "cpu_temp": stats.get("cpu_temp", 0),
            "disk_free_mb": stats.get("disk_free_mb", 0),
            "uptime_seconds": stats.get("uptime_seconds", 0),
            "pinned": "1" if state.get("pinned") else "0",
            "pinned_source": state.get("pinned_source") or "",
            "pinned_at": str(state.get("pinned_at") or ""),
        }
        result = self._api_call("POST", "/device/heartbeat", data)
        if result and result.get("pending_commands"):
            return result["pending_commands"]
        return []

    def _execute_command(self, cmd: dict):
        command = cmd.get("command")
        cmd_id = cmd.get("id")
        raw_params = cmd.get("params", "{}")
        params = json.loads(raw_params) if isinstance(raw_params, str) else (raw_params or {})

        log.info(f"Executing command: {command} (id={cmd_id})")
        result = "ok"

        try:
            if command == "reboot":
                self._api_call("POST", f"/device/commands/{cmd_id}/ack", {"result": "rebooting"})
                subprocess.run(["sudo", "reboot"], timeout=10)
                return

            elif command in ("vlc_restart", "player_restart"):
                # Just signal the player; we no longer own mpv.
                self._write_playlist()
                result = "player restart signaled"

            elif command == "update_now":
                updated = self._poll_manifest()
                result = "updated" if updated else "already current"

            elif command in ("unpin", "force_poll"):
                state = self._load_state()
                state["pinned"] = False
                state.pop("pinned_source", None)
                state.pop("pinned_at", None)
                self._save_state(state)
                updated = self._poll_manifest()
                result = "unpinned + " + ("updated" if updated else "no update")

            elif command == "health_probe":
                result = json.dumps(self._get_health_stats())

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

    # ── Main loop ──

    def run(self):
        log.info(f"Fleet client starting — device={self.device_id} group={self.config.get('group')}")

        # If the player has no playlist yet but we already have media on disk
        # (e.g. after a restart), rebuild it so fleet-player can resume.
        if self.current_link.exists() and not PLAYLIST_FILE.exists():
            self._write_playlist()

        # Initial probe + register (best-effort, doesn't block)
        self._server_reachable = self._probe_server()
        if self._server_reachable:
            try:
                self.register()
            except Exception as e:
                log.warning(f"Initial register exception: {e}")

        last_fast = 0.0
        next_slow_at = 0.0  # monotonic deadline; refreshed each fire with jitter
        prev_state: Optional[str] = None

        while True:
            try:
                now = time.monotonic()

                # 1. Fast probe (server reachability)
                if now - last_fast >= FAST_PROBE_INTERVAL:
                    self._server_reachable = self._probe_server()
                    last_fast = now

                # 2. Derive state
                state = self._compute_state()
                if state != prev_state:
                    log.info(f"State transition: {prev_state} → {state}")
                    self._update_osd(state, prev_state)
                    reason = self._reason_for_offline() if state == "PLAYING_OFFLINE" else None
                    self._persist_state(state, reason)
                    prev_state = state
                self._tick_osd_cleanup()

                # 2b. Externally-triggered immediate update (from local_control UI)
                update_trigger = Path("/tmp/fleet-update-now")
                if update_trigger.exists():
                    try:
                        update_trigger.unlink()
                    except Exception:
                        pass
                    log.info("Manual update trigger received")
                    if state == "PLAYING_CONNECTED" and not self._is_pinned():
                        try:
                            self._poll_manifest()
                        except Exception as e:
                            log.error(f"Triggered poll failed: {e}")

                # 3. Slow loop (manifest + heartbeat). Jitter is applied here
                # so 150 Pis don't all hammer the server on the same second.
                if now >= next_slow_at:
                    next_slow_at = now + SLOW_POLL_INTERVAL + random.uniform(
                        0, self.config.get("jitter_max", 0))
                    pinned = self._is_pinned()
                    if state == "PLAYING_CONNECTED":
                        if not pinned:
                            try:
                                self._poll_manifest()
                            except Exception as e:
                                log.error(f"Manifest poll error: {e}")
                        else:
                            log.info("Pinned — skipping manifest poll")
                        try:
                            commands = self._heartbeat()
                            for cmd in commands:
                                self._execute_command(cmd)
                        except Exception as e:
                            log.error(f"Heartbeat error: {e}")
                    elif state == "PLAYING_OFFLINE":
                        # Heartbeat will fail; try anyway so lastSeen behavior is consistent
                        # when the server briefly flapped.
                        try:
                            self._heartbeat()
                        except Exception:
                            pass
                    # NO_MEDIA: nothing to do; still ticked OSD above.

                time.sleep(1)

            except KeyboardInterrupt:
                log.info("Shutdown requested")
                return
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(2)


if __name__ == "__main__":
    FleetClient().run()
