# 🎬 Pi Fleet — Media Orchestration

A centralized fleet management and media orchestration platform designed to remotely manage, monitor, and deploy headless video playback across up to 150 Raspberry Pi (3/4/5) players for large-scale exhibitions and events.

> **Status:** v0.3 (July 2026). See [CHANGELOG.md](CHANGELOG.md) for the full v0.3 changelog; the [v0.2 rewrite notes](#-whats-new-in-v02) below still describe the core architecture.

---

## ✨ Key Features

- **Centralized Dashboard (Server):** FastAPI backend and a responsive web UI (desktop + phone) to mass-assign media, push playback settings, and monitor device health (state, temps, uptime, pin status) in real-time. Multi-file drag-drop upload with progress.
- **Live Playback Settings:** per-device **screen rotation** (0/90/180/270°), **slideshow slide duration**, volume/mute — settable from the dashboard or the on-Pi phone UI, applied via mpv IPC **without interrupting playback**.
- **Three Media Paths, One Pin Model:** server push (assign in dashboard), **SD card** (exFAT `FLEET-MEDIA` partition mounts on any laptop — drop files, boot, plays), and **USB stick** (insert into a running Pi). SD/USB content pins the device (💾/🔌 + Release button in the dashboard).
- **Pre-Primed SD Cards:** a `fleet-setup.toml` on the card (venue Wi-Fi, label/group, playback presets) gives **zero-touch onboarding** — plug in at the venue, done.
- **Headless Hardware-Accelerated Playback:** `mpv` with `--vo=drm` on Raspberry Pi OS Lite (Bookworm/Trixie). (No VLC — fatal DRM bug on Trixie.)
- **Split Daemon Design:** `fleet_player.py` owns mpv; `fleet_client.py` handles management; file-based handoff. mpv crashes recover in seconds without disturbing management.
- **NetworkManager-Native Onboarding:** venue Wi-Fi profiles and the first-boot captive-portal hotspot both run on `nmcli` (the Bookworm/Trixie default stack). Setup instructions render on the venue screen through the player.
- **Optional Mesh (Headscale/Tailscale):** per-SD single-use authkeys as in v0.2 — now optional; a public-HTTPS fleet server works without any mesh (devices poll outbound only).
- **Identity Survives SD Cloning:** device ID from the Pi SoC serial, not `/etc/machine-id`.
- **Derived State Machine:** `NO_MEDIA` / `PLAYING_CONNECTED` / `PLAYING_OFFLINE`, recomputed every tick — no stored mode flag. Idle/offline/identify overlays; never a black screen.
- **Server Deploy Package:** docker-compose + Caddy automatic TLS, or a bare-metal installer with generated secrets ([deploy/server/](deploy/server/), [docs/server-hosting-qa.md](docs/server-hosting-qa.md)).

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
- [`/client/onboarding`](client/onboarding/) — nmcli Wi-Fi manager, captive portal, fleet-setup.toml preseed, on-screen status renderer, Tailscale join.
- [`/deploy`](deploy/) — SD provisioning (flash, prep, FLEET-MEDIA partition, preseed example), udev rules, Headscale authkey minting.
- [`/deploy/server`](deploy/server/) — docker-compose + Caddy, bare-metal installer, systemd unit.
- [`/docs`](docs/) — architecture, provisioning, tech-team handbook, server hosting Q&A, media workflow.

---

## 🛠 Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Client:** Python, Flask (local UI), systemd, NetworkManager (nmcli)
- **Video Engine:** `mpv` (KMS/DRM, hardware-accelerated)
- **Mesh (optional):** Headscale (self-hosted) + Tailscale clients
- **Deployment:** Bash, Raspberry Pi Imager CLI, udev, Docker/Caddy (server)

---

## 🚀 Getting Started

1. **Stand up the server** — two packaged options, see [docs/server-hosting-qa.md](docs/server-hosting-qa.md):
   - Docker: `cd deploy/server && cp .env.example .env && docker compose up -d`
   - Bare metal: `sudo bash deploy/server/install_server.sh`
2. **Flash + prep SD cards** — `deploy/flash_and_prepare_sd.sh disk4` (or Imager + `prepare_sd_card.sh`), optionally pre-priming venue Wi-Fi. See [docs/device-provisioning.md](docs/device-provisioning.md).
3. **First boot at HQ with internet** — installs packages, creates the FLEET-MEDIA partition, reboots into fleet operation.
4. **Deploy at the venue** — zero-touch (pre-primed) or phone onboarding via the `AEC-PI-XXXX` hotspot.
5. **Assign media in the dashboard** — devices sync within ~30 s; or load media via SD/USB with no server at all.

For field-tech instructions (setup, controls, media workflows, troubleshooting), see [`docs/techteam-handbook.md`](docs/techteam-handbook.md).

---

## 📚 Documentation

- [CHANGELOG](CHANGELOG.md) — what changed in v0.3
- [Architecture](docs/architecture.md) — components, state machine, data flows, failure modes, command set
- [Device Provisioning](docs/device-provisioning.md) — flash/prep/first-boot pipeline, preseed, golden-clone notes
- [Tech Team Handbook](docs/techteam-handbook.md) — on-site setup, rotation/slides/volume, SD & USB media, troubleshooting
- [Server Hosting Q&A](docs/server-hosting-qa.md) — for whoever hosts the backend
- [Open Questions — Server](docs/open-questions-server.md) — what we need from the server admin
- [Media Workflow](docs/media-workflow.md) — SharePoint → screen paths and improvement options
