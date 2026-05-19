#!/usr/bin/env python3
"""
Fleet Onboarding Service — Main orchestrator.
Runs as systemd service on first boot (before fleet-client).

Flow:
1. Check if Wi-Fi credentials exist
2. If yes → skip onboarding, exit (let fleet-client handle things)
3. If no → start AP + captive portal + HDMI status
4. Wait for successful connection
5. Exit (fleet-client takes over)
"""
import logging
import os
import sys
import threading
import time

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import wifi_manager
import ap_manager
import hdmi_status
import captive_portal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/fleet-onboard.log", mode="a"),
    ]
)
log = logging.getLogger("onboard-service")


def get_device_id() -> str:
    """Read device ID from fleet-client config or generate one."""
    from pathlib import Path
    id_file = Path("/etc/fleet-client/device-id")
    if id_file.exists():
        return id_file.read_text().strip()
    
    # Generate from machine-id
    machine_id = Path("/etc/machine-id")
    if machine_id.exists():
        raw = machine_id.read_text().strip()
        did = f"pi-{raw[:12]}"
    else:
        import uuid
        did = f"pi-{uuid.uuid4().hex[:12]}"
    
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(did)
    return did


def check_usb_fallback() -> bool:
    """Check for USB stick with wifi.json fallback credentials."""
    import json
    from pathlib import Path
    
    usb_paths = [
        Path("/media/pi"),
        Path("/media/usb"),
        Path("/mnt/usb"),
    ]
    
    for base in usb_paths:
        if not base.exists():
            continue
        for mount in base.iterdir():
            wifi_file = mount / "wifi.json"
            if wifi_file.exists():
                try:
                    data = json.loads(wifi_file.read_text())
                    ssid = data.get("ssid")
                    password = data.get("password")
                    label = data.get("label", "")
                    if ssid and password:
                        log.info(f"USB fallback found: SSID={ssid}")
                        if wifi_manager.write_credentials(ssid, password):
                            return True
                except Exception as e:
                    log.warning(f"USB wifi.json parse error: {e}")
    
    return False


def main():
    log.info("=" * 50)
    log.info("Fleet Onboarding Service starting")
    log.info("=" * 50)
    
    device_id = get_device_id()
    log.info(f"Device ID: {device_id}")
    
    # Check if already configured
    if wifi_manager.has_wifi_credentials():
        current = wifi_manager.get_current_ssid()
        if current:
            log.info(f"Already connected to: {current} — skipping onboarding")
            return
        else:
            log.info("Credentials exist but not connected — attempting connection…")
            if wifi_manager.connect(timeout_sec=20):
                ip = wifi_manager.get_ip()
                log.info(f"Connected: {wifi_manager.get_current_ssid()} @ {ip}")
                return
            log.warning("Stored credentials failed — entering setup mode")
    
    # Check USB fallback
    if check_usb_fallback():
        log.info("USB credentials loaded — attempting connection…")
        if wifi_manager.connect(timeout_sec=20):
            ip = wifi_manager.get_ip()
            log.info(f"Connected via USB config: {wifi_manager.get_current_ssid()} @ {ip}")
            return
    
    # No credentials — enter setup mode
    log.info("No Wi-Fi credentials — entering AP setup mode")
    
    # Start AP
    if not ap_manager.start_ap():
        log.error("Failed to start AP — cannot proceed with onboarding")
        hdmi_status.show_failed("AP setup failed. Check hardware.")
        return
    
    ap_name = ap_manager.get_ap_name()
    ap_pass = ap_manager.get_ap_password()
    
    # Show setup screen on HDMI
    hdmi_status.show_setup_screen(ap_name, ap_pass)
    
    # Start captive portal in a thread
    shutdown_event = threading.Event()
    portal_thread = threading.Thread(
        target=captive_portal.run_portal,
        args=(device_id, shutdown_event),
        daemon=True
    )
    portal_thread.start()
    
    log.info(f"Setup mode active: AP={ap_name} pass={ap_pass}")
    log.info("Waiting for technician to complete Wi-Fi setup…")
    
    # Wait for successful connection (portal sets the event)
    while not shutdown_event.is_set():
        shutdown_event.wait(timeout=5)
    
    # Give the portal response time to render
    time.sleep(3)
    
    log.info("Onboarding complete — fleet-client will take over")
    
    # Final cleanup: make sure AP is off
    ap_manager.stop_ap()


if __name__ == "__main__":
    main()
