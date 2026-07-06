# Wi-Fi Onboarding — pre-primed config, first-boot AP + captive portal

## Overview (v0.3, NetworkManager-based)

On every boot without a completed onboarding (`/etc/fleet-client/onboard-done`),
the service tries, in order:

1. **`fleet-setup.toml`** on the FLEET-MEDIA partition or boot partition —
   pre-primed venue Wi-Fi + device/player config. Zero-touch: no phone needed.
2. **Existing `fleet-venue` profile** — reconnect to a previously joined network.
3. **`wifi.json` on a USB stick** — legacy fallback.
4. **Setup mode**: hotspot `AEC-PI-<last4serial>` + captive portal at
   `http://10.42.0.1`. The technician connects with a phone and picks the
   venue Wi-Fi. The AP credentials are shown ON THE SCREEN attached to the
   Pi (rendered through the fleet player).

Success in any path → optional Tailscale mesh join (non-fatal) → normal fleet
operation. The portal never times out; USB/SD kiosk playback works throughout.

## Files
- `onboard_service.py` — main orchestrator (systemd service)
- `nm_manager.py` — all Wi-Fi operations via `nmcli` (scan, venue profile,
  hotspot). Replaces the v0.2 `wifi_manager.py`/`ap_manager.py` pair — Pi OS
  Bookworm/Trixie is NetworkManager-managed; writing `wpa_supplicant.conf`
  does nothing there.
- `setup_config.py` — `fleet-setup.toml` reader/applier (apply-once per content hash)
- `captive_portal.py` — Flask app for credential entry (async connect,
  progress polling)
- `templates/setup.html` — portal page (network list + manual entry)
- `templates/status.html` — connecting/result page
- `hdmi_status.py` — renders setup/status cards as PNG shown by fleet-player
  (v0.2 wrote to tty1, which mpv's DRM output covered — invisible)
- `fleet-onboard.service` — systemd unit

## Scan limitation
A single Wi-Fi radio can't scan while the hotspot is up. The service scans
BEFORE starting the AP and the portal serves that cached list, plus a manual
SSID field.

## Dependencies (installed by golden image)
- network-manager (default on Pi OS Bookworm+), dnsmasq-base
- `/etc/NetworkManager/dnsmasq-shared.d/00-fleet-captive.conf` — wildcard DNS
  for captive-portal detection (installed by `golden_image_firstrun.sh`)
- python3, python3-flask, python3-pil
