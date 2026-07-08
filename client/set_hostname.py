#!/usr/bin/env python3
"""
Fleet hostname — <prefix>-<3-hex-checksum-of-device-id>, e.g. aef-pi-4a7.

Every Pi gets a stable, human-distinguishable hostname derived from its
device ID (which comes from the SoC serial, so it survives SD cloning —
two cloned cards in two Pis get two different hostnames automatically).

Suffix = first 3 hex chars of md5(device_id): 4096 buckets. Across ~150
devices there is a small chance two share a suffix — harmless: the device
ID stays unique everywhere that matters, and avahi auto-renames mDNS
clashes (aef-pi-4a7-2.local). The name is for humans reading screens.

Prefix: "hostname_prefix" in /etc/fleet-client/config.json (settable via
fleet-boot-config.json at SD prep or [device].hostname_prefix in
fleet-setup.toml). Default: aef-pi.

Runs as a root oneshot (fleet-hostname.service) on every boot; idempotent.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import device_id

CONFIG = Path("/etc/fleet-client/config.json")
DEFAULT_PREFIX = "aef-pi"


def desired_hostname() -> str:
    prefix = DEFAULT_PREFIX
    try:
        prefix = json.loads(CONFIG.read_text()).get("hostname_prefix") or DEFAULT_PREFIX
    except Exception:
        pass
    # hostname-safe: alnum + hyphens only
    prefix = re.sub(r"[^a-zA-Z0-9-]+", "-", prefix).strip("-") or DEFAULT_PREFIX
    suffix = hashlib.md5(device_id().encode()).hexdigest()[:3]
    return f"{prefix}-{suffix}".lower()


def main():
    want = desired_hostname()
    cur = subprocess.run(["hostname"], capture_output=True, text=True,
                         timeout=10).stdout.strip()
    if cur == want:
        print(f"hostname already {want}")
        return

    subprocess.run(["hostnamectl", "set-hostname", want], check=True, timeout=15)

    # keep sudo/getaddrinfo happy: 127.0.1.1 entry must match
    hosts = Path("/etc/hosts")
    try:
        txt = hosts.read_text()
        if re.search(r"^127\.0\.1\.1\s", txt, re.M):
            txt = re.sub(r"^127\.0\.1\.1\s.*$", f"127.0.1.1\t{want}", txt, flags=re.M)
        else:
            txt = txt.rstrip("\n") + f"\n127.0.1.1\t{want}\n"
        hosts.write_text(txt)
    except Exception as e:
        print(f"warning: /etc/hosts update failed: {e}")

    # Re-announce the new name on mDNS. MUST be --no-block: this script runs
    # inside a oneshot unit ordered Before=avahi-daemon, so a synchronous
    # restart would deadlock behind our own start job (hardware finding).
    try:
        subprocess.run(["systemctl", "try-restart", "--no-block", "avahi-daemon"],
                       check=False, timeout=10)
    except Exception as e:
        print(f"warning: avahi re-announce skipped: {e}")
    print(f"hostname set: {cur} → {want}")


if __name__ == "__main__":
    main()
