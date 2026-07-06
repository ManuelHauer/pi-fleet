#!/usr/bin/env python3
"""
Local Control Web UI — runs on each Raspberry Pi (port 8080).

The technician's on-site tool: connect a phone to the same network as the Pi
(or its hotspot) and open http://<pi-ip>:8080. Everything here works OFFLINE —
no fleet server needed. Server-pushed settings and local edits meet in
player-settings.json; the last writer wins and the dashboard always sees the
truth via heartbeat.

Controls: volume/mute, screen rotation, slideshow slide duration, restart
player, check for updates, identify (on-screen badge), play from SD card,
Wi-Fi reset, reboot.
"""
import json
import logging
import os
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, render_template_string, request, jsonify, session, redirect

from player_settings import load_settings, save_settings

log = logging.getLogger("local-control")

app = Flask(__name__)

MEDIA_BASE = Path("/opt/fleet-media")
STATE_FILE = MEDIA_BASE / "state.json"
OSD_FILE = MEDIA_BASE / "osd.json"
RESTART_TRIGGER = MEDIA_BASE / ".restart-player"
SD_MEDIA_DIR = Path("/media/fleet-sd")
SD_REIMPORT_TRIGGER = Path("/tmp/fleet-sd-reimport")
MPV_IPC_SOCKET = "/tmp/fleet-mpv-ipc"

# Auth — single shared password for all technicians (set via config.json
# "local_password" or FLEET_LOCAL_PASSWORD env; default is for lab only).
LOCAL_AUTH_PASSWORD = os.environ.get("FLEET_LOCAL_PASSWORD", "aec2026")


def _load_config() -> dict:
    try:
        p = Path("/etc/fleet-client/config.json")
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _load_local_password() -> str:
    return _load_config().get("local_password", LOCAL_AUTH_PASSWORD)


# Session signing key: derived from the local password + device id so it is
# stable across restarts but not a hardcoded constant in a public repo.
def _secret_key() -> str:
    device = ""
    try:
        device = Path("/etc/fleet-client/device-id").read_text().strip()
    except Exception:
        pass
    return f"fleet-local::{_load_local_password()}::{device}"


