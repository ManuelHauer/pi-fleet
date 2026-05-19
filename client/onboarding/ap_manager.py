#!/usr/bin/env python3
"""
AP hotspot manager using hostapd + dnsmasq.
Creates a temporary Wi-Fi access point for onboarding.
"""
import subprocess
import logging
import time
from pathlib import Path

log = logging.getLogger("ap-manager")

HOSTAPD_CONF = Path("/tmp/fleet-hostapd.conf")
DNSMASQ_CONF = Path("/tmp/fleet-dnsmasq.conf")
AP_INTERFACE = "wlan0"
AP_IP = "192.168.4.1"
AP_SUBNET = "192.168.4.0/24"
AP_DHCP_START = "192.168.4.10"
AP_DHCP_END = "192.168.4.50"


def get_device_serial_suffix() -> str:
    """Get last 4 chars of Pi serial for unique AP name."""
    try:
        serial_path = Path("/proc/cpuinfo")
        if serial_path.exists():
            for line in serial_path.read_text().split("\n"):
                if line.startswith("Serial"):
                    serial = line.split(":")[-1].strip()
                    return serial[-4:].upper()
    except Exception:
        pass
    return "0000"


def get_ap_name() -> str:
    return f"AEC-PI-{get_device_serial_suffix()}"


def get_ap_password() -> str:
    """Generate deterministic but unique AP password from serial."""
    serial = get_device_serial_suffix()
    # Simple deterministic password: aec + serial + fixed suffix
    # In production, use pre-printed deployment sheets
    return f"aec{serial}setup"


def start_ap() -> bool:
    """Start the AP hotspot."""
    ap_name = get_ap_name()
    ap_pass = get_ap_password()

    log.info(f"Starting AP: {ap_name} (pass: {ap_pass})")

    try:
        # Stop interfering services
        subprocess.run(["sudo", "systemctl", "stop", "wpa_supplicant"], capture_output=True, timeout=5)
        time.sleep(1)

        # Configure interface
        subprocess.run(["sudo", "ip", "link", "set", AP_INTERFACE, "down"], capture_output=True, timeout=5)
        subprocess.run(["sudo", "ip", "addr", "flush", "dev", AP_INTERFACE], capture_output=True, timeout=5)
        subprocess.run(["sudo", "ip", "addr", "add", f"{AP_IP}/24", "dev", AP_INTERFACE], capture_output=True, timeout=5)
        subprocess.run(["sudo", "ip", "link", "set", AP_INTERFACE, "up"], capture_output=True, timeout=5)
        time.sleep(1)

        # Write hostapd config
        HOSTAPD_CONF.write_text(f"""interface={AP_INTERFACE}
driver=nl80211
ssid={ap_name}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={ap_pass}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
""")

        # Write dnsmasq config (DHCP + DNS redirect for captive portal)
        DNSMASQ_CONF.write_text(f"""interface={AP_INTERFACE}
dhcp-range={AP_DHCP_START},{AP_DHCP_END},255.255.255.0,24h
address=/#/{AP_IP}
""")

        # Start dnsmasq
        subprocess.run(["sudo", "dnsmasq", "-C", str(DNSMASQ_CONF), "--no-daemon"],
                       capture_output=True, timeout=2)
        # dnsmasq in background
        subprocess.Popen(["sudo", "dnsmasq", "-C", str(DNSMASQ_CONF)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        # Start hostapd
        subprocess.Popen(["sudo", "hostapd", str(HOSTAPD_CONF)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)

        # Enable IP forwarding (not strictly needed but helps captive portal detection)
        subprocess.run(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True, timeout=5)

        log.info(f"AP started: {ap_name} on {AP_IP}")
        return True

    except Exception as e:
        log.error(f"Failed to start AP: {e}")
        return False


def stop_ap():
    """Stop the AP hotspot and restore normal Wi-Fi."""
    log.info("Stopping AP…")
    try:
        subprocess.run(["sudo", "pkill", "-f", "fleet-hostapd"], capture_output=True, timeout=5)
        subprocess.run(["sudo", "pkill", "-f", "fleet-dnsmasq"], capture_output=True, timeout=5)
        subprocess.run(["sudo", "killall", "hostapd"], capture_output=True, timeout=5)
        subprocess.run(["sudo", "killall", "dnsmasq"], capture_output=True, timeout=5)
        time.sleep(1)

        # Restore interface
        subprocess.run(["sudo", "ip", "addr", "flush", "dev", AP_INTERFACE], capture_output=True, timeout=5)
        subprocess.run(["sudo", "ip", "link", "set", AP_INTERFACE, "down"], capture_output=True, timeout=5)
        time.sleep(1)

        # Restart wpa_supplicant
        subprocess.run(["sudo", "systemctl", "start", "wpa_supplicant"], capture_output=True, timeout=10)
        time.sleep(2)

        log.info("AP stopped, wpa_supplicant restored")
    except Exception as e:
        log.warning(f"AP stop error: {e}")


def is_ap_running() -> bool:
    """Check if hostapd is running."""
    try:
        result = subprocess.run(["pgrep", "hostapd"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False
