#!/usr/bin/env python3
"""
Wi-Fi credential management via wpa_supplicant.
Handles reading, writing, and testing Wi-Fi connections.
"""
import subprocess
import time
import logging
from pathlib import Path

log = logging.getLogger("wifi-manager")

WPA_CONF = Path("/etc/wpa_supplicant/wpa_supplicant.conf")
WPA_CONF_BACKUP = Path("/etc/wpa_supplicant/wpa_supplicant.conf.bak")
WIFI_INTERFACE = "wlan0"


def has_wifi_credentials() -> bool:
    """Check if wpa_supplicant.conf has any network blocks."""
    if not WPA_CONF.exists():
        return False
    content = WPA_CONF.read_text()
    return "network=" in content and "ssid=" in content


def get_current_ssid() -> str:
    """Get currently connected SSID, or empty string."""
    try:
        result = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def scan_networks() -> list:
    """Scan for available Wi-Fi networks. Returns list of SSIDs."""
    try:
        subprocess.run(["sudo", "iwlist", WIFI_INTERFACE, "scan"],
                       capture_output=True, timeout=15)
        result = subprocess.run(
            ["sudo", "iwlist", WIFI_INTERFACE, "scan"],
            capture_output=True, text=True, timeout=15
        )
        ssids = []
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("ESSID:"):
                ssid = line.split('"')[1] if '"' in line else ""
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
        return sorted(ssids)
    except Exception as e:
        log.warning(f"Wi-Fi scan failed: {e}")
        return []


def write_credentials(ssid: str, password: str, country: str = "AT") -> bool:
    """Write Wi-Fi credentials to wpa_supplicant.conf."""
    try:
        # Backup existing
        if WPA_CONF.exists():
            WPA_CONF_BACKUP.write_text(WPA_CONF.read_text())

        # Generate PSK hash using wpa_passphrase
        result = subprocess.run(
            ["wpa_passphrase", ssid, password],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            log.error(f"wpa_passphrase failed: {result.stderr}")
            return False

        # Extract the hashed PSK line (skip the plaintext comment)
        psk_block = ""
        for line in result.stdout.split("\n"):
            if not line.strip().startswith("#"):
                psk_block += line + "\n"

        config = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country={country}

{psk_block}"""

        WPA_CONF.write_text(config)
        log.info(f"Wi-Fi credentials written for SSID: {ssid}")
        return True
    except Exception as e:
        log.error(f"Failed to write credentials: {e}")
        return False


def connect(timeout_sec: int = 30) -> bool:
    """Reconfigure wpa_supplicant and wait for connection."""
    try:
        # Reconfigure
        subprocess.run(["wpa_cli", "-i", WIFI_INTERFACE, "reconfigure"],
                       capture_output=True, timeout=10)

        # Wait for connection
        start = time.time()
        while time.time() - start < timeout_sec:
            ssid = get_current_ssid()
            if ssid:
                log.info(f"Connected to: {ssid}")
                # Also get IP
                time.sleep(2)
                ip = get_ip()
                if ip:
                    log.info(f"Got IP: {ip}")
                    return True
            time.sleep(2)

        log.warning("Connection timeout")
        return False
    except Exception as e:
        log.error(f"Connection attempt failed: {e}")
        return False


def get_ip() -> str:
    """Get current IP address on wlan0."""
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        )
        ips = result.stdout.strip().split()
        for ip in ips:
            if not ip.startswith("169.254") and not ip.startswith("192.168.4"):
                return ip
        return ips[0] if ips else ""
    except Exception:
        return ""


def disconnect():
    """Disconnect from current network."""
    try:
        subprocess.run(["wpa_cli", "-i", WIFI_INTERFACE, "disconnect"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def remove_credentials():
    """Remove stored credentials (return to setup mode)."""
    if WPA_CONF.exists():
        # Write minimal config without network block
        WPA_CONF.write_text(
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "update_config=1\n"
            "country=AT\n"
        )