app.config["SECRET_KEY"] = _secret_key()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json:
                return jsonify({"error": "Not authenticated"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


# ── Device / player state helpers ──

def _get_device_info() -> dict:
    info = {"hostname": socket.gethostname(), "device_id": "", "ip": "",
            "group": _load_config().get("group", "default")}
    try:
        p = Path("/etc/fleet-client/device-id")
        if p.exists():
            info["device_id"] = p.read_text().strip()
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


def _mpv_ipc(cmd_dict: dict):
    import socket as _sock
    try:
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(3)
        s.connect(MPV_IPC_SOCKET)
        s.sendall((json.dumps(cmd_dict) + "\n").encode())
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


def _read_state_json() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _media_type(f: Path) -> str:
    ext = f.suffix.lower()
    if ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}:
        return "video"
    if ext in {".mp3", ".wav", ".flac", ".ogg", ".aac"}:
        return "audio"
    return "image"


def _get_current_media() -> list:
    current = MEDIA_BASE / "current"
    if not current.exists():
        return []
    try:
        return sorted([
            {"name": f.name, "size": f.stat().st_size, "type": _media_type(f)}
            for f in current.iterdir()
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in {
                ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v",
                ".mp3", ".wav", ".flac", ".ogg", ".aac",
                ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
            }
        ], key=lambda x: x["name"])
    except OSError:
        return []


def _sd_has_media() -> bool:
    if not SD_MEDIA_DIR.is_dir():
        return False
    try:
        return any(
            p.is_file() and not p.name.startswith(".") and p.suffix.lower() in {
                ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v",
                ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
                ".mp3", ".wav", ".flac", ".ogg", ".aac"}
            for p in SD_MEDIA_DIR.iterdir()
        )
    except OSError:
        return False


def _status_payload() -> dict:
    st = _read_state_json()
    settings = load_settings()
    return {
        "device": _get_device_info(),
        "settings": settings,
        "media_count": len(_get_current_media()),
        "manifest_version": st.get("current_version") or "—",
        "pinned": bool(st.get("pinned")),
        "pinned_source": st.get("pinned_source", ""),
        "state": st.get("state", "—"),
        "offline_reason": st.get("offline_reason", ""),
        "sd_has_media": _sd_has_media(),
    }


# ── HTML (single page, phone-first) ──

CONTROL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{{ s.device.hostname }} — Fleet Control</title>
<style>
  :root {
    --bg:#0b0b12; --card:#15151f; --line:#272736; --ink:#e8e8f0;
    --dim:#8f8fa8; --accent:#7c6cf0; --accent-soft:rgba(124,108,240,.16);
    --ok:#3ddc84; --warn:#ffb020; --bad:#ff6b6b;
  }
  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { font-family:-apple-system,system-ui,sans-serif; background:var(--bg); color:var(--ink);
         min-height:100vh; padding:14px 14px calc(24px + env(safe-area-inset-bottom)); max-width:560px; margin:0 auto; }
  .header { text-align:center; padding:14px 0 6px; }
  .header h1 { font-size:19px; font-weight:700; }
  .header .sub { font-size:12px; color:var(--dim); margin-top:5px; font-family:ui-monospace,monospace; }
  .statebadge { display:inline-block; margin-top:8px; padding:4px 12px; border-radius:999px;
                font-size:12px; font-weight:600; letter-spacing:.3px; border:1px solid var(--line); }
  .st-ok  { color:var(--ok);  border-color:rgba(61,220,132,.35); background:rgba(61,220,132,.08); }
  .st-warn{ color:var(--warn);border-color:rgba(255,176,32,.35); background:rgba(255,176,32,.08); }
  .st-dim { color:var(--dim); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin:12px 0; }
  .card-title { font-size:12px; color:var(--dim); text-transform:uppercase; letter-spacing:.8px; margin-bottom:14px; font-weight:600; }
  .pinbar { display:flex; align-items:center; gap:12px; border:1px solid rgba(124,108,240,.4);
            background:var(--accent-soft); border-radius:14px; padding:14px 16px; margin:12px 0; }
  .pinbar .t { font-weight:600; font-size:14px; }
  .pinbar .d { font-size:12px; color:var(--dim); margin-top:2px; }

  /* Segmented control (rotation) */
  .seg { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
  .seg button { padding:14px 0; border:1px solid var(--line); border-radius:10px; background:#1b1b28;
                color:var(--ink); font-size:15px; font-weight:600; cursor:pointer; }
  .seg button.active { background:var(--accent); border-color:var(--accent); color:#fff; }

  /* Stepper (slide duration) */
  .stepper { display:flex; align-items:center; gap:10px; }
  .stepper button { width:56px; height:52px; border:1px solid var(--line); border-radius:10px;
                    background:#1b1b28; color:var(--ink); font-size:22px; cursor:pointer; flex:0 0 auto; }
  .stepper .val { flex:1; text-align:center; font-size:30px; font-weight:700; color:var(--accent); }
  .stepper .val small { font-size:13px; color:var(--dim); font-weight:400; }
  .hint { font-size:11px; color:var(--dim); margin-top:10px; text-align:center; }

  /* Volume */
  .vol-wrap { text-align:center; }
  .vol-display { font-size:42px; font-weight:700; color:var(--accent); }
  .vol-display .pct { font-size:18px; color:var(--dim); }
  input[type=range] { width:100%; height:44px; -webkit-appearance:none; appearance:none; background:transparent; margin:8px 0 4px; }
  input[type=range]::-webkit-slider-runnable-track { height:8px; background:#23232f; border-radius:4px; }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:30px; height:30px; margin-top:-11px;
    background:var(--accent); border-radius:50%; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.4); }
  .vol-btns { display:flex; gap:8px; justify-content:center; margin-top:6px; }
  .vol-btn { padding:12px 0; border:1px solid var(--line); border-radius:10px; background:#1b1b28;
             color:var(--ink); font-size:17px; cursor:pointer; flex:1; max-width:110px; }
  .vol-btn.muted { color:var(--bad); border-color:rgba(255,107,107,.4); background:rgba(255,107,107,.08); }

  .info-grid { display:grid; grid-template-columns:1fr 1fr; gap:6px 10px; }
  .info-item { padding:7px 4px; }
  .info-label { font-size:10px; color:var(--dim); text-transform:uppercase; letter-spacing:.5px; }
  .info-value { font-size:14px; margin-top:3px; word-break:break-all; }

  .media-item { display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid #1d1d2a; font-size:13px; }
  .media-item:last-child { border-bottom:none; }
  .media-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .media-size { color:var(--dim); font-size:11px; flex:0 0 auto; }

  .action-btn { display:flex; align-items:center; justify-content:center; gap:8px; width:100%;
                padding:15px; margin:8px 0; border:1px solid var(--line); border-radius:10px;
                background:#1b1b28; color:var(--ink); font-size:14px; font-weight:500; cursor:pointer; }
  .action-btn:active { background:var(--accent-soft); }
  .action-btn.danger { border-color:rgba(255,107,107,.35); color:var(--bad); }
  .action-btn:disabled { opacity:.4; }

  .toast { position:fixed; bottom:calc(22px + env(safe-area-inset-bottom)); left:50%; transform:translateX(-50%);
           padding:11px 22px; background:#1e1e2c; border:1px solid var(--accent); border-radius:10px;
           font-size:13px; z-index:100; max-width:88vw; text-align:center; }
</style>
</head>
<body>
  <div class="header">
    <h1>{{ s.device.hostname }}</h1>
    <div class="sub">{{ s.device.device_id }} · {{ s.device.group }} · {{ s.device.ip }}</div>
    <div id="stateBadge" class="statebadge">…</div>
  </div>

  <div id="pinbar" class="pinbar" style="display:none">
    <span style="font-size:22px" id="pinIcon">🔌</span>
    <div style="flex:1">
      <div class="t" id="pinTitle">Pinned</div>
      <div class="d">Server updates are ignored until released in the dashboard.</div>
    </div>
  </div>

  <!-- Playback settings -->
  <div class="card">
    <div class="card-title">Screen rotation</div>
    <div class="seg" id="rotSeg">
      <button data-rot="0">0°</button>
      <button data-rot="90">90°</button>
      <button data-rot="180">180°</button>
      <button data-rot="270">270°</button>
    </div>
    <div class="hint">Applied instantly — playback keeps running.</div>
  </div>

  <div class="card">
    <div class="card-title">Slide duration (images)</div>
    <div class="stepper">
      <button onclick="stepDuration(-5)">−5</button>
      <button onclick="stepDuration(-1)">−</button>
      <div class="val"><span id="durVal">10</span><small> sec / slide</small></div>
      <button onclick="stepDuration(1)">+</button>
      <button onclick="stepDuration(5)">+5</button>
    </div>
    <div class="hint">Takes effect from the next slide. Videos always play full length.</div>
  </div>

  <div class="card">
    <div class="card-title">Volume</div>
    <div class="vol-wrap">
      <div class="vol-display"><span id="volVal">100</span><span class="pct">%</span></div>
      <input type="range" id="volSlider" min="0" max="200" value="100" oninput="volSliderInput(this.value)">
      <div class="vol-btns">
        <button class="vol-btn" onclick="adjustVolume(-10)">−10</button>
        <button class="vol-btn" id="muteBtn" onclick="toggleMute()">🔇 Mute</button>
        <button class="vol-btn" onclick="adjustVolume(10)">+10</button>
      </div>
    </div>
  </div>

  <!-- Status -->
  <div class="card">
    <div class="card-title">Status</div>
    <div class="info-grid">
      <div class="info-item"><div class="info-label">Version</div><div class="info-value" id="stVersion">—</div></div>
      <div class="info-item"><div class="info-label">Media files</div><div class="info-value" id="stMedia">—</div></div>
      <div class="info-item"><div class="info-label">State</div><div class="info-value" id="stState">—</div></div>
      <div class="info-item"><div class="info-label">SD media</div><div class="info-value" id="stSd">—</div></div>
    </div>
  </div>

  <!-- Current media -->
  <div class="card">
    <div class="card-title">Current media</div>
    {% for m in media %}
    <div class="media-item">
      <span>{{ '🎥' if m.type == 'video' else ('🎵' if m.type == 'audio' else '🖼') }}</span>
      <span class="media-name">{{ m.name }}</span>
      <span class="media-size">{{ (m.size / 1048576)|round(1) }} MB</span>
    </div>
    {% endfor %}
    {% if not media %}
    <div style="color:var(--dim);font-size:13px;text-align:center;padding:10px">No media loaded yet</div>
    {% endif %}
  </div>

  <!-- Actions -->
  <div class="card">
    <div class="card-title">Actions</div>
    <button class="action-btn" onclick="apiAction('force_osd')">📺 Show device info on screen (30s)</button>
    <button class="action-btn" onclick="apiAction('player_restart')">▶ Restart player</button>
    <button class="action-btn" onclick="apiAction('update_now')">⬇ Check server for updates</button>
    <button class="action-btn" id="sdBtn" onclick="apiAction('play_sd')">💾 Play from SD card</button>
    <button class="action-btn" onclick="if(confirm('Reset Wi-Fi? The Pi returns to setup mode on next reboot.'))apiAction('wifi_reset')">📶 Reset Wi-Fi</button>
    <button class="action-btn danger" onclick="if(confirm('Reboot device?'))apiAction('reboot')">⟳ Reboot device</button>
  </div>

<script>
let S = {{ s | tojson }};
let volTimer = null, durTimer = null, pendingDur = null;

function render() {
  const set = S.settings || {};
  // rotation
  document.querySelectorAll('#rotSeg button').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.rot) === (set.rotation || 0));
  });
  // duration (don't clobber while the user is mid-stepping)
  if (pendingDur === null) document.getElementById('durVal').textContent = set.image_duration_s ?? 10;
  // volume
  if (!volTimer) {
    document.getElementById('volVal').textContent = set.volume_pct ?? 100;
    document.getElementById('volSlider').value = set.volume_pct ?? 100;
  }
  const mb = document.getElementById('muteBtn');
  mb.classList.toggle('muted', !!set.muted);
  mb.textContent = set.muted ? '🔊 Unmute' : '🔇 Mute';
  // state badge
  const badge = document.getElementById('stateBadge');
  const st = S.state || '—';
  badge.textContent = st + (S.offline_reason ? ' · ' + S.offline_reason : '');
  badge.className = 'statebadge ' + (st === 'PLAYING_CONNECTED' ? 'st-ok' : (st === 'PLAYING_OFFLINE' ? 'st-warn' : 'st-dim'));
  // pin bar
  const pb = document.getElementById('pinbar');
  if (S.pinned) {
    pb.style.display = 'flex';
    document.getElementById('pinIcon').textContent = S.pinned_source === 'sdcard' ? '💾' : '🔌';
    document.getElementById('pinTitle').textContent =
      S.pinned_source === 'sdcard' ? 'Playing from SD card' : 'Pinned to USB media';
  } else pb.style.display = 'none';
  // status grid
  document.getElementById('stVersion').textContent = (S.manifest_version || '—').slice(0, 18);
  document.getElementById('stMedia').textContent = S.media_count;
  document.getElementById('stState').textContent = st;
  document.getElementById('stSd').textContent = S.sd_has_media ? 'present' : '—';
  document.getElementById('sdBtn').disabled = !S.sd_has_media;
}

function saveSettings(patch, msg) {
  fetch('/api/settings', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch) })
    .then(r => r.json()).then(d => {
      if (d.ok) { S.settings = d.settings; render(); if (msg) toast(msg); }
      else toast(d.message || 'Failed');
    }).catch(() => toast('Connection error'));
}

