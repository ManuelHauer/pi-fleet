# 🎬 Pi Fleet — Media Orchestration

A centralized fleet management and media orchestration platform designed to remotely manage, monitor, and deploy headless video playback across up to 150 Raspberry Pi (3/4/5) players for large-scale exhibitions and events.

## ✨ Key Features

- **Centralized Dashboard (Server):** FastAPI backend and web UI to mass-assign media, push updates, and monitor device health (CPU temps, uptime, playback status) in real-time.
- **Headless Hardware-Accelerated Playback:** Uses `mpv` with `--vo=drm` on Raspberry Pi OS Lite (Bookworm/Trixie) for buttery-smooth looping video without a desktop environment.
- **Field-Ready Provisioning:** Zero-touch "Golden Image" SD card creation. Field technicians can configure Wi-Fi on-site using a first-boot Captive Portal / AP Hotspot.
- **Technician Local Control:** Each Pi hosts a mobile-friendly local Web UI (port 8080) for on-the-fly volume adjustments, manual syncing, and reboots.
- **Bulletproof Fallbacks:** If the venue network dies, technicians can plug in a USB stick to automatically wipe the current media, copy the new files, rebuild an offline manifest, and instantly resume playback via `udev` triggers.

## 📂 Repository Structure

- `/server` — The FastAPI central brain, SQLite database, and Web Dashboard.
- `/client` — The Python daemon running on each Pi, handling polling, updates, mpv IPC, and the local technician UI.
- `/client/onboarding` — The AP Manager and Captive Portal for headless Wi-Fi setup.
- `/deploy` — Automated scripts to flash OS images, inject the client payload, and configure systemd services.
- `/docs` — Architecture decisions and the Tech Team Handbook.

## 🛠 Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Client:** Python, Flask (Local UI), systemd
- **Video Engine:** `mpv` (KMS/DRM)
- **Deployment:** Bash, Raspberry Pi Imager CLI, udev
