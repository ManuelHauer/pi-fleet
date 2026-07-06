#!/usr/bin/env python3
"""
Captive portal web app for Wi-Fi onboarding (v0.3, NetworkManager-based).

Runs on the Pi's hotspot at http://10.42.0.1 — a technician connects with a
phone (AEC-PI-XXXX) and enters venue Wi-Fi credentials.

Connection handling is asynchronous: the phone gets an immediate "connecting…"
page (the old flow tried to answer AFTER tearing down the AP, so the success
response never reached the phone). The actual switch runs in a background
thread; the page polls /api/progress while the AP is still up, and the HDMI
screen shows the authoritative result throughout.
"""
import logging
import threading
import time

from flask import Flask, render_template, request, redirect, jsonify

import nm_manager
import hdmi_status

log = logging.getLogger("captive-portal")

app = Flask(__name__, template_folder="templates")

# State
_device_id = ""
_done_event = threading.Event()
_cached_networks: list = []
_progress = {"phase": "idle", "ssid": "", "error": ""}
_connect_lock = threading.Lock()


@app.route("/")
def index():
    return render_template("setup.html",
                           device_id=_device_id,
                           ap_name=nm_manager.get_ap_name(),
                           networks=_cached_networks)


# Captive-portal detection endpoints of the various OS vendors
@app.route("/generate_204")
@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
@app.route("/ncsi.txt")
@app.route("/connecttest.txt")
@app.route("/redirect")
def captive_detect():
    return redirect("/")


def _do_connect(ssid: str, password: str):
    """Background worker: AP down → join venue Wi-Fi → signal, or AP back up."""
    global _progress
    try:
        hdmi_status.show_connecting(ssid)
        _progress = {"phase": "connecting", "ssid": ssid, "error": ""}

        # Give the phone a moment to finish loading the status page before
        # the hotspot (and with it this HTTP connection) goes away.
        time.sleep(3)
        nm_manager.stop_hotspot()

        if not nm_manager.write_venue_profile(ssid, password):
            raise RuntimeError("Could not store Wi-Fi profile")

        if nm_manager.connect_venue(timeout_sec=45):
            _progress = {"phase": "connected", "ssid": nm_manager.get_current_ssid(),
                         "error": ""}
            log.info(f"✅ Connected: {nm_manager.get_current_ssid()} @ {nm_manager.get_ip()}")
            _done_event.set()
            return

        raise RuntimeError(f"Could not connect to '{ssid}' — check password and range")

    except Exception as e:
        log.warning(f"Connect failed: {e}")
        nm_manager.forget_venue_wifi()
        hdmi_status.show_failed(str(e))
        time.sleep(2)
        nm_manager.start_hotspot()
        hdmi_status.show_setup_screen(nm_manager.get_ap_name(),
                                      nm_manager.get_ap_password(),
                                      portal_url=f"http://{nm_manager.AP_IP}")
        _progress = {"phase": "failed", "ssid": ssid, "error": str(e)}


@app.route("/connect", methods=["POST"])
def connect():
    ssid = (request.form.get("ssid") or request.form.get("ssid_manual") or "").strip()
    password = request.form.get("password", "")

    if not ssid:
        return redirect("/")

    with _connect_lock:
        if _progress.get("phase") == "connecting":
            return redirect("/status-page")
        log.info(f"Connection request: SSID={ssid}")
        threading.Thread(target=_do_connect, args=(ssid, password),
                         daemon=True).start()

    return render_template("status.html", ssid=ssid, device_id=_device_id,
                           ap_name=nm_manager.get_ap_name())


@app.route("/status-page")
def status_page():
    return render_template("status.html", ssid=_progress.get("ssid", ""),
                           device_id=_device_id, ap_name=nm_manager.get_ap_name())


@app.route("/api/progress")
def api_progress():
    return jsonify(_progress)


@app.route("/status")
def status():
    return {
        "device_id": _device_id,
        "connected": bool(nm_manager.get_current_ssid()),
        "ssid": nm_manager.get_current_ssid(),
        "ip": nm_manager.get_ip(),
        "ap_running": nm_manager.is_hotspot_active(),
        "progress": _progress,
    }


def run_portal(device_id: str, done_event: threading.Event = None,
               networks: list = None):
    global _device_id, _done_event, _cached_networks
    _device_id = device_id
    if done_event is not None:
        _done_event = done_event
    _cached_networks = networks or []

    log.info(f"Starting captive portal on {nm_manager.AP_IP}:80 "
             f"({len(_cached_networks)} cached networks)")
    app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_portal("test-device-0000", threading.Event(),
               [{"ssid": "TestNet", "signal": 70, "security": "WPA2"}])
