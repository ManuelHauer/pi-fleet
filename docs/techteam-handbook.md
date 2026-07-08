# Ars Festival Media Fleet — Tech Team Handbook

> Version 0.3 · July 2026

---

## Overview

Each exhibition display runs on a **Raspberry Pi** (model 3B+, 4, or 5) connected to a screen via HDMI. Media (video, images, audio) reaches a Pi three ways, and you can mix them freely:

1. **Server push** — assign files in the central dashboard; the Pi syncs within ~30 s.
2. **SD card** — the Pi's own SD card has a big **FLEET-MEDIA** partition that shows up on any Mac/Windows laptop. Drop files on it, put it back, boot — done.
3. **USB stick** — insert a stick with media into a running Pi; it switches within seconds.

SD and USB media **pin** the device: the dashboard shows 💾 or 🔌 and ignores server assignments until you press **Release**.

A freshly-installed Pi **never** shows a black screen: it boots into an info card with its device ID, hostname, IP and **"WAITING FOR MEDIA"**. During Wi-Fi setup the screen shows the setup instructions instead. If you can see a card on HDMI, the install worked.

---

## 1. On-Site Setup (Per Device)

### What You Need
- Raspberry Pi (pre-flashed SD card)
- HDMI cable + display/screen
- Power supply (USB-C for Pi 4/5, micro-USB for 3B+)
- Your phone (only if the card was NOT pre-primed)

> ### ⚠ Wi-Fi band — check before assigning a Pi to a venue
> **A Raspberry Pi 3 Model B has 2.4 GHz Wi-Fi ONLY.** If a venue's Wi-Fi is
> **5 GHz-only**, a Pi 3 B *cannot see it at all* and will never onboard over
> Wi-Fi. Know your hardware:
>
> | Model | Wi-Fi bands |
> |---|---|
> | Pi 3 Model **B** | 2.4 GHz only |
> | Pi 3 Model **B+** | 2.4 + 5 GHz |
> | Pi 4 / Pi 5 | 2.4 + 5 GHz |
>
> For a 5 GHz-only venue, send a **B+/4/5** — or run the Pi 3 B on **wired
> Ethernet** or as an **offline USB/SD kiosk** (drop media on the FLEET-MEDIA
> partition; no network needed). The dashboard's device panel shows each Pi's
> model, so you can spot 3 B units.

### Zero-touch (pre-primed card)
If the SD card was prepared with a `fleet-setup.toml` (venue Wi-Fi pre-filled — see §7), there is **nothing to do**: connect HDMI + power, wait ~2 minutes, the Pi joins the venue Wi-Fi and starts playback/idle card by itself.

### Phone setup (captive portal)
1. **Connect** HDMI and power.
2. **Wait ~60 seconds** — the screen shows SETUP MODE with a Wi-Fi name + password, e.g. `AEC-PI-A7F3` / `aecA7F3setup`.
3. **Connect your phone** to that Wi-Fi network.
4. The **setup page opens automatically** (if not: `http://10.42.0.1`).
5. **Pick the venue Wi-Fi** from the list (strongest first) or type its name.
6. Enter the Wi-Fi password, tap **Connect device**.
7. Your phone loses the setup network — that's normal. **Watch the screen on the Pi:**
   - **CONNECTED** → done, playback starts automatically.
   - **SETUP FAILED** → the setup Wi-Fi comes back; reconnect your phone and try again.

### Fallback: USB Wi-Fi config
Put a `wifi.json` on a USB stick, plug in, reboot:
```json
{ "ssid": "VenueWiFiName", "password": "VenuePassword" }
```

---

## 2. Controlling a Device (Rotation, Slides, Volume)

Each Pi runs a **local control panel**: open `http://<pi-ip>:8080` on any device in the same network and enter the tech password. The IP is on the idle card; the **Identify** button in the dashboard flashes it on the venue screen.

| Control | Description |
|--------|-------------|
| ↕ **Screen flip** | Flip the image 180° for screens mounted upside down — applies **instantly**, playback keeps running. |
| ⏱ **Slide duration** | Seconds per image in a slideshow (videos always play full length). Applies from the next slide. |
| 🔊 Volume + mute | 0–200%, persists across reboots |
| 📺 Show device info on screen | 30-second on-screen badge (device ID + IP) |
| ▶ Restart player | Restart the mpv loop only |
| ⬇ Check server for updates | Force immediate media sync |
| 💾 Play from SD card | Re-import the FLEET-MEDIA partition content |
| 📶 Reset Wi-Fi | Clear stored Wi-Fi → setup mode on next reboot |
| ⟳ Reboot device | Full reboot |

