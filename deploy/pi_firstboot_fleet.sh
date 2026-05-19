#!/bin/bash
# First-boot hook for Raspberry Pi Imager (--first-run-script)
# Runs once on first boot. Calls the fleet installer that lives on the boot partition.
set -e

if [ -x /boot/firmware/fleet/golden_image_firstrun.sh ]; then
  bash /boot/firmware/fleet/golden_image_firstrun.sh || true
elif [ -x /boot/fleet/golden_image_firstrun.sh ]; then
  bash /boot/fleet/golden_image_firstrun.sh || true
fi
