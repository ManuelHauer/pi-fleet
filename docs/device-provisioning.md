# Device Provisioning — v0.1

## Current reality
The currently connected Pi has SSH enabled but appears to require **unknown public-key auth** (no password login).
So we cannot push-install the agent onto that unit remotely yet.

## Provisioning options (recommended order)
1. **Golden SD image (recommended)**
   - Flash Raspberry Pi OS Lite + preinstall `fleet-client` + enable first-boot AP onboarding.
   - This becomes the standard festival deployment method.

2. **USB-assisted install (fallback)**
   - If the existing custom OS already runs a USB autorun hook, we can piggyback an installer.
   - Requires inspecting how that OS handles the USB stick.

3. **SSH push-install (lab only)**
   - Requires known credentials or preinstalled authorized_keys.
   - Then run `raspi-fleet/deploy/push_to_pi.sh <ip> <server-url> <group> <label>`.

## First-boot Wi-Fi onboarding (planned)
If no Wi-Fi credentials exist:
- start AP `AEC-PI-<id>`
- captive portal form (SSID + password + label)
- join venue Wi-Fi
- register to server