document.querySelectorAll('#rotSeg button').forEach(b => {
  b.onclick = () => saveSettings({rotation: parseInt(b.dataset.rot)}, 'Rotation: ' + b.dataset.rot + '°');
});

function stepDuration(delta) {
  const cur = pendingDur ?? (S.settings.image_duration_s || 10);
  pendingDur = Math.max(1, Math.min(3600, cur + delta));
  document.getElementById('durVal').textContent = pendingDur;
  clearTimeout(durTimer);
  durTimer = setTimeout(() => {
    const v = pendingDur; pendingDur = null;
    saveSettings({image_duration_s: v}, 'Slide duration: ' + v + 's');
  }, 600);
}

function volSliderInput(v) {
  document.getElementById('volVal').textContent = v;
  clearTimeout(volTimer);
  volTimer = setTimeout(() => { volTimer = null; saveSettings({volume_pct: parseInt(v)}, 'Volume: ' + v + '%'); }, 250);
}
function adjustVolume(delta) {
  const v = Math.max(0, Math.min(200, parseInt(document.getElementById('volSlider').value) + delta));
  document.getElementById('volSlider').value = v;
  volSliderInput(v);
}
function toggleMute() { saveSettings({muted: !S.settings.muted}); }

function apiAction(action) {
  fetch('/api/action', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}) })
    .then(r => r.json()).then(d => toast(d.message || action + ' sent'))
    .catch(() => toast('Connection error'));
}

