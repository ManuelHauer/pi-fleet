#!/usr/bin/env python3
"""
Local Control Web UI — runs on each Raspberry Pi.
Accessible by technicians on the same LAN via phone browser.
Provides volume control, playback info, and basic device management.

Runs on port 8080 (avoids conflict with captive portal on 80).
"""
import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from functools import wraps
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for

log = logging.getLogger("local-control")

app = Flask(__name__)
app.config["SECRET_KEY"] = "fleet-local-ctrl-aec2026"

# Auth — single shared password for all technicians
# Read from config or use default (change before production!)
LOCAL_AUTH_PASSWORD = os.environ.get("FLEET_LOCAL_PASSWORD", "aec2026")

# mpv IPC socket for runtime control
MPV_IPC_SOCKET = "/tmp/fleet-mpv-ipc"


def _load_local_password() -> str:
    """Load password from fleet config if set, otherwise use env/default."""
    try:
        cfg_path = Path("/etc/fleet-client/config.json")
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            return cfg.get("local_password", LOCAL_AUTH_PASSWORD)
    except Exception:
        pass
    return LOCAL_AUTH_PASSWORD


def require_auth(f):
    """Decorator to require local auth for routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json:
                return jsonify({"error": "Not authenticated"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

MEDIA_BASE = Path("/opt/fleet-media")
STATE_FILE = MEDIA_BASE / "state.json"
LOCAL_STATE_FILE = Path("/etc/fleet-client/local-state.json")


def _get_device_info() -> dict:
    """Collect device identity and status."""
    info = {
        "hostname": socket.gethostname(),
        "device_id": "",
        "ip": "",
        "group": "default",
    }
    try:
        id_file = Path("/etc/fleet-client/device-id")
        if id_file.exists():
            info["device_id"] = id_file.read_text().strip()
    except Exception:
        pass
    try:
        config_file = Path("/etc/fleet-client/config.json")
        if config_file.exists():
            cfg = json.loads(config_file.read_text())
            info["group"] = cfg.get("group", "default")
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["ip"] = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return info


def _mpv_ipc_command(cmd_dict: dict):
    """Send JSON IPC command to mpv via Unix socket."""
    import socket as _sock
    try:
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(3)
        s.connect(MPV_IPC_SOCKET)
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
    except Exception as e:
        return {"error": str(e)}


def _get_volume() -> int:
    """Get current mpv volume (0-100 percent)."""
    resp = _mpv_ipc_command({"command": ["get_property", "volume"]})
    if resp and "data" in resp:
        return int(resp["data"])
    # Fall back to saved state
    state = _get_local_state()
    return max(0, min(100, round(state.get("volume", 256) / 256 * 100)))


def _set_volume(pct: int) -> str:
    """Set mpv volume (0-100 percent)."""
    pct = max(0, min(200, pct))
    resp = _mpv_ipc_command({"command": ["set_property", "volume", pct]})
    if resp and resp.get("error") == "success":
        return "ok"
    return f"error: {resp}"


def _get_local_state() -> dict:
    """Read persistent local state (volume etc)."""
    if LOCAL_STATE_FILE.exists():
        try:
            return json.loads(LOCAL_STATE_FILE.read_text())
        except Exception:
            pass
    return {"volume": 256}


def _save_local_state(state: dict):
    """Save persistent local state."""
    try:
        LOCAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning(f"Failed to save local state: {e}")


def _get_current_media() -> list:
    """List current media files."""
    current = MEDIA_BASE / "current"
    if not current.exists():
        return []
    return sorted([
        {"name": f.name, "size": f.stat().st_size, "type": _media_type(f)}
        for f in current.iterdir()
        if f.is_file() and f.suffix.lower() in {
            ".mp4", ".mkv", ".avi", ".mov", ".webm",
            ".mp3", ".wav", ".flac", ".ogg", ".aac",
            ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
        }
    ], key=lambda x: x["name"])


def _media_type(f: Path) -> str:
    ext = f.suffix.lower()
    if ext in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
        return "video"
    elif ext in {".mp3", ".wav", ".flac", ".ogg", ".aac"}:
        return "audio"
    else:
        return "image"


def _get_manifest_version() -> str:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("current_version", "—")
        except Exception:
            pass
    return "—"


# ── HTML template (single-page, mobile-first) ──

CONTROL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{{ device.hostname }} — Ars Fleet Control</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, system-ui, sans-serif;
    background: #0a0a0f; color: #e0e0e8;
    min-height: 100vh; padding: 16px;
  }
  .header { text-align: center; padding: 16px 0 8px; }
  .header h1 { font-size: 18px; }
  .header .sub { font-size: 11px; color: #8888a0; margin-top: 4px; font-family: monospace; }
  
  .card {
    background: #141420; border: 1px solid #2a2a3e; border-radius: 12px;
    padding: 20px; margin: 12px 0;
  }
  .card-title { font-size: 13px; color: #8888a0; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  
  /* Volume control */
  .volume-section { text-align: center; }
  .volume-display { font-size: 48px; font-weight: 700; color: #a29bfe; margin: 8px 0; }
  .volume-display .pct { font-size: 20px; color: #8888a0; }
  .volume-slider {
    width: 100%; height: 44px; -webkit-appearance: none; appearance: none;
    background: #1c1c2e; border-radius: 8px; outline: none;
    margin: 16px 0;
  }
  .volume-slider::-webkit-slider-thumb {
    -webkit-appearance: none; width: 32px; height: 32px;
    background: #6c5ce7; border-radius: 50%; cursor: pointer;
  }
  .volume-slider::-webkit-slider-runnable-track {
    height: 8px; background: #2a2a3e; border-radius: 4px;
  }
  .vol-btns { display: flex; gap: 8px; justify-content: center; }
  .vol-btn {
    padding: 12px 24px; border: 1px solid #2a2a3e; border-radius: 8px;
    background: #1c1c2e; color: #e0e0e8; font-size: 18px; cursor: pointer;
    min-width: 60px;
  }
  .vol-btn:active { background: #6c5ce722; }
  .vol-btn.mute { color: #ff6b6b; border-color: #ff6b6b44; }
  
  /* Status info */
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .info-item { padding: 8px; }
  .info-label { font-size: 10px; color: #8888a0; text-transform: uppercase; }
  .info-value { font-size: 14px; margin-top: 2px; }
  
  /* Media list */
  .media-item {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 0; border-bottom: 1px solid #1c1c2e; font-size: 13px;
  }
  .media-icon { font-size: 16px; }
  .media-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .media-size { color: #8888a0; font-size: 11px; }
  
  /* Actions */
  .action-btn {
    display: block; width: 100%; padding: 14px; margin: 8px 0;
    border: 1px solid #2a2a3e; border-radius: 8px;
    background: #141420; color: #e0e0e8; font-size: 14px;
    cursor: pointer; text-align: center;
  }
  .action-btn:active { background: #1c1c2e; }
  .action-btn.danger { border-color: #ff6b6b44; color: #ff6b6b; }
  
  .toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    padding: 10px 20px; background: #1c1c2e; border: 1px solid #6c5ce7;
    border-radius: 8px; font-size: 13px; z-index: 100;
  }
</style>
</head>
<body>
  <div class="header">
    <h1>🎬 {{ device.hostname }}</h1>
    <div class="sub">{{ device.device_id }} · {{ device.group }}</div>
  </div>

  <!-- Volume Control -->
  <div class="card">
    <div class="card-title">🔊 Volume</div>
    <div class="volume-section">
      <div class="volume-display"><span id="volVal">{{ volume_pct }}</span><span class="pct">%</span></div>
      <input type="range" class="volume-slider" id="volSlider"
             min="0" max="200" value="{{ volume_pct }}"
             oninput="updateVolume(this.value)">
      <div class="vol-btns">
        <button class="vol-btn" onclick="adjustVolume(-10)">−</button>
        <button class="vol-btn mute" onclick="setVolume(0)">🔇</button>
        <button class="vol-btn" onclick="adjustVolume(10)">+</button>
      </div>
    </div>
  </div>

  <!-- Device Info -->
  <div class="card">
    <div class="card-title">📊 Status</div>
    <div class="info-grid">
      <div class="info-item">
        <div class="info-label">IP Address</div>
        <div class="info-value">{{ device.ip }}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Manifest</div>
        <div class="info-value">{{ manifest_version[:15] }}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Media Files</div>
        <div class="info-value">{{ media|length }}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Playback</div>
        <div class="info-value" id="playStatus">active</div>
      </div>
    </div>
  </div>

  <!-- Current Media -->
  <div class="card">
    <div class="card-title">🎬 Current Media</div>
    {% for m in media %}
    <div class="media-item">
      <span class="media-icon">{{ '🎥' if m.type == 'video' else ('🎵' if m.type == 'audio' else '🖼') }}</span>
      <span class="media-name">{{ m.name }}</span>
      <span class="media-size">{{ (m.size / 1048576)|round(1) }}MB</span>
    </div>
    {% endfor %}
    {% if not media %}
    <div style="color:#8888a0;font-size:13px;text-align:center;padding:12px">No media loaded yet</div>
    {% endif %}
  </div>

  <!-- Actions -->
  <div class="card">
    <div class="card-title">⚙ Actions</div>
    <button class="action-btn" onclick="apiAction('vlc_restart')">▶ Restart Player</button>
    <button class="action-btn" onclick="apiAction('update_now')">⬇ Check for Updates</button>
    <button class="action-btn" onclick="apiAction('wifi_reset')">📶 Reset Wi-Fi (Re-enter credentials)</button>
    <button class="action-btn danger" onclick="if(confirm('Reboot device?'))apiAction('reboot')">⟳ Reboot Device</button>
  </div>

<script>
let currentVol = {{ volume_pct }};
let volTimer = null;

function updateVolume(pct) {
  document.getElementById('volVal').textContent = pct;
  clearTimeout(volTimer);
  volTimer = setTimeout(() => setVolume(parseInt(pct)), 200);
}

function setVolume(pct) {
  document.getElementById('volVal').textContent = pct;
  document.getElementById('volSlider').value = pct;
  fetch('/api/volume', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({volume_pct: pct})
  }).then(r => r.json()).then(d => {
    if (d.ok) toast('Volume: ' + pct + '%');
  });
}

function adjustVolume(delta) {
  let v = parseInt(document.getElementById('volSlider').value) + delta;
  v = Math.max(0, Math.min(200, v));
  setVolume(v);
}

function apiAction(action) {
  fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: action})
  }).then(r => r.json()).then(d => {
    toast(d.message || action + ' sent');
  });
}

function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2000);
}

// Auto-refresh volume
setInterval(async () => {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.volume_pct !== undefined && !volTimer) {
      document.getElementById('volVal').textContent = d.volume_pct;
      document.getElementById('volSlider').value = d.volume_pct;
    }
  } catch(e) {}
}, 5000);
</script>
</body>
</html>"""


