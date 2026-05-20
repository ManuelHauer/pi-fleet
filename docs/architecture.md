# Pi Fleet Architecture — v0.2

## Components

- **Fleet Server (Mac mini at HQ)**
  - FastAPI + SQLite (device registry, manifests, commands, heartbeats, pin state)
  - Media file hosting (local filesystem)
  - Admin endpoints (HTTP Basic / Bearer)
  - Dashboard (static SPA served from `/dashboard/`)
  - Reachable from the fleet only via its tailnet IP (Headscale mesh).

- **Headscale (Hetzner VPS)**
  - Self-hosted Tailscale coordination server.
  - Mints single-use, ephemeral preauth keys at SD-prep time.
  - Server + Pis communicate only over tailnet addresses.

- **Fleet Player (Pi)** — `client/fleet_player.py`, `fleet-player.service`
  - Owns the mpv lifecycle (DRM/KMS, ALSA, IPC socket).
  - Reads `/opt/fleet-media/playlist.current` to know what to play.
  - Reads `/opt/fleet-media/osd.json` to draw overlays.
  - Watches `/opt/fleet-media/.restart-player` mtime to force reload.
  - Renders the idle info-card PNG when no playlist is available.

- **Fleet Client (Pi)** — `client/fleet_client.py`, `fleet-client.service`
  - Management daemon. Does **not** own mpv.
  - Fast loop (10s): cheap `HEAD /health` probe of the server.
  - Slow loop (30s): manifest poll + heartbeat (skips manifest when pinned).
  - On manifest update: atomic symlink swap → write `playlist.current` →
    touch `.restart-player`. That's the whole handoff to the player.
  - Maintains `osd.json` per the OSD rules (offline countdown / connected flash).
  - Persists derived state into `state.json` for the local UI and diag.sh.

- **Local Control UI (Pi)** — `client/local_control.py`, `fleet-local-control.service`
  - Flask app on `:8080`.
  - Shows pinned banner, derived state, offline reason.
  - Buttons: restart player, check for updates, force-show 30-s status overlay,
    Wi-Fi reset, reboot.

- **Onboarding (Pi)** — `client/onboarding/*`, `fleet-onboard.service`
  - Runs once on first boot if no Wi-Fi credentials.
  - Brings up AP + captive portal + HDMI status.
  - After Wi-Fi joins, calls `tailscale up` with the per-SD authkey
    (non-fatal: Pi boots even if mesh join fails).

## State Machine (derived, not stored)

```
                          ┌──────────────┐
                          │  ONBOARDING  │  (first boot only; until onboard-done exists)
                          └──────┬───────┘
                                 ▼
                ┌────────────────────────────────┐
                │   compute_state() each tick    │
                │   inputs:                      │
                │     - has current symlink?     │
                │     - server reachable?        │
                │     - pinned?                  │
                └──────┬──────────┬──────────────┘
                       │          │
              has_media│          │no current
                       ▼          ▼
              ┌────────────┐   ┌──────────┐
   reachable  │ PLAYING_   │   │ NO_MEDIA │
       ◄──────│ CONNECTED  │   │ (idle    │
              │            │   │  screen) │
              └─────▲──────┘   └──────────┘
                    │
                    │unreachable
                    ▼
              ┌──────────────────┐
              │ PLAYING_OFFLINE  │  ← OSD countdown 5m, then quiet
              └──────────────────┘
```

`offline_reason` is a cheap-to-specific drill-down: `no wifi` → `no mesh` → `no server`.

## Data flow: dashboard-pushed manifest

1. Admin uploads media to server, publishes a manifest for a group.
2. `fleet_client` polls `/device/manifest/<id>` (falls back to `/manifest/<group>`).
3. If `version` differs from local `state.json["current_version"]`:
   - Download missing files to `releases/<version>/`, verify sha256.
   - `ln -sfn releases/<version> current.new && mv -T current.new current`
   - Write `playlist.current`, touch `.restart-player`.
4. `fleet_player` sees mtime change → terminates mpv → starts new mpv on new playlist.
5. `fleet_client` posts heartbeat (now including pin fields).

## Data flow: USB pin

1. Tech inserts USB stick. Udev fires `usb_sync.sh`.
2. Stick is mounted read-only into a private mktemp dir.
3. Script finds a `fleet/` dir or root media; otherwise silently exits.
4. Content hash → release ID `usb-<hash12>`. New release dir is created
   `releases/usb-<hash>.tmp`, then atomically renamed to `releases/usb-<hash>`.
5. Atomic symlink swap of `current`.
6. `state.json` updated: `pinned=true, pinned_source="usb", pinned_at=<epoch>`.
7. `playlist.current` rewritten, `.restart-player` touched.
8. `fleet_client` heartbeats include the pin → server marks device pinned
   → dashboard shows `🔌` + Release button.
9. Manifest polls are skipped (client-side) until pin is cleared.

## Data flow: dashboard "Release" button

1. Admin clicks Release. Dashboard POSTs `/admin/devices/<id>/unpin`.
2. Server clears `pinned=0` and queues a `force_poll` command for the device.
3. On next heartbeat, `fleet_client` picks up the command, clears local
   `state.json["pinned"]`, immediately runs `_poll_manifest()`.

## Failure modes

| Failure | Behavior |
|---|---|
| Server unreachable | `fleet_client` stays in `PLAYING_OFFLINE` if media is on disk, otherwise `NO_MEDIA` idle screen. mpv keeps looping last good content. |
| Partial download / checksum mismatch | Staging dir is removed; no symlink change; client keeps current playback. |
| mpv crash | `fleet-player.service` restarts mpv within seconds (`Restart=always`). `fleet-client` is untouched. |
| Tailscale fails to join | Onboarding still completes (`onboard-done` is written). Pi runs in offline mode, can still serve USB-pinned media. |
| SD card cloning | Identity is derived from SoC serial, so each Pi registers as itself regardless of shared image. |
| Wi-Fi never available at venue | Pi stays in `NO_MEDIA` until USB stick arrives → then runs offline as a kiosk indefinitely. |

## Command set

| Command | Effect on client |
|---|---|
| `update_now` | Force `_poll_manifest()` regardless of timing. |
| `vlc_restart` / `player_restart` | Touch `.restart-player` so fleet_player reloads mpv. |
| `unpin` / `force_poll` | Clear local pin state, then `_poll_manifest()`. |
| `health_probe` | Return current health stats. |
| `reboot` | `sudo reboot`. |
| `shell` | (lab only) run arbitrary shell, return first 500 chars. |