function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast'; el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

setInterval(async () => {
  try {
    const r = await fetch('/api/status');
    if (r.ok) { S = await r.json(); render(); }
  } catch(e) {}
}, 5000);

render();
</script>
</body>
</html>"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Fleet — Login</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,system-ui,sans-serif; background:#0b0b12; color:#e8e8f0;
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
  .card { background:#15151f; border:1px solid #272736; border-radius:16px; padding:32px;
          width:100%; max-width:360px; text-align:center; }
  h1 { font-size:20px; margin-bottom:4px; }
  .sub { color:#8f8fa8; font-size:12px; margin-bottom:24px; }
  input { width:100%; padding:14px; border:1px solid #272736; border-radius:10px; background:#0b0b12;
          color:#e8e8f0; font-size:16px; outline:none; margin-bottom:16px; }
  input:focus { border-color:#7c6cf0; }
  .btn { width:100%; padding:15px; border:none; border-radius:10px; background:#7c6cf0;
         color:#fff; font-size:16px; font-weight:600; cursor:pointer; }
  .error { color:#ff6b6b; font-size:13px; margin-bottom:12px; }
</style>
</head>
<body>
<div class="card">
  <h1>🎬 Fleet Device Control</h1>
  <div class="sub">Technician access</div>
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
        if request.form.get("password", "") == _load_local_password():
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
    return render_template_string(CONTROL_HTML,
                                  s=_status_payload(),
                                  media=_get_current_media())


@app.route("/api/status")
@require_auth
def api_status():
    return jsonify(_status_payload())


@app.route("/api/settings", methods=["POST"])
@require_auth
def api_settings():
    """Patch playback settings. fleet_player applies the file change live via
    mpv IPC within a second; volume additionally goes through IPC here for
    zero-lag slider feedback."""
    patch = request.get_json(force=True) or {}
    allowed = {k: v for k, v in patch.items()
               if k in ("rotation", "image_duration_s", "volume_pct", "muted")}
    if not allowed:
        return jsonify({"ok": False, "message": "No valid settings in request"}), 400
    settings = save_settings(allowed, updated_by="local")
    if "volume_pct" in allowed:
        _mpv_ipc({"command": ["set_property", "volume", settings["volume_pct"]]})
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/action", methods=["POST"])
@require_auth
def api_action():
    action = (request.get_json(force=True) or {}).get("action", "")

    if action in ("vlc_restart", "player_restart"):
        try:
            RESTART_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
            RESTART_TRIGGER.touch()
        except Exception as e:
            return jsonify({"ok": False, "message": f"Trigger failed: {e}"})
        return jsonify({"ok": True, "message": "Player restart signaled"})

    elif action == "update_now":
        Path("/tmp/fleet-update-now").touch()
        return jsonify({"ok": True, "message": "Update check triggered"})

    elif action == "play_sd":
        if not _sd_has_media():
            return jsonify({"ok": False, "message": "No media on the SD card partition"})
        SD_REIMPORT_TRIGGER.touch()
        return jsonify({"ok": True, "message": "Switching to SD card media…"})

    elif action == "force_osd":
        info = _get_device_info()
        force_until = datetime.now(timezone.utc) + timedelta(seconds=30)
        payload = {
            "message": f"Device: {info.get('device_id','?')} · IP: {info.get('ip','?')}",
            "force_until": force_until.isoformat(),
            "kind": "info",
        }
        try:
            OSD_FILE.parent.mkdir(parents=True, exist_ok=True)
            OSD_FILE.write_text(json.dumps(payload))
        except Exception as e:
            return jsonify({"ok": False, "message": f"OSD write failed: {e}"})
        return jsonify({"ok": True, "message": "Status overlay on for 30s"})

    elif action == "reboot":
        subprocess.Popen(["sudo", "reboot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "message": "Rebooting…"})

    elif action == "wifi_reset":
        try:
            Path("/etc/fleet-client/onboard-done").unlink(missing_ok=True)
        except Exception:
            pass
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent / "onboarding"))
            import nm_manager
            nm_manager.forget_venue_wifi()
        except Exception as e:
            log.warning(f"Wi-Fi forget failed (will still re-onboard on reboot): {e}")
        return jsonify({"ok": True, "message": "Wi-Fi reset. Reboot to re-enter credentials."})

    return jsonify({"ok": False, "message": f"Unknown action: {action}"})


def run_local_control():
    log.info("Starting local control UI on port 8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_local_control()
