# Ars Festival Media Orchestration — Architecture v0.1

## Components
- **Fleet Server (MacBook)**
  - FastAPI + SQLite (device registry, manifests, commands, heartbeats)
  - Media file hosting (local filesystem)
  - Admin endpoints (basic auth or Bearer token)
  - Dashboard (static SPA)

- **Fleet Client (Raspberry Pi)**
  - systemd service that:
    - polls manifest
    - downloads new/changed media (sha256 verified)
    - atomically swaps current media directory
    - restarts VLC loop
    - sends heartbeats + fetches commands

## Data flow
1. Admin uploads media files to server.
2. Admin publishes a **manifest** for a group (latest-wins).
3. Clients poll `/manifest/<group>` and detect new `version`.
4. Clients download required `/media/file/<filename>` and verify checksums.
5. Client swaps `current` symlink -> new release directory.
6. Client restarts VLC loop.
7. Client posts heartbeat; server returns pending commands.

## Failure modes
- Server unreachable: client keeps playing last known good.
- Partial download / checksum mismatch: client aborts update, keeps current.
- VLC crash: systemd restarts client; client restarts VLC.

## Phase 1 command set
- `update_now`
- `vlc_restart`
- `health_probe`
- `reboot`
- (optional) `shell` for lab only
