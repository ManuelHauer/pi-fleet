# Open Questions — Fleet Server Hosting

*For the colleague offering the server ("host the files and handle the
negotiating of the Pis"). Context in
[server-hosting-qa.md](server-hosting-qa.md). Short answers are fine —
most of these are one-liners.*

---

## ✅ Decisions from the answers (2026-07-07)

- **Deployment: Docker on the PowerEdge** (his preference; VM resources as
  needed, disk effectively unlimited — 32 TB pool). Use
  [`docker-compose.nginx-edge.yml`](../deploy/server/docker-compose.nginx-edge.yml).
- **Topology: edge VPS with nginx in front, TLS terminates there**; the
  PowerEdge runs the fleet app on :8550 (publicly addressable, but firewall
  8550 to the VPS only). Requested nginx config delivered:
  [`deploy/server/nginx-fleet.conf`](../deploy/server/nginx-fleet.conf) —
  two placeholders to fill (`<FLEET_DOMAIN>`, `<POWEREDGE_IP>`).
- **Correction to Q6 ("the codebase does WireGuard tunneling, right?"):**
  **No tunnel is needed.** The Pis only make *outbound HTTPS* requests to the
  fleet domain (30-second polls + media downloads); the server never connects
  to a Pi. Outbound 443 from the venue Wi-Fi is the entire network
  requirement. (Tailscale/WireGuard support exists but is optional — only for
  SSH-into-Pis convenience, not for fleet operation.)
- **Backups: "no backups, YOLO"** → we handle it ourselves: one nightly
  `rsync`/snapshot of the Docker volume (`fleet-data`, ≤250 GB) to any second
  disk during build-up + festival. One cron line; losing the library mid-
  festival would mean re-uploading everything, so we won't run bare.
- **Contact:** "hottakeherbert" (internal runbook only).
- **Still open:** monitoring hook (Q9 — we'll watch `/health` ourselves if
  none), media-privacy policy sign-off (Q12), AEC IT rules (Q13), and the
  actual **domain name** for `<FLEET_DOMAIN>`.

---

## The server itself

1. **What is the machine?** (VM/bare metal, OS + version, CPU/RAM, where does
   it physically/logically live — AEC infrastructure, hosted VPS, …?)
   Anwser: VM, was du bracuhst krigst du. 
2. **How much disk can we get** for the media library, and can it grow if an
   artist shows up with 80 GB of video? (We're budgeting 100–250 GB.)
   Anwser: infite! (32 tb available)
3. **Docker available/allowed**, or do you prefer the bare-metal systemd
   install? (Both are packaged — your call.)
   Anwser: bevorzugt docker , anstatt VM
4. **Who administrates it** during the festival (OS updates, reboots,
   emergencies), and how do we reach that person on show days?
   Anwser: he is in the team. 

## Reachability — the one architecture-deciding question

5. **Is the server reachable via public HTTPS from the venue networks?**
   The Pis poll outbound from ~20 venue Wi-Fi networks around Linz.
   - If YES (public IP or reverse-proxied): we need a **DNS name**
     (e.g. `fleet.<something>.at`) and ports 80/443. Can you provide the
     subdomain, or should we point one of ours at your IP?
   - If NO (internal-only): every Pi must join a VPN/mesh. We have a working
     Tailscale/Headscale setup from v0.2 — could your server also host the
     Headscale coordinator (one small service, UDP 3478 + HTTPS), or is
     there an existing AEC VPN the Pis could use instead?
     Anwser: it is via edge-VPS, running nginx, provide a nginx config file please. clarify the server running the pi-fleet service hadnshaking is not VPS but the poweredge accesable via public IP. 
6. **Any firewalling/filtering on the venue Wi-Fi networks we should know
   about** (blocked outbound ports, captive portals, client isolation)?
   Outbound 443 is all the Pis need.
   Anwser: blocked outbounds are not relevant, because the codebase macht ja eher wireguard tunneling (right?)
7. **TLS**: fine if our reverse proxy (Caddy) does Let's Encrypt on the box,
   or does AEC terminate TLS centrally / provide certificates?
   Anwser: our reverse proxy will be replaced by the edge vps. 

## Operations

8. **Backups**: can you snapshot/rsync one data directory nightly during
   build-up + festival (≤ 250 GB), or should we handle backup ourselves?
   Anwser: no backups, YOLO! 
9. **Monitoring**: do you have an uptime system we should hook `GET /health`
   into, or shall we watch it ourselves?
10. **Bandwidth**: any cap or shared-link concern if ~20 devices pull a 1 GB
    file within a few minutes after a content update? We can stagger rollouts
    per venue if needed.
    Anwser: not at all. 
11. **Lifetime**: server available from when (we'd like it ~mid-August for SD
    provisioning) until when (teardown + a final backup after Sept 13)?
    Anwser: irrelevant

## Policy

12. **Media privacy**: device media downloads are link-knowledge-only (no
    auth) — acceptable, or must everything sit behind auth/VPN?
13. Any **AEC IT rules** we must follow (allowed software, admin accounts,
    logging, security review), and is there someone from IT we should loop in
    early?
14. Are you okay being named as **server contact** in our internal runbook
    (not in the public repo)? 
    Anwser: "hottakeherbert"


