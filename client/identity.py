"""Pi identity — derived from SoC serial. Survives SD cloning."""
import hashlib
import re
import socket
from pathlib import Path


def pi_serial() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            m = re.match(r"Serial\s*:\s*([0-9a-fA-F]+)", line)
            if m:
                return m.group(1).lower()
    except Exception:
        return None
    return None


def device_id() -> str:
    """Stable ID: 'pi-<last 12 of serial>'. Falls back to hostname hash."""
    cached = Path("/etc/fleet-client/device-id")
    if cached.exists():
        v = cached.read_text().strip()
        if v:
            return v
    s = pi_serial()
    if s:
        did = f"pi-{s[-12:]}"
    else:
        h = hashlib.sha256(socket.gethostname().encode()).hexdigest()
        did = f"pi-{h[:12]}"
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(did)
    except Exception:
        pass
    return did
