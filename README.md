# 🎬 Pi Fleet — Media Orchestration

A centralized fleet management and media orchestration platform designed to remotely manage, monitor, and deploy headless video playback across up to 150 Raspberry Pi (3/4/5) players for large-scale exhibitions and events.

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

## 📂 Repository Structure

- `/server` — The FastAPI central brain, SQLite database, and Web Dashboard.
- `/client` — Two daemons (`fleet_client.py` + `fleet_player.py`), the local-control UI, USB sync, identity module, and `diag.sh` health-check.
- `/client/onboarding` — The AP Manager, Captive Portal, and Tailscale mesh join for headless Wi-Fi setup.
- `/deploy` — Automated scripts to flash OS images, inject the client payload, mint Headscale authkeys, and configure systemd services.
- `/docs` — Architecture decisions and the Tech Team Handbook.

## 🛠 Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Client:** Python, Flask (Local UI), systemd
- **Video Engine:** `mpv` (KMS/DRM)
- **Mesh:** Headscale (self-hosted) + Tailscale clients
- **Deployment:** Bash, Raspberry Pi Imager CLI, udev
