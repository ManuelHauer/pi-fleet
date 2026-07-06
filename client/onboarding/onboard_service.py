#!/usr/bin/env python3
"""
Fleet Onboarding Service — main orchestrator (v0.3, NetworkManager-based).

Runs on every boot until /etc/fleet-client/onboard-done exists (systemd
ConditionPathExists). Order of attempts:

  1. fleet-setup.toml (pre-primed SD card) — apply [device]/[player]/[server],
     and if a [wifi] block is present, write the venue profile and connect.
     → zero-touch onboarding, no phone needed.
  2. Existing venue profile (fleet-venue) — reconnect.
  3. USB stick wifi.json — legacy fallback, kept from v0.2.
  4. Captive portal — hotspot AEC-PI-XXXX + phone setup page.

Success in any path → optional Tailscale mesh join (non-fatal) → write
onboard-done → clear the on-screen setup card. The captive portal never
times out: it waits for a technician for as long as it takes, while the
player/USB/SD kiosk paths keep working independently.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import nm_manager
import hdmi_status
import captive_portal
import setup_config

TAILSCALE_AUTHKEY_FILE = Path("/etc/fleet-client/tailscale-authkey")
ONBOARD_DONE = Path("/etc/fleet-client/onboard-done")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("onboard-service")


def get_device_id() -> str:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from identity import device_id
    return device_id()


def _tailscale_up(authkey: str, hostname: str, timeout_sec: int = 60) -> bool:
    """Bring up Tailscale. Non-fatal: returns False if it can't join.
    Meshless deployments (public HTTPS fleet server) simply skip this."""
    if not authkey:
        log.info("No Tailscale authkey present — skipping mesh join")
        return False
    if shutil.which("tailscale") is None:
        log.warning("tailscale binary not installed — skipping mesh join")
        return False
    try:
        subprocess.run(["sudo", "systemctl", "enable", "--now", "tailscaled"],
                       check=False, timeout=30)
        cmd = ["sudo", "tailscale", "up",
               f"--authkey={authkey}",
               f"--hostname={hostname}",
               "--accept-routes=false",
               "--ssh"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if r.returncode != 0:
            log.error(f"tailscale up failed: {r.stderr.strip()}")
            return False
        r2 = subprocess.run(["tailscale", "ip", "-4"],
                            capture_output=True, text=True, timeout=10)
        if r2.returncode == 0 and r2.stdout.strip():
            log.info(f"Tailscale up: {r2.stdout.strip()}")
            return True
    except subprocess.TimeoutExpired:
        log.error("tailscale up timed out")
    except Exception as e:
        log.error(f"tailscale up exception: {e}")
    return False


def join_mesh_if_configured(device_id: str) -> bool:
    authkey = ""
    if TAILSCALE_AUTHKEY_FILE.exists():
        try:
            authkey = TAILSCALE_AUTHKEY_FILE.read_text().strip()
        except Exception as e:
            log.warning(f"Could not read {TAILSCALE_AUTHKEY_FILE}: {e}")
    return _tailscale_up(authkey, hostname=device_id)


def check_usb_wifi_fallback() -> bool:
    """Legacy path: wifi.json on a mounted USB stick."""
    for base in (Path("/media/pi"), Path("/media/usb"), Path("/mnt/usb")):
        if not base.exists():
            continue
        try:
            mounts = list(base.iterdir())
        except OSError:
            continue
        for mount in mounts:
            wifi_file = mount / "wifi.json"
            if not wifi_file.exists():
                continue
            try:
                data = json.loads(wifi_file.read_text())
                ssid, password = data.get("ssid"), data.get("password")
                if ssid and password:
                    log.info(f"USB wifi.json found: SSID={ssid}")
                    return nm_manager.write_venue_profile(ssid, password)
            except Exception as e:
                log.warning(f"USB wifi.json parse error: {e}")
    return False


def finish(device_id: str, via: str):
    """Common success path."""
    ssid, ip = nm_manager.get_current_ssid(), nm_manager.get_ip()
    log.info(f"Onboarding complete via {via}: SSID={ssid} IP={ip}")
    join_mesh_if_configured(device_id)
    try:
        ONBOARD_DONE.parent.mkdir(parents=True, exist_ok=True)
        ONBOARD_DONE.write_text(f"{via} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    except Exception as e:
        log.error(f"onboard-done write failed: {e}")
    hdmi_status.show_connected(ssid, ip, device_id)
    time.sleep(8)  # let the tech read it
    hdmi_status.clear()


def main():
    log.info("=" * 50)
    log.info("Fleet Onboarding Service starting (v0.3 / NetworkManager)")
    log.info("=" * 50)

    device_id = get_device_id()
    log.info(f"Device ID: {device_id}")

    # 1. Pre-primed SD card config
    cfg, cfg_path, is_new = setup_config.load_setup()
    if cfg and is_new:
        log.info(f"Applying pre-primed setup from {cfg_path}")
        setup_config.apply_non_wifi(cfg)
        wifi = setup_config.wifi_block(cfg)
        if wifi:
            if wifi.get("country"):
                nm_manager.set_wifi_country(wifi["country"])
            hdmi_status.show_connecting(wifi["ssid"])
            if nm_manager.write_venue_profile(wifi["ssid"], wifi["password"],
                                              hidden=wifi["hidden"]):
                if nm_manager.connect_venue(timeout_sec=60):
                    setup_config.mark_applied(cfg_path)
                    finish(device_id, via="fleet-setup.toml")
                    return
                log.warning("Pre-primed Wi-Fi failed to connect — "
                            "falling through to portal setup")
        setup_config.mark_applied(cfg_path)  # non-wifi parts are applied either way

    # 2. Existing venue profile (previous onboarding)
    if nm_manager.has_venue_profile():
        if nm_manager.get_current_ssid() and nm_manager.get_ip():
            finish(device_id, via="existing profile (already connected)")
            return
        log.info("Venue profile exists — attempting reconnect…")
        if nm_manager.connect_venue(timeout_sec=45):
            finish(device_id, via="existing profile")
            return
        log.warning("Stored profile failed — entering setup mode")

    # 3. USB wifi.json fallback
    if check_usb_wifi_fallback() and nm_manager.connect_venue(timeout_sec=45):
        finish(device_id, via="usb wifi.json")
        return

    # 4. Captive portal
    log.info("No usable Wi-Fi — entering AP setup mode")

    # Scan BEFORE the hotspot claims the radio; the portal serves this cache.
    networks = nm_manager.scan_networks()
    log.info(f"Pre-AP scan: {len(networks)} networks visible")

    if not nm_manager.start_hotspot():
        log.error("Hotspot start failed — cannot onboard interactively")
        hdmi_status.show_failed("Hotspot could not start. Check hardware / reboot.")
        # Exit non-zero → systemd Restart=on-failure retries in a minute.
        sys.exit(1)

    hdmi_status.show_setup_screen(nm_manager.get_ap_name(),
                                  nm_manager.get_ap_password(),
                                  portal_url=f"http://{nm_manager.AP_IP}")

    done_event = threading.Event()
    portal_thread = threading.Thread(
        target=captive_portal.run_portal,
        args=(device_id, done_event, networks),
        daemon=True,
    )
    portal_thread.start()

    log.info(f"Setup mode active: AP={nm_manager.get_ap_name()} — waiting for technician "
             "(no timeout; player/USB/SD keep working independently)")

    while not done_event.is_set():
        done_event.wait(timeout=5)

    time.sleep(2)
    finish(device_id, via="captive portal")
    nm_manager.stop_hotspot()


if __name__ == "__main__":
    main()
