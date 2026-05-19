# Ars Festival Media Fleet — Tech Team Handbook

> Version 0.1 · February 2026

---

## Overview

Each exhibition display runs on a **Raspberry Pi** (model 3, 4, or 5) connected to a screen via HDMI. Media (video, audio, images) is delivered to each Pi from a central server over Wi-Fi. You **never** need to bring USB sticks or swap SD cards in the field.

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
| ▶ Restart Playback | Restart the VLC media loop |
| ⬇ Check for Updates | Force immediate media sync |
| 📶 Reset Wi-Fi | Clear stored Wi-Fi (triggers setup mode on reboot) |
| ⟳ Reboot Device | Full device reboot |

Volume settings persist across reboots.

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

## 6. Troubleshooting

| Problem | Solution |
|---------|----------|
| Pi shows setup screen after successful Wi-Fi setup | Check that venue Wi-Fi is working. Reboot Pi and try again. |
| No media playing (black screen) | Check dashboard — is device online? Is a manifest published for its group? Try "Update Now" from dashboard. |
| Audio too loud/quiet | Use local control panel (`http://<ip>:8080`) to adjust volume. |
| Pi offline in dashboard | Check power and Wi-Fi. Pi may have lost Wi-Fi connection — visit device physically and check HDMI screen for status. |
| Media not updating | Default sync every 12h. Use "Update Now" from dashboard or local control for immediate sync. |
| Wrong media on device | Check device group assignment in dashboard. Ensure correct manifest is published for that group. |
| Multiple Pis on same network | Each Pi has a unique hostname shown on its setup/status screen and in the dashboard. Use the IP address to access each one. |

---

## 7. Security

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

## 8. Quick Reference Card

```
Setup:    Connect Pi → HDMI shows AP → Phone connects → Enter Wi-Fi → Done
Control:  http://<pi-ip>:8080 → password → volume/restart/update
Dashboard: https://<server>/dashboard/ → admin login → devices/media/manifests
Media:    MP4 (H.264, 1080p) recommended · MP3/WAV for audio · JPG/PNG for images
```

---

*Questions? Contact: arstable@screenart.dev*
