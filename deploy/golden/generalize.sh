#!/bin/bash
# Generalize (sysprep) a fully-provisioned fleet Pi so its SD card can be
# CLONED to many Pis. Runs ON THE PI (over SSH), then powers off.
#
#   ssh pi@<master> 'sudo bash -s' < generalize.sh
#   # then image the card on a laptop (capture_golden.sh does both steps)
#
# What "generalize" means here: strip everything that must be UNIQUE per
# device, but KEEP the installed packages + fleet software (that's the whole
# point ? clones skip the ~200 MB install). After this, a clone boots and:
#   * re-derives its device-id from its own SoC serial  -> unique
#   * regenerates machine-id + SSH host keys            -> unique
#   * re-runs onboarding (reads its own fleet-setup.toml) -> its venue Wi-Fi
#   * re-derives its aef-pi-<hash> hostname             -> unique
#   * SKIPS the installer (.firstrun-done kept)         -> no re-install
#
# Idempotent-ish; intended to be the LAST thing done to a master card.
set -u

echo "[generalize] stopping fleet services..."
systemctl stop fleet-client fleet-player fleet-keyboard fleet-local-control \
               fleet-onboard fleet-hostname 2>/dev/null

echo "[generalize] clearing per-device fleet identity + onboarding state..."
# device identity cache (re-derived from SoC serial on next boot)
rm -f /etc/fleet-client/device-id
# onboarding markers -> onboarding re-runs and reads the clone's fleet-setup.toml
rm -f /etc/fleet-client/onboard-done /etc/fleet-client/setup-applied
# KEEP /etc/fleet-client/.firstrun-done so the installer does NOT run again
touch /etc/fleet-client/.firstrun-done

echo "[generalize] resetting media playback state..."
rm -f /opt/fleet-media/state.json /opt/fleet-media/player-settings.json \
      /opt/fleet-media/osd.json /opt/fleet-media/playlist.current \
      /opt/fleet-media/.restart-player /opt/fleet-media/.onboarding-active
# drop downloaded releases + current symlink (clone re-pulls its own media)
rm -rf /opt/fleet-media/releases/* 2>/dev/null
rm -f /opt/fleet-media/current 2>/dev/null
# keep the FLEET-MEDIA partition contents? No ? it's the master's; leave the
# mountpoint but clear stray system files. (Real media is dropped per-card.)
rm -f /media/fleet-sd/.DS_Store 2>/dev/null

echo "[generalize] regenerating machine-id + SSH host keys on next boot..."
truncate -s 0 /etc/machine-id 2>/dev/null
rm -f /var/lib/dbus/machine-id /etc/ssh/ssh_host_* 2>/dev/null
# Keep SSH re-generation armed for clones.
# /etc/systemd/system/ssh.service.d/10-fleet-keys.conf and
# /etc/systemd/system/fleet-regenerate-hostkeys.service are intentionally kept —
# they are what regenerates the keys removed above on a clone's first boot.

echo "[generalize] resetting hostname (fleet-hostname re-derives per serial)..."
hostnamectl set-hostname raspberrypi 2>/dev/null
sed -i 's/127\.0\.1\.1.*/127.0.1.1\traspberrypi/' /etc/hosts 2>/dev/null

echo "[generalize] removing legacy autostart hooks (VLC media looper, etc.)..."
# Older exhibition images sometimes autostarted VLC/cvlc on login or TTY1.
# Clones should boot into the fleet stack, not a legacy looper.
rm -f /home/pi/.bash_profile /home/pi/.bash_login /home/pi/loopvideos.sh \
      /home/pi/autostart_video.sh /home/pi/start_vlc.sh 2>/dev/null
rm -rf /etc/systemd/system/getty@tty1.service.d 2>/dev/null
pkill -9 -f "vlc|cvlc" 2>/dev/null || true

echo "[generalize] clearing logs + caches..."
rm -f /var/log/fleet-*.log 2>/dev/null
journalctl --rotate 2>/dev/null; journalctl --vacuum-time=1s 2>/dev/null
rm -rf /var/lib/systemd/random-seed 2>/dev/null
history -c 2>/dev/null; rm -f /home/pi/.bash_history /root/.bash_history 2>/dev/null

echo "[generalize] removing this master's Wi-Fi + wired profiles..."
# so clones don't all try to join the master's network ? each uses its own
# fleet-setup.toml (or the captive portal)
for c in preconfigured fleet-venue fleet-cable fleet-hotspot; do
    nmcli con delete "$c" 2>/dev/null
done
rm -f /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null

sync
echo "[generalize] DONE ? powering off. Image the card, then it's your golden master."
# fire poweroff detached so this SSH session returns cleanly.
# The Wi-Fi profile deletion immediately above drops the network if you ran
# this over Wi-Fi; the detached poweroff still completes.
( sleep 2; systemctl poweroff ) &
exit 0
