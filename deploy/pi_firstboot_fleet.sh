#!/bin/bash
# First-boot hook (runs ONCE via Raspberry Pi Imager --first-run-script or the
# patched Imager firstrun.sh). Installs a retrying systemd unit for the real
# installer: if golden_image_firstrun.sh fails (typically: no network yet),
# it runs again on every boot until it succeeds — the Imager hook itself
# never re-fires, so the unit is what makes provisioning reliable.
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
ConditionPathExists=!/etc/fleet-client/.firstrun-done

[Service]
Type=oneshot
ExecStart=/bin/bash $FLEET_DIR/golden_image_firstrun.sh
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fleet-firstrun.service

echo "fleet-firstrun unit installed — installer runs after this boot completes"
