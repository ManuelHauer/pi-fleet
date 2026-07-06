# Pi Fleet Architecture — v0.3

## Components

- **Fleet Server** — `server/`
  - FastAPI + SQLite (device registry, manifests, commands, heartbeats,
    pin state, playback settings), media file hosting, admin API.
  - Dashboard (static single-file SPA) served at `/dashboard/`.
  - Deployment: docker-compose + Caddy auto-TLS, or bare-metal systemd —
    see `deploy/server/` and `docs/server-hosting-qa.md`.
  - Reachability model: devices poll **outbound HTTPS** — a public HTTPS
    endpoint is sufficient. The Tailscale/Headscale mesh from v0.2 remains
    supported but is now **optional** (`no mesh` diagnostics only appear
    when tailscale is installed).

- **Fleet Player (Pi)** — `client/fleet_player.py`, `fleet-player.service`
  - Owns the mpv lifecycle (DRM/KMS, ALSA, IPC socket).
  - Reads `playlist.current` (what to play), `osd.json` (overlay),
    `player-settings.json` (rotation / image duration / volume / mute —
    **applied live via mpv IPC, no playback restart**),
    `.restart-player` (mtime = reload).
  - Renders the idle info-card; shows the onboarding setup card while
    `.onboarding-active` exists (setup instructions render THROUGH the
    player — v0.2 wrote to tty1, which mpv's DRM plane covered).

- **Fleet Client (Pi)** — `client/fleet_client.py`, `fleet-client.service`
  - Management daemon. Fast loop (10 s): `HEAD /health` probe. Slow loop
    (30 s, jittered): manifest poll + heartbeat + SD-partition scan —
    **in every state, including NO_MEDIA** (v0.2 bug: idle devices went
    silent and could never receive their first assignment).
  - Manifest update: download + sha256 verify → atomic symlink swap →
    write playlist → touch restart trigger.
  - Heartbeat carries pin state, derived state, IP and current playback
    settings; response delivers queued commands
    (`set_settings`, `identify`, `play_sd`, `update_now`, `player_restart`,
    `unpin`/`force_poll`, `health_probe`, `reboot`[, `shell` if enabled]).

- **Local Control UI (Pi)** — `client/local_control.py`, port 8080
  - Phone-first, works fully offline: rotation, slide duration,
    volume/mute, identify, restart player, sync now, play-from-SD,
    Wi-Fi reset, reboot.

- **Keyboard Control (Pi)** — `client/keyboard_control.py`, `fleet-keyboard.service`
  - A USB keyboard plugged into the Pi drives playback with no phone/network:
    volume/mute, rotation, slideshow speed (transport: next/prev/pause).
  - Settings go through `player-settings.json` (same live-apply path as the
    dashboard/phone UI); transport actions go straight to mpv IPC. Hot-plug
    aware; reads `/dev/input` via the `input` group (evdev).

- **Onboarding (Pi)** — `client/onboarding/`, `fleet-onboard.service`
  - **NetworkManager-native** (nmcli): venue profile with autoconnect,
    onboarding hotspot via NM shared mode (10.42.0.1), captive-portal DNS
    wildcard via `dnsmasq-shared.d`. (v0.2 wrote `wpa_supplicant.conf`,
    which Pi OS Bookworm/Trixie ignores.)
  - Boot order: `fleet-setup.toml` preseed → existing venue profile →
    USB `wifi.json` → captive portal (scans BEFORE the AP claims the
    radio; serves the cached list; async connect with progress page;
    **no timeout** — and it no longer blocks the other services).
  - Optional Tailscale join stays non-fatal.

## Media sources & the pin model

```
                 ┌──────────────┐
   dashboard ───▶│ fleet server │──── manifest poll ───▶ releases/<version>/
                 └──────────────┘                            │ symlink swap
                                                             ▼
   USB stick ── udev → usb_sync.sh ── copy+hash ──▶ /opt/fleet-media/current
                                                             ▲
   SD card ("FLEET-MEDIA" exFAT partition) ── zero-copy ─────┘
            fleet_client scans boot + every 30 s
```

- USB insert or SD content change → atomic swap + **pin**
  (`pinned_source: usb | sdcard`). Pinned devices heartbeat normally but
  skip manifest polls; the dashboard shows 🔌/💾 + Release.
- Release (dashboard) → `force_poll` command → local pin cleared → server
  manifest swaps back in. SD re-import only triggers again when the card
  content actually changes (or via "Play from SD").
- Playlists filter to playable extensions — `fleet-setup.toml`,
  `DROP-MEDIA-HERE.txt` etc. can live next to the media.

## Playback settings flow

```
dashboard ── PUT /admin/devices/{id}/settings ──▶ set_settings command
                                                       │ (next heartbeat)
local UI  ── writes ─────────────┐                     ▼
                                 ├──▶ player-settings.json ──▶ fleet_player
fleet-setup.toml [player] ── once┘         (mtime watch)        applies live
                                                                via mpv IPC
device heartbeat ◀── reports applied settings ── (dashboard shows truth)
```

Last writer wins; the server only pushes when an admin acts, so local and
remote edits don't fight.

## SD card layout (created at first boot by `setup_media_partition.sh`)

```
p1  bootfs       FAT32   512 MB   fleet code + fleet-boot-config.json (+ preseed)
p2  rootfs       ext4    ~8 GB    OS + /opt/fleet-media/releases
p3  FLEET-MEDIA  exFAT   rest     media drop zone + optional fleet-setup.toml
```

`prepare_sd_card.sh` disables the Pi OS auto-expand; the partition surgery
happens on-Pi because laptops lack ext4 tools. Cards too small for p3 fall
back to full-card rootfs.

## State machine (derived, not stored — unchanged from v0.2)

`NO_MEDIA | PLAYING_CONNECTED | PLAYING_OFFLINE`, recomputed every tick from
`(current symlink exists, server reachable, pinned)`. Offline reason drill-down
`no wifi → no mesh (only if tailscale installed) → no server`. OSD: 5-min
countdown per offline episode, ✓ flash on reconnect, identify badge on demand.

## Failure modes

| Failure | Behavior |
|---|---|
| Server unreachable | Playback continues from disk; `PLAYING_OFFLINE` + OSD countdown. |
| Partial/corrupt download | Staging dir discarded; current playback untouched. |
| mpv crash | `Restart=always` revives it in seconds; management daemon unaffected. |
| First boot without network | Retrying installer unit runs again next boot (v0.2's one-shot hook silently gave up). |
| Venue has no Wi-Fi | Zero-config USB/SD kiosk; onboarding portal waits forever without blocking playback. |
| SD cloned to many cards | Identity from SoC serial — no collisions. |
| Settings change mid-show | Applied live via IPC; no black frame. |