Flip and slide duration can **also** be set per device from the central dashboard (Playback settings section in the device panel) — same effect, applied within one heartbeat (~30 s). The dashboard always shows what the device last reported.

### Controlling a device with a USB keyboard  *(new in v0.4)*

Plug a **USB keyboard** into the Pi and you can control playback directly — no
phone, no network needed (handy in a venue with no signal). Keys work the
instant the keyboard is plugged in.

| Key | Action |
|---|---|
| `+` / `−` (or the volume keys) | Volume up / down |
| `m` (or the mute key) | Mute / unmute |
| `r` or `f` | Flip the image 180° (toggle, for upside-down mounted screens) |
| `[` / `]` | Slideshow: slower / faster (image duration ± 2 s) |
| `→` / `←` (or next/prev track keys) | Next / previous item |
| `Space` (or play/pause key) | Pause / resume |
| `i` | **Show this device's IP + phone-control URL on the screen** (~8 s) |

Every keypress **flashes a confirmation on the screen** (e.g. "🔊 Volume 65%",
"⏸ Paused", "⟳ Rotate 90°"), so you can see what you pressed.

Keyboard, phone UI and dashboard all stay in sync — a change made on the
keyboard shows up in the dashboard on the next heartbeat.

> **Tip — finding the phone UI:** press **`i`** on the keyboard and the screen
> shows `Phone control: http://<ip>:8080`. Open that on a phone connected to the
> same network (password from your team lead) to control the device without a
> keyboard.

### Reading the on-screen overlay

| Overlay | What it means |
|---|---|
| `⚠ OFFLINE · no wifi · auto-hide MM:SS` | Pi has no network route — venue Wi-Fi died. |
| `⚠ OFFLINE · no mesh · auto-hide MM:SS` | Wi-Fi up, Tailscale mesh down (only on mesh deployments). |
| `⚠ OFFLINE · no server · auto-hide MM:SS` | Network up but the fleet server is unreachable. |
| `✓ CONNECTED` (5 s flash) | Pi just reconnected. |
| `◉ pi-xxxx · hostname · IP` | Someone pressed Identify. |

Playback continues through all of this — offline just means "no new content until reconnect". The overlay auto-hides after 5 minutes per offline episode.

---

## 3. Supported Media Formats

### Video
| Format | Extension | Notes |
|--------|-----------|-------|
| H.264/MP4 | `.mp4` | ✅ Recommended. Best hardware decode on all Pi models. |
| H.265/HEVC | `.mp4`, `.mkv` | ⚠ Pi 4/5 only. |
| MKV / MOV / AVI / WebM | `.mkv` `.mov` `.avi` `.webm` | ✅ Supported (WebM = software decode; avoid on Pi 3B+) |

**Recommended:** 1920×1080, H.264, 24–30 fps, 5–15 Mbps. 4K only on Pi 4/5 with H.265.

### Images
`.jpg` `.png` `.webp` `.bmp` `.gif` (static) — 1920×1080 recommended. For screens mounted upside down use the **Flip** setting; for portrait installations export the content pre-rotated (1080×1920 shown on a physically rotated screen).

### Audio
`.mp3` `.wav` `.flac` `.ogg` `.aac`

> Non-media files on sticks/SD cards (`README.txt`, `fleet-setup.toml`, …) are ignored by the player — safe to keep them next to the content.

---

## 4. Central Dashboard

- URL: provided by your team lead (e.g. `https://fleet.example.org/dashboard/`)
- Works on desktop **and phone**; login with the admin credentials.

**Devices tab** — every Pi as a card: green/red online dot, state badge (`playing`, `offline·playing`, `no media`), pin badge (🔌 USB / 💾 SD), rotation/slide/volume at a glance. Filter by group (venue). Tap a card for the device panel:
- **Info** — ID, IP, model, last seen, content version
- **Name & venue** — label, group, venue text
- **Playback settings** — rotation, slide duration, volume/mute (pushed live)
- **Assigned media** — list, remove, assign more
- **Actions** — Identify (30 s on-screen badge), restart player, sync now, play from SD, reboot
- **Release** button when pinned

**Media tab** — drag & drop upload (multiple files at once, progress bars), library with per-file assignment counts, assign to any set of devices (tap a group name to select the whole venue).

---

## 5. Getting Artist Media Onto Devices (Server Path)

1. Download the file from the PM's SharePoint folder (see `docs/media-workflow.md`).
2. Dashboard → **Media** → pick or create a **folder** (e.g. one per venue or artist)
   in the chip bar, then drop the file(s) into the upload zone — they land in the
   open folder. *(Folders are for organizing the library; they don't change what
   plays — assignment does.)*
