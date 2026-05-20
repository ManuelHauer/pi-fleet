# Ars Festival Media Fleet — Tech Team Handbook

> Version 0.2 · May 2026

---

## Overview

Each exhibition display runs on a **Raspberry Pi** (model 3, 4, or 5) connected to a screen via HDMI. Media (video, audio, images) is delivered to each Pi from a central server over a private Tailscale mesh (Headscale). If a venue has no Wi-Fi, you can still drop media on the Pi via a USB stick — see §6.

A freshly-installed Pi **never** shows a black screen: it boots into an info card with its device ID, hostname, IP, and the message **"WAITING FOR MEDIA"**. If you can see that card on HDMI, the install worked.

---

## 1. On-Site Setup (Per Device)

### What You Need
- Raspberry Pi (pre-flashed SD card)
- HDMI cable + display/screen
- Power supply (USB-C for Pi 4/5, micro-USB for Pi 3)
- Your phone (for Wi-Fi setup)

### Step-by-Step

1. **Connect** HDMI cable to screen, power to Pi.
2. **Wait ~60 seconds** — the setup screen appears on HDMI:

   ```
   ╔══════════════════════════════════════╗
   ║  🎬 ARS FESTIVAL MEDIA PLAYER       ║
   ║       — SETUP MODE —                ║
   ╠══════════════════════════════════════╣
   ║  Wi-Fi Network:  AEC-PI-A7F3        ║
   ║  Password:       aecA7F3setup       ║
   ╚══════════════════════════════════════╝
   ```

3. **Connect your phone** to the Wi-Fi network shown (e.g., `AEC-PI-A7F3`).
4. A **setup page** opens automatically in your browser. If not, go to `http://192.168.4.1`.
5. **Select the venue Wi-Fi** from the scan list (or enter manually).
6. **Enter the Wi-Fi password** and a location label (e.g., `Mariendom-05`).
7. Tap **Connect**.
8. Wait for **✅ Connected** confirmation on your phone and on the HDMI screen.
9. **Done.** The Pi will sync media and start playback automatically.

### Fallback: USB Wi-Fi Config
If the phone portal doesn't work, create a file called `wifi.json` on a USB stick:
```json
{
  "ssid": "VenueWiFiName",
  "password": "VenuePassword",
  "label": "Mariendom-05"
}
```
Plug the USB into the Pi and reboot. It will read the credentials automatically.

---

## 2. Controlling a Device (Volume, Playback)

Each Pi runs a **local control panel** accessible from any device on the same Wi-Fi network.

### Access
1. Open your phone browser.
2. Go to `http://<pi-ip-address>:8080` (the IP is shown on the HDMI status screen and in the central dashboard).
3. Enter the **tech password** (provided by your team lead).

### What You Can Do
| Action | Description |
|--------|-------------|
| 🔊 Volume slider | Adjust playback volume (0–200%) |
| 🔇 Mute button | Instant mute |
| ▶ Restart Player | Restart the mpv media loop (does not touch the management daemon) |
| ⬇ Check for Updates | Force immediate media sync from server |
| 📺 Show status on screen | Force the on-screen status overlay on for 30 seconds (useful when the offline countdown has already expired) |
| 📶 Reset Wi-Fi | Clear stored Wi-Fi (triggers setup mode on reboot) |
| ⟳ Reboot Device | Full device reboot |

Volume settings persist across reboots.

### Reading the On-Screen Overlay

Each Pi may briefly draw a small text overlay in the bottom-right of the video:

| Overlay | What it means |
|---|---|
| `⚠ OFFLINE · no wifi · auto-hide MM:SS` | Pi has no default route. Wi-Fi died at the venue. |
| `⚠ OFFLINE · no mesh · auto-hide MM:SS` | Wi-Fi is up but Tailscale won't connect (Headscale unreachable or authkey expired). |
| `⚠ OFFLINE · no server · auto-hide MM:SS` | Mesh is up but the server is unreachable (e.g. server box is rebooting). |
| `✓ CONNECTED` (flash, ~5s) | Pi just reconnected to the server. |
| `🔌 Pinned to USB media` (in the local UI banner) | Device is showing USB content; dashboard pushes are ignored until you Release. |

