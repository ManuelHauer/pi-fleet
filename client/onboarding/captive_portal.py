#!/usr/bin/env python3
"""
Captive portal web app for Wi-Fi onboarding.
Runs on the Pi's AP interface at http://192.168.4.1
Technicians connect via phone and enter venue Wi-Fi credentials.
"""
import logging
import threading
import time
from flask import Flask, render_template, request, redirect

import wifi_manager
import ap_manager
import hdmi_status

log = logging.getLogger("captive-portal")

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "fleet-onboard-local"

# State
_device_id = ""
_shutdown_flag = threading.Event()


def set_device_id(did: str):
    global _device_id
    _device_id = did


@app.route("/")
def index():
    """Main setup page with Wi-Fi scan results."""
    networks = wifi_manager.scan_networks()
    return render_template("setup.html",
                           device_id=_device_id,
                           networks=networks)


@app.route("/scan")
def scan():
    """Rescan and redirect to main page."""
    return redirect("/")


@app.route("/generate_204")
@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
@app.route("/ncsi.txt")
@app.route("/connecttest.txt")
@app.route("/redirect")
def captive_detect():
    """Handle captive portal detection from various OS vendors."""
    return redirect("/")


@app.route("/connect", methods=["POST"])
def connect():
    """Process Wi-Fi connection request."""
    ssid = request.form.get("ssid") or request.form.get("ssid_manual", "")
    password = request.form.get("password", "")
    label = request.form.get("label", "")

    if not ssid or not password:
        return redirect("/")

    log.info(f"Connection request: SSID={ssid}, label={label}")

    # Show connecting on HDMI
    hdmi_status.show_connecting(ssid)

    # Write credentials
    if not wifi_manager.write_credentials(ssid, password):
        hdmi_status.show_failed("Could not save credentials")
        return render_template("status.html",
                               success=False,
                               error="Failed to save Wi-Fi credentials",
                               device_id=_device_id)

    # Stop AP
    log.info("Stopping AP for connection attempt…")
    ap_manager.stop_ap()
    time.sleep(2)

    # Try to connect
    if wifi_manager.connect(timeout_sec=30):
        ip = wifi_manager.get_ip()
        current_ssid = wifi_manager.get_current_ssid()

        hdmi_status.show_connected(current_ssid, ip, _device_id)
        log.info(f"✅ Connected: {current_ssid} @ {ip}")

        # Signal the onboard service to proceed
        _shutdown_flag.set()

        return render_template("status.html",
                               success=True,
                               ssid=current_ssid,
                               ip=ip,
                               device_id=_device_id)
    else:
        # Connection failed — restart AP for retry
        hdmi_status.show_failed("Could not connect to network")
        log.warning("Connection failed, restarting AP…")
        wifi_manager.remove_credentials()
        ap_manager.start_ap()

        ap_name = ap_manager.get_ap_name()
        ap_pass = ap_manager.get_ap_password()
        hdmi_status.show_setup_screen(ap_name, ap_pass)

        return render_template("status.html",
                               success=False,
                               error=f"Could not connect to '{ssid}'. Check password and range.",
                               device_id=_device_id)


@app.route("/status")
def status():
    """Simple status endpoint."""
    ssid = wifi_manager.get_current_ssid()
    ip = wifi_manager.get_ip()
    return {
        "device_id": _device_id,
        "connected": bool(ssid),
        "ssid": ssid,
        "ip": ip,
        "ap_running": ap_manager.is_ap_running(),
    }


def run_portal(device_id: str, shutdown_event: threading.Event = None):
    """Start the captive portal server."""
    global _shutdown_flag
    if shutdown_event:
        _shutdown_flag = shutdown_event
    set_device_id(device_id)

    log.info(f"Starting captive portal on {ap_manager.AP_IP}:80")
    app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_portal("test-device-0000")