3. Click **assign** on the file → tick the target devices (or tap a group name for
   the whole venue) → **Assign**. Use **move** to re-file media later.
4. Devices sync within ~30 s (online ones). The device panel shows the new version.

No manifest publishing step anymore — assignments generate the manifests automatically.

---

## 6. Updating Content via USB Stick

Insert a USB stick (FAT32/exFAT) with media at the root or in a `fleet/` folder into a **running** Pi. Within ~10 seconds the content is copied, atomically activated, and the device is **pinned** (🔌 in the dashboard, ignores server pushes until **Release**).

- Same stick again = no-op. Different content = clean swap, stays pinned.
- Use case: artist arrives with last-minute edits → walk the room with one stick.

---

## 7. SD Card: Media + Pre-Configuration  *(new in v0.3)*

Every fleet SD card has a big **FLEET-MEDIA** partition (exFAT) that mounts on any Mac or Windows laptop — no special software.

### Load media directly onto the card
1. Power off the Pi, take the SD card, put it in your laptop.
2. Open the **FLEET-MEDIA** volume, drop media files in the top folder (no subfolders).
3. Eject cleanly, card back into the Pi, power on.
4. The Pi detects the changed content and plays it (pinned 💾, zero copying — the whole partition size is usable).

Editing files on the card later (add/remove/replace) is detected on every boot and every ~30 s while running.

### Pre-prime a card for a venue (`fleet-setup.toml`)
Copy `deploy/fleet-setup.example.toml` to the FLEET-MEDIA partition (or bootfs) as `fleet-setup.toml` and fill in:
- `[wifi]` — venue SSID/password → the Pi joins by itself, **no phone setup at the venue**
- `[device]` — label + group so it appears correctly named in the dashboard
- `[player]` — rotation / slide duration / volume presets
- `[server]` — only if this card should talk to a non-default server

The file is applied once per content change (editing it re-applies). **Treat prepared cards like keys to the venue Wi-Fi.**

---

## 8. Troubleshooting

| Problem | Solution |
|---------|----------|
| `⚠ OFFLINE · no wifi` | Venue Wi-Fi died. Check router/AP. Pi resumes by itself. |
| `⚠ OFFLINE · no server` | Network fine, server down/unreachable — escalate to team lead. |
| Setup screen reappears after setup | Venue Wi-Fi credentials failed. Redo phone setup; check password. |
| "WAITING FOR MEDIA" idle card | Nothing assigned yet (and no SD/USB media). Assign in dashboard or use **Sync now**. |
| Pinned 💾/🔌 but I want dashboard content | **Release** in the device panel. |
| SD media not playing after card edit | Check files are in the TOP folder of FLEET-MEDIA, supported formats; then local UI → **Play from SD card**. |
| Screen is mounted upside down | Toggle **Flip 180°** in the dashboard device panel, local UI, or press `r` on a plugged-in keyboard. |
| Slides flip too fast/slow | Slide duration in dashboard device panel or local UI. |
| No picture at all | mpv may have crashed → **Restart player**; else reboot; else re-seat HDMI. |
| Which Pi is which? | Every Pi names itself `aef-pi-xxx` (a checksum of its device ID) — shown on the idle card, in the dashboard and as `aef-pi-xxx.local`. For a live check: **Identify** in the dashboard → 30 s badge on the venue screen, or press `i` on a plugged-in keyboard. |
| "What's wrong with this Pi?" | SSH in and run `sudo /opt/fleet-client/diag.sh` — one-shot health dump. |

---

## 9. Security

- Dashboard: admin username + password (JWT session).
- Local device control: shared tech password.
- Device ↔ server: pre-shared device key over HTTPS (and/or Tailscale mesh).
- **All defaults must be changed before deployment** — the SD prep script and server installer warn about / generate proper secrets.
- The remote-shell command is disabled by default on the packaged server deployment (`FLEET_DISABLE_SHELL=1`).

---

## 10. Quick Reference Card

```
Zero-touch:  pre-primed card → power on → wait → playing
Phone setup: HDMI shows AP name+pass → connect phone → http://10.42.0.1 → pick Wi-Fi
Control:     http://<pi-ip>:8080 → rotation / slides / volume / restart
Dashboard:   https://<server>/dashboard/ → devices + media, works on phone
SD media:    FLEET-MEDIA volume on any laptop → drop files → boot Pi
USB media:   stick into running Pi → plays + pins within seconds
Formats:     MP4 H.264 1080p · JPG/PNG · MP3/WAV
```
