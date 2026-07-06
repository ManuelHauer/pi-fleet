#!/usr/bin/env python3
"""
Wi-Fi management via NetworkManager (nmcli) — replaces the v0.2
wpa_supplicant/hostapd/dnsmasq stack.

Why: Raspberry Pi OS Bookworm and Trixie ship with NetworkManager managing
wlan0. Writing wpa_supplicant.conf on those images does nothing, and manually
juggling hostapd against NM is a fight. nmcli gives us scanning, venue
profiles with autoconnect, AND the onboarding hotspot (NM 'shared' mode
includes DHCP; the captive-portal DNS wildcard comes from
/etc/NetworkManager/dnsmasq-shared.d/00-fleet-captive.conf, installed by the
golden image).

Connection profiles this module owns:
  fleet-venue    the venue Wi-Fi (autoconnect, priority 10)
  fleet-hotspot  the temporary onboarding AP (10.42.0.1)
"""
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger("nm-manager")

WIFI_IFACE = "wlan0"
VENUE_CON = "fleet-venue"
HOTSPOT_CON = "fleet-hotspot"
AP_IP = "10.42.0.1"   # NetworkManager shared-mode default gateway


def _nmcli(args: list, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(["nmcli"] + args, capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out.strip()
    except FileNotFoundError:
        log.error("nmcli not found — is NetworkManager installed?")
        return 127, "nmcli missing"
    except subprocess.TimeoutExpired:
        return 124, "nmcli timeout"
    except Exception as e:
        return 1, str(e)


# ── Device identity for the AP name ──

def get_device_serial_suffix() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("Serial"):
                return line.split(":")[-1].strip()[-4:].upper()
    except Exception:
        pass
    return "0000"


def get_ap_name() -> str:
    return f"AEC-PI-{get_device_serial_suffix()}"


def get_ap_password() -> str:
    """Deterministic per-device AP password (printed on deployment sheets)."""
    return f"aec{get_device_serial_suffix()}setup"


# ── Status ──

def get_current_ssid() -> str:
    rc, out = _nmcli(["-t", "-f", "ACTIVE,SSID", "dev", "wifi"], timeout=15)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "yes" and parts[1]:
                if not is_hotspot_active():
                    return parts[1]
                # In hotspot mode the 'active' SSID is our own AP — not a venue.
                return ""
    return ""


def get_ip() -> str:
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=5).strip()
        for ip in out.split():
            if not ip.startswith(("169.254", "10.42.", "192.168.4")) and "." in ip:
                return ip
        return ""
    except Exception:
        return ""


def set_wifi_country(code: str):
    """Set the Wi-Fi regulatory domain (unblocks radio on fresh images)."""
    if not code:
        return
    if Path("/usr/bin/raspi-config").exists():
        subprocess.run(["raspi-config", "nonint", "do_wifi_country", code],
                       capture_output=True, timeout=20)
    else:
        subprocess.run(["iw", "reg", "set", code], capture_output=True, timeout=10)
    subprocess.run(["rfkill", "unblock", "wifi"], capture_output=True, timeout=10)


# ── Scanning ──

def scan_networks() -> list:
    """Visible networks as [{ssid, signal, security}], strongest first.
    NOTE: scanning does not work while the hotspot is up (single radio) —
    the onboarding flow scans BEFORE starting the AP and serves the cache."""
    rc, out = _nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi",
                      "list", "ifname", WIFI_IFACE, "--rescan", "yes"],
                     timeout=25)
    if rc != 0:
        log.warning(f"Wi-Fi scan failed: {out}")
        return []
    best = {}
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 2 or not parts[0]:
            continue
        ssid = parts[0]
        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0
        security = ":".join(parts[2:]) if len(parts) > 2 else ""
        if ssid not in best or best[ssid]["signal"] < signal:
            best[ssid] = {"ssid": ssid, "signal": signal,
                          "security": security or "open"}
    return sorted(best.values(), key=lambda n: -n["signal"])


# ── Venue connection ──

def has_venue_profile() -> bool:
    rc, out = _nmcli(["-t", "-f", "NAME", "con", "show"], timeout=15)
    return rc == 0 and VENUE_CON in out.splitlines()


def _wait_for_ip(timeout_sec: int) -> bool:
    start = time.time()
    while time.time() - start < timeout_sec:
        if get_ip():
            return True
        time.sleep(2)
    return False


def write_venue_profile(ssid: str, password: str, hidden: bool = False) -> bool:
    """Create/replace the venue Wi-Fi profile (autoconnect on every boot)."""
    _nmcli(["con", "delete", VENUE_CON], timeout=15)  # ignore result
    args = ["con", "add", "type", "wifi", "ifname", WIFI_IFACE,
            "con-name", VENUE_CON, "ssid", ssid,
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", "10"]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    rc, out = _nmcli(args, timeout=20)
    if rc != 0:
        log.error(f"Venue profile create failed: {out}")
        return False
    log.info(f"Venue profile written: SSID={ssid} hidden={hidden}")
    return True


def connect_venue(timeout_sec: int = 45) -> bool:
    """Bring the venue profile up and wait for an IP."""
    rc, out = _nmcli(["con", "up", VENUE_CON, "ifname", WIFI_IFACE],
                     timeout=timeout_sec)
    if rc != 0:
        log.warning(f"con up {VENUE_CON} failed: {out}")
        return False
    ok = _wait_for_ip(timeout_sec=20)
    if ok:
        log.info(f"Connected: {get_current_ssid()} @ {get_ip()}")
    return ok


def forget_venue_wifi():
    _nmcli(["con", "delete", VENUE_CON], timeout=15)
    log.info("Venue Wi-Fi profile removed")


# ── Onboarding hotspot ──

def start_hotspot() -> bool:
    ap_name, ap_pass = get_ap_name(), get_ap_password()
    log.info(f"Starting hotspot {ap_name} on {AP_IP}")
    _nmcli(["con", "delete", HOTSPOT_CON], timeout=15)  # clean slate
    rc, out = _nmcli(["dev", "wifi", "hotspot", "ifname", WIFI_IFACE,
                      "con-name", HOTSPOT_CON,
                      "ssid", ap_name, "password", ap_pass], timeout=30)
    if rc != 0:
        log.error(f"Hotspot start failed: {out}")
        return False
    # Don't let the hotspot profile resurrect itself after reboots
    _nmcli(["con", "modify", HOTSPOT_CON, "connection.autoconnect", "no"],
           timeout=15)
    return True


def stop_hotspot():
    _nmcli(["con", "down", HOTSPOT_CON], timeout=20)
    _nmcli(["con", "delete", HOTSPOT_CON], timeout=15)
    log.info("Hotspot stopped")


def is_hotspot_active() -> bool:
    rc, out = _nmcli(["-t", "-f", "NAME", "con", "show", "--active"], timeout=15)
    return rc == 0 and HOTSPOT_CON in out.splitlines()
