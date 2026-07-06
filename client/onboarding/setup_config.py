#!/usr/bin/env python3
"""
fleet-setup.toml — pre-primed SD card configuration.

Drop a `fleet-setup.toml` next to the media on the FLEET-MEDIA partition (or
onto the boot partition) BEFORE the Pi ships to the venue, and the device
configures itself on boot: venue Wi-Fi joins without the captive portal,
label/group are set, playback settings (rotation, slide duration) preset.

Search order (first hit wins — the SD media partition beats the boot
partition, so techs can override in the field without touching bootfs):
  /media/fleet-sd/fleet-setup.toml
  /boot/firmware/fleet-setup.toml
  /boot/fleet-setup.toml

Applied ONCE per file content: a sha256 marker in /etc/fleet-client/
setup-applied prevents re-applying on every boot (which would clobber
later local/dashboard changes). Edit the file → new hash → applied again.

Example (see deploy/fleet-setup.example.toml for the full reference):

    [wifi]
    ssid = "VenueNetz"
    password = "secret"
    # hidden = false
    # country = "AT"

    [device]
    label = "OK Linz Mediendeck — left screen"
    group = "ok-linz"

    [player]
    rotation = 90
    image_duration_s = 12

    [server]
    url = "https://fleet.example.org"
"""
import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("setup-config")

SEARCH_PATHS = [
    Path("/media/fleet-sd/fleet-setup.toml"),
    Path("/boot/firmware/fleet-setup.toml"),
    Path("/boot/fleet-setup.toml"),
]
APPLIED_MARKER = Path("/etc/fleet-client/setup-applied")
CLIENT_CONFIG = Path("/etc/fleet-client/config.json")


def find_setup_file() -> Path | None:
    for p in SEARCH_PATHS:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _parse(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # pre-3.11 — golden image targets Bookworm (3.11+)
        log.error("tomllib unavailable (Python < 3.11) — cannot read fleet-setup.toml")
        return {}
    try:
        return tomllib.loads(path.read_text())
    except Exception as e:
        log.error(f"fleet-setup.toml parse error: {e}")
        return {}


def load_setup() -> tuple[dict, Path | None, bool]:
    """Returns (config, path, is_new). is_new = content differs from the
    last-applied hash, i.e. the [device]/[player]/[server] blocks should be
    (re-)applied and a [wifi] block should (re-)provision the profile."""
    path = find_setup_file()
    if not path:
        return {}, None, False
    cfg = _parse(path)
    if not cfg:
        return {}, path, False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    applied = ""
    try:
        applied = APPLIED_MARKER.read_text().strip()
    except OSError:
        pass
    return cfg, path, digest != applied


def mark_applied(path: Path):
    try:
        APPLIED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        APPLIED_MARKER.write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    except Exception as e:
        log.warning(f"Could not write setup-applied marker: {e}")


def apply_non_wifi(cfg: dict):
    """Apply [device] / [server] / [player] blocks. Wi-Fi is handled by the
    onboarding flow (it needs the AP/portal fallback around it)."""
    device = cfg.get("device", {}) or {}
    server = cfg.get("server", {}) or {}
    player = cfg.get("player", {}) or {}

    # config.json merge
    if device or server:
        current = {}
        if CLIENT_CONFIG.exists():
            try:
                current = json.loads(CLIENT_CONFIG.read_text())
            except Exception:
                pass
        for key in ("label", "group", "location"):
            if device.get(key):
                current[key if key != "group" else "group"] = str(device[key])
        if server.get("url"):
            current["server_url"] = str(server["url"]).rstrip("/")
        if server.get("device_psk"):
            current["device_psk"] = str(server["device_psk"])
        try:
            CLIENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CLIENT_CONFIG.write_text(json.dumps(current, indent=2))
            log.info(f"Applied [device]/[server] setup → {CLIENT_CONFIG}")
        except Exception as e:
            log.error(f"config.json write failed: {e}")

    # player settings
    if player:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from player_settings import save_settings
            patch = {k: player[k] for k in
                     ("rotation", "image_duration_s", "volume_pct", "muted")
                     if k in player}
            if patch:
                save_settings(patch, updated_by="setup")
                log.info(f"Applied [player] setup: {patch}")
        except Exception as e:
            log.error(f"player settings apply failed: {e}")


def wifi_block(cfg: dict) -> dict:
    """Normalized [wifi] block or {}."""
    wifi = cfg.get("wifi", {}) or {}
    ssid = str(wifi.get("ssid", "")).strip()
    if not ssid:
        return {}
    return {
        "ssid": ssid,
        "password": str(wifi.get("password", "")),
        "hidden": bool(wifi.get("hidden", False)),
        "country": str(wifi.get("country", "")).strip().upper() or None,
    }