The offline overlay auto-hides after 5 minutes and won't reappear until the **next** offline episode. To force it on again, open `:8080` and press **📺 Show status on screen**.

---

## 3. Supported Media Formats

### Video
| Format | Extension | Notes |
|--------|-----------|-------|
| H.264/MP4 | `.mp4` | ✅ Recommended. Best hardware decode support on all Pi models. |
| H.265/HEVC | `.mp4`, `.mkv` | ⚠ Pi 4/5 only. Pi 3 may struggle. |
| AVI | `.avi` | ✅ Supported |
| MKV | `.mkv` | ✅ Supported |
| WebM/VP9 | `.webm` | ⚠ Software decode only — may drop frames at high res on Pi 3. |
| MOV | `.mov` | ✅ Supported |

### Recommended Video Settings
| Setting | Recommendation |
|---------|---------------|
| Resolution | **1920×1080 (1080p)** — recommended for all Pi models |
| | 3840×2160 (4K) — Pi 4/5 only, H.265 codec required |
| Frame rate | 24–30 fps (60 fps possible on Pi 4/5 at 1080p) |
| Bitrate | 5–15 Mbps for 1080p, 20–40 Mbps for 4K |
| Codec | **H.264** (broadest support) or H.265 (Pi 4/5 only) |

### Audio
| Format | Extension | Notes |
|--------|-----------|-------|
| MP3 | `.mp3` | ✅ Recommended |
| WAV | `.wav` | ✅ Lossless — larger files |
| FLAC | `.flac` | ✅ Lossless compressed |
| OGG Vorbis | `.ogg` | ✅ Supported |
| AAC | `.aac` | ✅ Supported |

> **Audio-only behavior:** When a device receives only audio files (no video or images), the HDMI screen displays the **Ars Electronica logo** centered on a black background while audio plays.

### Images
| Format | Extension | Notes |
|--------|-----------|-------|
| JPEG | `.jpg`, `.jpeg` | ✅ Recommended for photos |
| PNG | `.png` | ✅ Supported — larger files |
| BMP | `.bmp` | ✅ Supported |
| GIF | `.gif` | ✅ Static display (no animation) |
| WebP | `.webp` | ✅ Supported |

### Recommended Image Settings
| Setting | Recommendation |
|---------|---------------|
| Resolution | 1920×1080 pixels (match display) |
| | Up to 3840×2160 for 4K displays |
| Display duration | 10 seconds per image (configurable) |

---

## 4. Central Dashboard

The central dashboard shows all devices and their status at a glance.

### Access
- URL: provided by your team lead (e.g., `https://fleet.yourdomain.com/dashboard/`)
- Login: admin credentials (username + password)

### Features
- **Device list** — all Pis with online/offline status, group, IP, last seen
- **Device detail** — CPU temperature, disk space, VLC status, current media version, heartbeat history
- **Remote commands** — update now, restart VLC, health probe, reboot
- **Media library** — upload/delete media files
- **Manifests** — publish media versions to device groups

### Device Groups
Devices are organized by **group** (typically one per venue):
- `mariendom`
- `aec-center`
- `ok-platz`
- `art-university`
- etc.

You can publish different media to different groups.

---

## 5. Uploading Media

1. Open the central dashboard.
2. Click **📁 Media** tab.
3. Click **⬆ Upload Media Files** and select your files.
4. After upload, click **📋 Manifests** tab.
5. Select the target **group** (venue).
6. Check the files you want in the playlist.
7. Click **📋 Publish Manifest**.
8. Devices in that group will pick up the new media on their next sync cycle (default: every 12 hours, or use "Update Now" for immediate).

---

## 6. Updating Content via USB Stick

Insert a FAT32 USB stick with either:

