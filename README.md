# 🎬 Pi Fleet — Media Orchestration

A centralized fleet management and media orchestration platform designed to remotely manage, monitor, and deploy headless video playback across up to 150 Raspberry Pi (3/4/5) players for large-scale exhibitions and events.

> **Status:** v0.2 (May 2026). See [What's New in v0.2](#-whats-new-in-v02) for the rewrite changelog.

---

## ✨ Key Features

- **Centralized Dashboard (Server):** FastAPI backend and web UI to mass-assign media, push updates, and monitor device health (CPU temps, uptime, playback status) in real-time.
- **Headless Hardware-Accelerated Playback:** Uses `mpv` with `--vo=drm` on Raspberry Pi OS Lite (Bookworm/Trixie) for buttery-smooth looping video without a desktop environment. (No VLC — has a fatal DRM bug on Trixie.)
- **Split Daemon Design:** `fleet_player.py` owns mpv; `fleet_client.py` handles management. mpv crashes recover in seconds via `systemd Restart=always` without disturbing the management daemon. The two communicate via files on disk — no IPC sockets between them.
- **Mesh Networking (Headscale/Tailscale):** Every Pi joins a self-hosted Tailscale tailnet during onboarding with a per-SD single-use authkey. Server is never publicly exposed.
- **Field-Ready Provisioning:** Zero-touch "Golden Image" SD card creation. Field technicians can configure Wi-Fi on-site using a first-boot Captive Portal / AP Hotspot.
- **Identity Survives SD Cloning:** Device ID is derived from the Pi SoC serial in `/proc/cpuinfo`, not `/etc/machine-id` — so mass-flashed cards don't collide.
- **USB Pinning Model:** Insert a USB stick at any time → atomic release-dir + symlink swap → device is now **pinned** against server manifests. Dashboard shows `🔌` indicator and a Release button. Idempotent: re-inserting the same stick is a no-op; different content swaps in.
- **Technician Local Control:** Each Pi hosts a mobile-friendly local Web UI (port 8080) for on-the-fly volume adjustments, manual syncing, restart-player, force-show-status overlay, and reboot.
- **Derived State Machine:** `NO_MEDIA` / `PLAYING_CONNECTED` / `PLAYING_OFFLINE`, recomputed every tick from `(server reachable, current symlink exists, pinned)`. No stored mode flag, no flag-corruption mode.
- **Idle / Offline Overlays:** Freshly-installed Pi shows an info card (device ID + hostname + IP + "WAITING FOR MEDIA") — never a black screen. `Connected → Offline` flashes a 5-min countdown overlay with the cause (`no wifi` / `no mesh` / `no server`). `Offline → Connected` flashes a 5-second confirmation.

---

## 🆕 What's New in v0.2

v0.2 is a near-total rewrite of the player and client stack. The headlines:

### Player & client split
- **New `fleet_player.py` daemon** (and `fleet-player.service`) owns the mpv lifecycle. It restarts independently on crash, so management is never blocked by a stuck player.
- **`fleet_client.py` is now a pure state machine** — no mpv ownership, no stored mode flag. State (`NO_MEDIA` / `PLAYING_CONNECTED` / `PLAYING_OFFLINE`) is derived every tick from `(symlink exists, server reachable, pinned)`.
- **File-based handoff** between the two: `playlist.current`, `osd.json`, `.restart-player` mtime. No IPC sockets between them.
- Slow-poll loop is **jittered** to avoid 150 Pis stampeding the server at the same second.

### Identity that survives SD cloning
- Device ID is now derived from the **Pi SoC serial in `/proc/cpuinfo`** (`identity.py`), not `/etc/machine-id`. Mass-flashed golden images no longer collide.

### USB pinning, done properly
- **`usb_sync.sh` was rewritten** as an idempotent atomic-swap + pinning model.
  - Hashes USB content → `releases/usb-<hash12>`.
  - Atomic symlink swap of `current`.
  - Writes `pinned=true, pinned_source="usb"` into `state.json`.
  - Re-inserting the same stick is a no-op; different content swaps in cleanly.
  - Keeps the 3 newest releases on disk (fixed retention bug from v0.1).
- **Dashboard surfaces pin state** — `🔌` icon + Release button. Releasing queues a `force_poll` command for the device.

### Tailscale mesh onboarding
- The captive-portal onboarding flow now calls `tailscale up` with the per-SD authkey after Wi-Fi joins. Failure is **non-fatal** — the Pi still boots and can serve USB-pinned media offline.

### Local control UI upgrade
- Mobile-friendly local UI (`:8080`) now exposes:
  - Current derived state + offline reason
  - Pin banner (with source: USB)
  - **Force-show status overlay** button (30 s) — useful after the 5-min auto-hide
  - Restart player (player only, not management)
  - Check for updates (force manifest poll)
  - Reset Wi-Fi (triggers setup mode on next boot)

### Deployment unification
- Golden image script installs **mpv + Tailscale** by default; VLC is gone (fatal DRM bug on Trixie).
- systemd: player split into its own unit, no `network-online.target` dependency, `WatchdogSec` removed (daemons weren't sending sd_notify pings).
- **`diag.sh`** — one-shot health-check script for SSH-in triage (uptime, temps, mpv status, current symlink, state.json, recent logs).

See [`docs/architecture.md`](docs/architecture.md) for the full architecture and data-flow diagrams.

---

## 📂 Repository Structure

- [`/server`](server/) — FastAPI central brain, SQLite database, auth, manifests, command queue.
- [`/dashboard`](dashboard/) — Static SPA served from the server (built artifacts in `dist/`).
- [`/client`](client/) — On-Pi software:
  - `fleet_client.py` — management daemon (heartbeats, manifest polling, state machine)
  - `fleet_player.py` — mpv lifecycle daemon
  - `local_control.py` — Flask local UI on `:8080`
  - `identity.py` — SoC-serial device-ID derivation
  - `usb_sync.sh` — atomic USB pin/swap
  - `diag.sh` — one-shot health-check
  - `*.service` — systemd unit files
- [`/client/onboarding`](client/onboarding/) — AP Manager, Captive Portal, HDMI status renderer, Tailscale join.
- [`/deploy`](deploy/) — Golden-image first-run, SD-card flashing, udev rules, Headscale authkey minting.
- [`/docs`](docs/) — Architecture, device provisioning, and the Tech Team Handbook.

---

## 🛠 Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Client:** Python, Flask (local UI), systemd
- **Video Engine:** `mpv` (KMS/DRM, hardware-accelerated)
- **Mesh:** Headscale (self-hosted) + Tailscale clients
- **Deployment:** Bash, Raspberry Pi Imager CLI, udev

---

## 🚀 Getting Started

This repo is not a one-line installer — it ships with a SD-card provisioning pipeline and a deploy-from-server workflow. The high-level steps:

1. **Stand up the server** (Mac mini or any Linux/macOS box):
   - `pip install -r server/requirements.txt`
   - Configure admin credentials and PSK in `server/config.py`.
   - Run `uvicorn server.main:app` (or systemd-manage it).
2. **Stand up Headscale** on a public VPS and mint per-SD preauth keys.
3. **Flash SD cards** with `deploy/flash_and_prepare_sd.sh` — this bakes the golden image, injects the client payload, the per-SD authkey, and the server's tailnet IP.
4. **Boot a Pi** → captive portal → enter Wi-Fi → Pi joins mesh and registers with the server.
5. **Publish a manifest** from the dashboard for the Pi's group → device picks it up on next poll.

For field-tech instructions (setup, USB workflow, troubleshooting), see [`docs/techteam-handbook.md`](docs/techteam-handbook.md).

---

## 📚 Documentation

- [Architecture](docs/architecture.md) — components, state machine, data flows, failure modes, command set
- [Device Provisioning](docs/device-provisioning.md) — golden-image build, per-SD authkey workflow
- [Tech Team Handbook](docs/techteam-handbook.md) — on-site setup, media formats, USB workflow, troubleshooting
