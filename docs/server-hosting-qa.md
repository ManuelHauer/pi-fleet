# Fleet Server Hosting — Q&A for the Server Admin

*For the ARS festival tech-team colleague providing the server. Everything you
need to know to host the fleet backend, in Q&A form. Companion doc:
[open-questions-server.md](open-questions-server.md) — the things we need to
know from you.*

---

## What is this, in one paragraph?

A single small web service (Python/FastAPI) that ~50–150 Raspberry Pis at the
festival venues poll over HTTPS every 30 seconds. It stores a device registry
and playback settings in SQLite and hosts the media files (videos/images) that
the Pis download. Admins use a built-in web dashboard (static HTML served by
the same service) from desktop or phone. That's it — no database server, no
message queue, no build pipeline.

## What's the tech stack exactly?

| Layer | Technology |
|---|---|
| App | Python 3.11+ · FastAPI · uvicorn |
| Data | SQLite (WAL mode), single file |
| Media storage | Plain files on disk (sha256-prefixed names) |
| Dashboard | Static single-file HTML/JS, served at `/dashboard/` |
| Auth | Admin: HTTP Basic → JWT session · Devices: pre-shared key header |
| TLS | Terminated by a reverse proxy (Caddy config included, or your own nginx/traefik) |

Repo: https://github.com/ManuelHauer/pi-fleet — server code in `server/`,
deployment files in `deploy/server/`.

## How do I run it?

Two supported options, pick whatever fits your setup:

**A) Docker Compose (recommended if the box runs Docker anyway)**
```
cd pi-fleet/deploy/server
cp .env.example .env        # fill in domain + secrets
docker compose up -d
```
That starts the app + a Caddy reverse proxy with automatic Let's Encrypt TLS
for the domain in `.env`. Persistent data lives in named volumes.

**B) Bare metal (Debian/Ubuntu, behind your existing reverse proxy)**
```
sudo bash deploy/server/install_server.sh
```
Installs to `/opt/pi-fleet`, data in `/var/lib/fleet-server`, systemd service
`fleet-server` bound to `127.0.0.1:8550`, secrets auto-generated into
`/etc/fleet-server/env`. You point your existing nginx/caddy at `:8550`.

## What resources does it need?

| Resource | Sizing |
|---|---|
| CPU / RAM | Tiny. 1–2 vCPU, 1–2 GB RAM is plenty (it mostly serves files). |
| Disk | **The real question.** Media library for the festival: budget **100–250 GB** to be safe (loops are typically 50 MB–2 GB each). SQLite DB stays in the low MB range. |
| Network in | ~150 devices × 1 heartbeat/30 s = ~5 requests/s of tiny JSON. Noise. |
| Network out | Bursty: when a 1 GB video is assigned to 20 devices, they all download it. With per-device jitter it's spread out, but plan for the uplink to sustain a few hundred Mbit/s bursts, or roll content out venue-by-venue. |
| Ports | 443 (+80 for the TLS challenge/redirect) if public. Internal port is 8550. |

## What has to be reachable from where?

- **Pis → server**: outbound HTTPS only. The Pis sit in venue Wi-Fi networks
  behind NAT — they poll; the server never connects to them. So the server
  must be reachable **from the venue networks** (public HTTPS is the simple
  answer; a VPN/Tailscale-only server works too if every Pi joins the mesh).
- **Admins → server**: the dashboard, same HTTPS endpoint.
- **Server → internet**: only for TLS certificate issuance (and OS updates).

## What about security?

- All admin endpoints require login; device endpoints require the pre-shared
  device key. Media downloads are unauthenticated by design (festival loops,
  not secrets) — tell us if that's a problem for your policy.
- Secrets live in env vars / an env file, never in the repo.
- The optional remote-shell feature is **disabled by default**
  (`FLEET_DISABLE_SHELL=1`) in both deployment variants.
- Recommended: keep the box's SSH access as you normally would; the app needs
  no inbound SSH for us.

## What needs backing up?

One directory: the data dir (`/var/lib/fleet-server` or the `fleet-data`
Docker volume). It contains `fleet.db` + `media/`. A nightly rsync/snapshot
during the festival build-up week is plenty. Losing it means re-uploading
media and re-registering devices (annoying, not fatal — Pis keep playing
their local copies).

## What monitoring makes sense?

- `GET /health` returns `{"status":"ok", ...}` — wire it into whatever
  uptime checker you use.
- Disk-space alert on the data dir (media uploads are the only thing that
  grows).
- The dashboard itself shows device online/offline counts — that part is on us.

## When does it need to exist?

Ideally 3–4 weeks before the festival (Sept 9–13) so we can provision SD
cards against the real URL. Load stays near zero until build-up week; the
hot phase is Sept 1–13. After the festival it can be archived (final backup)
and switched off.
