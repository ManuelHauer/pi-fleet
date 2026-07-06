# Changelog

## v0.4 — July 2026 (keyboard control + hardware-test fixes)

First run on real hardware (Raspberry Pi 4 / Raspberry Pi OS Trixie). See
[docs/hardware-test-findings.md](docs/hardware-test-findings.md).

### New feature
- **USB-keyboard control** (`client/keyboard_control.py`, `fleet-keyboard.service`):
  a keyboard plugged into a Pi controls playback — volume/mute, screen rotation,
  slideshow speed, next/previous, pause — for venues with no phone/network.
  Settings go through the same live-apply path (player-settings.json → mpv IPC)
  so keyboard, phone UI and dashboard stay in sync. Hot-plug aware. Needs
  `python3-evdev` (added to the golden image).

### Fixes from the hardware test (also apply to v0.3 deployments)
- **Provisioning: grow rootfs before apt.** Stock root is ~2 GB with auto-expand
  disabled; `apt install` ran out of space before the media-partition step grew
  it. Reordered; exfatprogs installed on demand.
- **Player black screen:** PIL couldn't save the idle/setup card to a `.tmp`
  name (`unknown file extension`). Save with explicit `format="PNG"`.
- **Onboarding stole the Wi-Fi radio:** it raced NetworkManager and raised the
  setup hotspot before the venue Wi-Fi connected. Now waits for any Wi-Fi
  profile to come online first.
- **config.json unreadable by the service user:** written root-only, but daemons
  run as `pi` → wrong-server defaults, no registration. `chown pi:pi`.
- **Release (unpin) ~70 s late:** heartbeat race re-pinned the device; now
  reports `pinned=0` before re-polling → swaps back in one cycle.
- Tailscale install made non-fatal.

## v0.3 — July 2026 (refactor + festival features)

Full refactor pass over the v0.2 stack. The architecture (split daemons,
derived state machine, USB pinning, serial identity) is retained; everything
around it was hardened and extended for AEF26.

### New features
- **Playback settings, live**: per-device screen rotation (0/90/180/270°),
  slideshow slide duration, volume/mute — settable from the dashboard AND the
  on-Pi phone UI, applied via mpv IPC without interrupting playback,
  reported back through heartbeats.
- **SD-card media**: every card gets an exFAT **FLEET-MEDIA** partition
  visible on macOS/Windows. Drop media on it with any laptop; the Pi detects
  changed content at boot (and every 30 s) and pins to it — zero-copy, full
  card capacity, no 4 GB FAT32 limit.
- **Pre-primed SD cards**: `fleet-setup.toml` (venue Wi-Fi, label/group,
  playback presets, server override) → zero-touch onboarding at the venue.
- **Dashboard rebuilt**: responsive (desktop + phone), device cards with live
  status/settings, device panel with settings push + media assignment,
  multi-file drag-drop upload with progress, group bulk-select, identify.
- **Local control UI rebuilt**: phone-first, adds rotation + slide duration +
  SD controls + identify.
- **Onboarding portal rebuilt**: network list with signal bars, async connect
  with progress page, honest "watch the device screen" guidance.
- New device commands: `set_settings`, `identify`, `play_sd`.
- Server deploy package: docker-compose + Caddy auto-TLS, bare-metal
  installer with generated secrets, hardened systemd unit.
- Docs: server-hosting Q&A + open questions (for the hosting colleague),
  media workflow analysis, refreshed architecture/provisioning/handbook.

### Fixes (v0.2 bugs)
- **NO_MEDIA devices never polled or heartbeated** — a fresh Pi could never
  receive its first assignment from the server. Now all states poll.
- **Onboarding was wpa_supplicant/hostapd-based** — Pi OS Bookworm/Trixie
  (NetworkManager) ignores `wpa_supplicant.conf`; hostapd fought NM for the
  radio. Whole stack rewritten on nmcli (venue profiles, NM hotspot,
  dnsmasq-shared.d captive DNS).
- **Setup instructions were invisible**: they went to tty1, which mpv's DRM
  output covers. The AP name/password now render THROUGH the player.
- **First-boot installer ran once and masked failures** (`|| true`) — a first
  boot without network left a half-installed card. Now a retrying systemd
  unit runs it until success.
- Onboarding no longer blocks fleet-client/local-control (offline kiosks get
  their volume UI), and the portal no longer times out after 10 minutes.
- Playlists filter non-media files (a README.txt on a stick no longer ends
  up "playing").
- `no mesh` offline reason only shown when tailscale is actually installed.
- Heartbeat table pruned (500 rows/device) instead of growing unbounded.
- Weak defaults removed from deploy artifacts; remote shell disabled by
  default; local UI session key no longer hardcoded.

### Compatibility
- v0.2 devices keep working against the v0.3 server (endpoints unchanged,
  new fields optional). v0.3 devices degrade gracefully against a v0.2
  server (unknown form fields ignored).
- DB migrates automatically (additive columns).

## v0.2 — May 2026 (rewrite)

Player/client split with file-based handoff, derived state machine,
serial-number identity, idempotent USB pinning, Tailscale mesh onboarding,
golden-image deploy, diag.sh. See README §"What's New in v0.2".

## v0.1 — February 2026 (prototype)

Initial GPT-5.2-built prototype: FastAPI server, VLC-based client,
captive-portal onboarding, SSH push-install.