# ── Login page ──

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Ars Fleet — Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, system-ui, sans-serif;
    background: #0a0a0f; color: #e0e0e8;
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }
  .card {
    background: #141420; border: 1px solid #2a2a3e; border-radius: 16px;
    padding: 32px; width: 100%; max-width: 360px; text-align: center;
  }
  h1 { font-size: 20px; margin-bottom: 4px; }
  .sub { color: #8888a0; font-size: 12px; margin-bottom: 24px; }
  input {
    width: 100%; padding: 12px 14px; border: 1px solid #2a2a3e; border-radius: 8px;
    background: #0a0a0f; color: #e0e0e8; font-size: 16px; outline: none;
    margin-bottom: 16px;
  }
  input:focus { border-color: #6c5ce7; }
  .btn {
    width: 100%; padding: 14px; border: none; border-radius: 8px;
    background: #6c5ce7; color: white; font-size: 16px; font-weight: 600; cursor: pointer;
  }
  .error { color: #ff6b6b; font-size: 13px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="card">
  <h1>🎬 Ars Fleet</h1>
  <div class="sub">Device Control — Login</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Password" autofocus required>
    <button type="submit" class="btn">Login</button>
  </form>
</div>
</body>
</html>"""


# ── Routes ──

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == _load_local_password():
            session["authenticated"] = True
            return redirect("/")
        return render_template_string(LOGIN_HTML, error="Wrong password")
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect("/login")


@app.route("/")
@require_auth
def index():
    device = _get_device_info()
    volume_pct = _get_volume()  # mpv already returns 0-100
    media = _get_current_media()
    manifest_version = _get_manifest_version()
    return render_template_string(CONTROL_HTML,
                                  device=device,
                                  volume_pct=volume_pct,
                                  media=media,
                                  manifest_version=manifest_version)


@app.route("/api/status")
@require_auth
def api_status():
    device = _get_device_info()
    volume_pct = _get_volume()
    media = _get_current_media()
    return jsonify({
        "device": device,
        "volume_pct": volume_pct,
        "media_count": len(media),
        "manifest_version": _get_manifest_version(),
    })


@app.route("/api/volume", methods=["POST"])
@require_auth
def api_volume():
    data = request.get_json(force=True)
    pct = int(data.get("volume_pct", 100))
    _set_volume(pct)
    # Persist (store in mpv-native 0-100 scale, keep legacy "volume" key for compat)
    state = _get_local_state()
    state["volume"] = round(pct * 256 / 100)  # legacy VLC scale for fleet_client compat
    state["volume_pct"] = pct
    _save_local_state(state)
    return jsonify({"ok": True, "volume_pct": pct})


@app.route("/api/action", methods=["POST"])
@require_auth
def api_action():
    data = request.get_json(force=True)
    action = data.get("action", "")

    if action == "vlc_restart":
        subprocess.Popen(["systemctl", "restart", "fleet-client"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "message": "Restarting playback…"})

    elif action == "update_now":
        # Touch a trigger file that fleet_client checks
        Path("/tmp/fleet-update-now").touch()
        return jsonify({"ok": True, "message": "Update check triggered"})

    elif action == "reboot":
        subprocess.Popen(["sudo", "reboot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "message": "Rebooting…"})

    elif action == "wifi_reset":
        # Remove onboard-done marker so onboarding restarts on reboot
        try:
            Path("/etc/fleet-client/onboard-done").unlink(missing_ok=True)
        except Exception:
            pass
        # Clear stored Wi-Fi
        try:
            from onboarding import wifi_manager
            wifi_manager.remove_credentials()
        except Exception:
            pass
        return jsonify({"ok": True, "message": "Wi-Fi reset. Reboot to re-enter credentials."})

    return jsonify({"ok": False, "message": f"Unknown action: {action}"})


def run_local_control():
    """Start the local control server."""
    log.info("Starting local control UI on port 8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_local_control()