- Media files directly at the root, **or**
- A `fleet/` subfolder containing the media

…into a running Pi. Within ~10 seconds:

1. The stick is mounted read-only.
2. Files are copied into a hashed release dir (`releases/usb-<hash12>`).
3. The `current` symlink is atomically swapped.
4. The player reloads with the new content.
5. **The device becomes "pinned"** — dashboard pushes are ignored until you Release.

In the dashboard the device will show a purple **🔌** icon next to its name and a **Release** button in the detail panel. Click Release when you want the device to start picking up dashboard manifests again.

Re-inserting the **same** stick is a no-op (idempotent). Re-inserting a stick with **different** content does an atomic swap to the new content; the device stays pinned.

> Use case at the festival: artist arrives at the opening with last-minute edits → tech walks the room with a USB stick → all kiosks updated within a minute, no dashboard touch required.

---

## 7. Troubleshooting

### Pi shows `⚠ OFFLINE · no wifi`
Wi-Fi died. Check the access point / router at the venue. Once Wi-Fi recovers the Pi will flash `✓ CONNECTED` and resume normal polling.

### Pi shows `⚠ OFFLINE · no mesh`
Wi-Fi is up but the Tailscale mesh isn't. Either the per-SD authkey expired (re-flash SD) or Headscale is down (check with team lead).

### Pi shows `⚠ OFFLINE · no server`
Mesh is fine, but the server isn't responding. Check the Mac mini at HQ.

### Pi shows the "WAITING FOR MEDIA" idle card
The Pi has no media. Either no manifest has been published to its group yet, or the device hasn't pulled it. Try **Update Now** in the dashboard or **⬇ Check for Updates** in the local UI.

### Pi shows a 🔌 in the dashboard but I want the dashboard content
The device is pinned to a USB-inserted release. Click **Release** in the detail panel.

### Generic troubleshooting

| Problem | Solution |
|---------|----------|
| Pi shows setup screen after successful Wi-Fi setup | Check that venue Wi-Fi is working. Reboot Pi and try again. |
| No media playing and no idle card either | mpv may have crashed. Press **▶ Restart Player** in the local UI; if that doesn't fix it, reboot. |
| Audio too loud/quiet | Use local control panel (`http://<ip>:8080`) to adjust volume. |
| Pi offline in dashboard | Check power and Wi-Fi. Pi may have lost Wi-Fi connection — visit device physically and check HDMI screen for status. |
| Media not updating | Default sync every 30s. Use **Update Now** from dashboard or local control for immediate sync. If device is pinned, click **Release** first. |
| Wrong media on device | Check device group assignment in dashboard. Ensure correct manifest is published for that group. |
| Multiple Pis on same network | Each Pi has a unique hostname shown on its idle screen and in the dashboard. Use the IP address to access each one. |
| "What's wrong with this Pi?" | SSH (tailnet) into the Pi and run `sudo /opt/fleet-client/diag.sh` for a one-shot health dump. |

---

## 8. Security

- **Central dashboard:** protected by admin username + password (or Bearer token).
- **Local device control:** protected by shared tech team password.
- **Device ↔ server communication:** authenticated via pre-shared device key.
- All passwords should be changed from defaults before festival deployment.

### Default Credentials (CHANGE BEFORE DEPLOYMENT)
| System | Default |
|--------|---------|
| Dashboard admin | `admin` / `aec2026!` |
| Local device control | `aec2026` |
| Device PSK | `aec-device-psk-2026` |

---

## 9. Quick Reference Card

```
Setup:    Connect Pi → HDMI shows AP → Phone connects → Enter Wi-Fi → Done
Control:  http://<pi-ip>:8080 → password → volume/restart/update
Dashboard: https://<server>/dashboard/ → admin login → devices/media/manifests
Media:    MP4 (H.264, 1080p) recommended · MP3/WAV for audio · JPG/PNG for images
```

---

*Questions? Contact: arstable@screenart.dev*
