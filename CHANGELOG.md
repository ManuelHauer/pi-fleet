# Changelog

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
