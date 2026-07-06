# Open Questions — Fleet Server Hosting

*For the colleague offering the server ("host the files and handle the
negotiating of the Pis"). Context in
[server-hosting-qa.md](server-hosting-qa.md). Short answers are fine —
most of these are one-liners.*

## The server itself

1. **What is the machine?** (VM/bare metal, OS + version, CPU/RAM, where does
   it physically/logically live — AEC infrastructure, hosted VPS, …?)
2. **How much disk can we get** for the media library, and can it grow if an
   artist shows up with 80 GB of video? (We're budgeting 100–250 GB.)
3. **Docker available/allowed**, or do you prefer the bare-metal systemd
   install? (Both are packaged — your call.)
4. **Who administrates it** during the festival (OS updates, reboots,
   emergencies), and how do we reach that person on show days?

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
6. **Any firewalling/filtering on the venue Wi-Fi networks we should know
   about** (blocked outbound ports, captive portals, client isolation)?
   Outbound 443 is all the Pis need.
7. **TLS**: fine if our reverse proxy (Caddy) does Let's Encrypt on the box,
   or does AEC terminate TLS centrally / provide certificates?

## Operations

8. **Backups**: can you snapshot/rsync one data directory nightly during
   build-up + festival (≤ 250 GB), or should we handle backup ourselves?
9. **Monitoring**: do you have an uptime system we should hook `GET /health`
   into, or shall we watch it ourselves?
10. **Bandwidth**: any cap or shared-link concern if ~20 devices pull a 1 GB
    file within a few minutes after a content update? We can stagger rollouts
    per venue if needed.
11. **Lifetime**: server available from when (we'd like it ~mid-August for SD
    provisioning) until when (teardown + a final backup after Sept 13)?

## Policy

12. **Media privacy**: device media downloads are link-knowledge-only (no
    auth) — acceptable, or must everything sit behind auth/VPN?
13. Any **AEC IT rules** we must follow (allowed software, admin accounts,
    logging, security review), and is there someone from IT we should loop in
    early?
14. Are you okay being named as **server contact** in our internal runbook
    (not in the public repo)?
