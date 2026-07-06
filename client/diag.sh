#!/bin/bash
# One-shot 'what's wrong with this Pi' dump. Safe to run repeatedly.
# Tech walks up to a misbehaving Pi, SSHes in (Tailscale SSH or LAN),
# runs this, pastes the output into chat.

echo "=== Fleet Diagnostic ==="
echo "Device ID: $(cat /etc/fleet-client/device-id 2>/dev/null || echo unknown)"
echo "Hostname:  $(hostname)"
echo "IP:        $(hostname -I)"
echo "Tailscale: $(tailscale ip -4 2>/dev/null || echo 'not joined')"
echo

echo "--- Services ---"
for s in fleet-onboard fleet-player fleet-client fleet-local-control tailscaled; do
    printf "%-25s %s\n" "$s" "$(systemctl is-active "$s.service" 2>/dev/null)"
done
echo

echo "--- State (/opt/fleet-media/state.json) ---"
if [ -f /opt/fleet-media/state.json ]; then
    cat /opt/fleet-media/state.json | python3 -m json.tool 2>/dev/null || cat /opt/fleet-media/state.json
else
    echo "(no state.json)"
fi
echo

echo "--- Player settings (/opt/fleet-media/player-settings.json) ---"
if [ -f /opt/fleet-media/player-settings.json ]; then
    cat /opt/fleet-media/player-settings.json | python3 -m json.tool 2>/dev/null || cat /opt/fleet-media/player-settings.json
else
    echo "(defaults: rotation 0, 10s slides, volume 100)"
fi
echo

echo "--- SD media partition (/media/fleet-sd) ---"
if mountpoint -q /media/fleet-sd 2>/dev/null; then
    df -h /media/fleet-sd | tail -1
    ls /media/fleet-sd 2>/dev/null | head -10
else
    echo "(not mounted — card has no FLEET-MEDIA partition or fstab entry missing)"
fi
echo

echo "--- OSD (/opt/fleet-media/osd.json) ---"
if [ -f /opt/fleet-media/osd.json ]; then
    cat /opt/fleet-media/osd.json | python3 -m json.tool 2>/dev/null || cat /opt/fleet-media/osd.json
else
    echo "(no osd.json — no overlay armed)"
fi
echo

echo "--- Current playlist (first 10 lines) ---"
if [ -f /opt/fleet-media/playlist.current ]; then
    head -10 /opt/fleet-media/playlist.current
else
    echo "(no playlist.current — fleet-player will be on the idle screen)"
fi
echo

echo "--- Current symlink ---"
ls -ld /opt/fleet-media/current 2>/dev/null || echo "(no current symlink — NO_MEDIA state)"
echo

echo "--- Recent player log (tail 20) ---"
tail -20 /var/log/fleet-player.log 2>/dev/null || echo "(no player log)"
echo

echo "--- Recent client log (journalctl, last 20) ---"
journalctl -u fleet-client -n 20 --no-pager 2>/dev/null
echo

echo "--- USB sync log (tail 10) ---"
tail -10 /var/log/fleet-usb-sync.log 2>/dev/null || echo "(no usb sync log yet)"
