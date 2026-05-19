# Wi-Fi Onboarding — First-boot AP + Captive Portal

## Overview
When a Pi boots without stored Wi-Fi credentials, it enters **setup mode**:
- Creates AP hotspot `AEC-PI-<last4serial>`
- Runs captive portal on `http://192.168.4.1`
- Technician connects phone, enters venue Wi-Fi details
- Pi joins venue network and registers with fleet server

## Files
- `onboard_service.py` — main orchestrator (systemd service)
- `captive_portal.py` — Flask web app for credential entry
- `templates/setup.html` — portal form page
- `templates/status.html` — success/failure page
- `hdmi_status.py` — framebuffer status display
- `ap_manager.py` — hostapd/dnsmasq control
- `wifi_manager.py` — wpa_supplicant credential management
- `fleet-onboard.service` — systemd unit

## Dependencies (installed by golden image)
- hostapd, dnsmasq
- python3, python3-flask
- wpa_supplicant (standard)
