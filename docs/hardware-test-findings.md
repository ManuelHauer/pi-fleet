# Hardware Test Findings — 2026-07-06

First end-to-end run of the v0.3 stack on a **real Raspberry Pi 4**, flashed
from the provisioning pipeline (Wi-Fi preseed), talking to a fleet server on a
laptop. OS: **Raspberry Pi OS Trixie (Debian 13), aarch64**.

Result: **full pass** after fixing 5 bugs that only surface on real hardware.
The keyboard-control feature (v0.4) was built and validated in the same session.

## Validated on hardware

| Area | Result |
|---|---|
| SD `FLEET-MEDIA` partition surgery (rootfs 2.3 GB→8 GB online + 20.7 GB exFAT p3, mounted `/media/fleet-sd`) | ✅ |
| `fleet-setup.toml` preseed (device label/group + player settings applied) | ✅ |
| Package install on Trixie, services up, mpv playback via DRM | ✅ |
| Dashboard registration (device appears with preseed label/group) | ✅ |
| Server → device media assign / download / play | ✅ |
| Live rotation + volume from dashboard (applied to running mpv, no restart) | ✅ |
| SD-card media: drop file on FLEET-MEDIA → pin → play; dashboard shows pin | ✅ |
| Release / unpin → back to server media | ✅ |
| On-Pi local control UI (`:8080`) | ✅ |
| USB-keyboard control (rotate / volume / mute / next-prev / pause) | ✅ |

## Bugs found & fixed (each a commit)

1. **apt ran before the rootfs grow → out of disk.**
   Auto-expand is disabled at flash time (to free space for FLEET-MEDIA), so the
   stock root is ~2 GB. The installer ran `apt install mpv …` *before*
   `setup_media_partition.sh` grew the rootfs → `E: You don't have enough free
   space in /var/cache/apt/archives/`. Provisioning aborted.
   **Fix:** partition/grow first, then apt; `setup_media_partition.sh` installs
   exfatprogs on demand once the rootfs has room.

2. **fleet-player crash-looped → black screen.**
   `render_idle_screen()` saved the idle card to `idle.png.tmp`; PIL can't infer
   a format from `.tmp` → `ValueError: unknown file extension: .tmp`. mpv never
   started. Same latent bug in the onboarding screen renderer.
   **Fix:** `img.save(tmp, format="PNG")`. (Local tests never ran the player —
   it needs DRM hardware, so this was invisible until now.)

3. **Onboarding hijacked the Wi-Fi radio into hotspot mode.**
   `fleet-onboard` ran before NetworkManager finished auto-connecting the venue
   Wi-Fi, saw no IP and no `fleet-venue` profile (the preseed profile is named
   `preconfigured`), and raised the setup AP on wlan0 — taking the radio away
   from the venue Wi-Fi so the device could never reach the server.
   **Fix:** recognize *any* Wi-Fi profile and `wait_online(45s)` for NM before
   starting the AP.

4. **`config.json` was root-only; the pi service user couldn't read it.**
   Written `root:root 600`, but the daemons run as `User=pi` → fleet-client
   silently fell back to built-in defaults (wrong server/group) and never
   registered; the local UI lost its password.
   **Fix:** `chown pi:pi /etc/fleet-client/config.json` (mode 600 keeps the PSK
   and password private to the service user).

5. **Release (unpin) was ~70 s late.**
   The heartbeat that delivered the `force_poll` command still reported
   `pinned=1`, re-pinning the device server-side; the immediate re-poll got the
   "still pinned" sentinel and skipped. It recovered only on a later heartbeat.
   **Fix:** after clearing the local pin, send a heartbeat (`pinned=0`) *before*
   re-polling → swaps back in one cycle (~35 s, bounded by the poll interval).

Also: the Tailscale install was made **non-fatal** — an optional-mesh repo/keyring
hiccup must never abort the whole provisioning.

## Deployment constraints (learned provisioning multiple boards)

- **Pi 3 Model B is 2.4 GHz-only.** The test network `ae-extern` is 5 GHz
  (channel 116); a Pi 3 B literally cannot see it and never onboards over
  Wi-Fi. Pi 3 B+, Pi 4, Pi 5 are dual-band. → Match board to venue Wi-Fi, or
  wire the 3 B / run it as an offline USB-SD kiosk. Documented in the handbook.
- **First boot needs internet AT boot time.** The installer waits ~60 s for a
  network then, if absent, exits and only retries on the NEXT boot (systemd
  oneshot). A Pi that boots with no network and gets it later must be
  rebooted to install. Bench provisioning: give the switch a working uplink
  (or Mac Internet Sharing) BEFORE powering the Pis.
- **Bench topology that works:** a plain switch with Pis + one uplink to the
  venue/office LAN (DHCP + internet). The Pis land on the real subnet, install
  over Ethernet, and register — regardless of Wi-Fi band. This is the
  recommended HQ provisioning bench.

## Field notes (not bugs, but worth knowing at the festival)

- **Wi-Fi reconnect on DFS channels.** The test network was 5 GHz channel 116
  (a DFS channel). The Pi occasionally took a while to re-associate after a
  reboot. If venues run the Pis on DFS channels, expect slower reconnects after
  power-cycles; a non-DFS 2.4/5 GHz channel reconnects faster.
- **macOS can't `dd` a removable card** (privacy protection blocks raw device
  reads even as root). Use `hdiutil`/`asr` for imaging — see the SD backup notes.
- **First boot needs wired internet or a working Wi-Fi preseed** for the apt
  step; a first boot with no network retries automatically on the next boot.

## Environment

- Device: Raspberry Pi 4 Model B
- OS: Raspberry Pi OS Lite, Debian 13 (Trixie), aarch64
- Provisioning: `flash_and_prepare_sd.sh` equivalent + `fleet-setup.toml` (Wi-Fi + [device]/[player])
- Server: FastAPI dev server on a laptop, same LAN
