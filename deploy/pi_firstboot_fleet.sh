#!/bin/bash
# First-boot hook (runs ONCE via Raspberry Pi Imager --first-run-script or the
# self-contained firstrun.sh that prepare_sd_card.sh generates). Installs a
# retrying systemd unit for the real installer.
#
# The installer's #1 failure is "no network yet" (booted before the venue/HQ
# uplink was ready). A plain oneshot only re-runs on a REBOOT, so a Pi that
# gets network 2 minutes after boot would sit failed until someone power-cycles
# it (hardware-test finding — the Pi 3). Restart=on-failure makes it retry
# every RestartSec until the network appears and the install succeeds.
set -e

FLEET_DIR=""
if [ -d /boot/firmware/fleet ]; then FLEET_DIR=/boot/firmware/fleet
elif [ -d /boot/fleet ]; then FLEET_DIR=/boot/fleet
fi
[ -z "$FLEET_DIR" ] && { echo "fleet dir missing on boot partition"; exit 0; }

cat > /etc/systemd/system/fleet-firstrun.service <<EOF
[Unit]
Description=Ars Fleet golden-image installer (retries until success)
After=network.target
Wants=network-online.target
ConditionPathExists=!/etc/fleet-client/.firstrun-done
# Never give up retrying (no start-rate limit)
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=/bin/bash $FLEET_DIR/golden_image_firstrun.sh
# Self-heal: if the installer exits non-zero (typically no network yet),
# retry after 30s instead of waiting for a manual reboot.
Restart=on-failure
RestartSec=30
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fleet-firstrun.service

echo "fleet-firstrun unit installed — installer runs after this boot completes"
