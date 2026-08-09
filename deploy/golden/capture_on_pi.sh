#!/bin/bash
# Capture a golden image directly ON THE PI.
#
# Usage:
#   ./capture_on_pi.sh /dev/sdX        # write raw image to USB stick / drive
#   ./capture_on_pi.sh /mnt/usb        # write .img.gz to a mounted USB stick
#   ./capture_on_pi.sh                 # stream gzip image over stdout (pipe to PC)
#
# What it does:
#   1. Stops fleet services, generalizes the running master.
#   2. Restores cross-board boot firmware (Trixie raspi-firmware bug guard).
#   3. Trims/zeros free space so the .gz compresses small.
#   4. Captures /dev/mmcblk0 UP TO THE END OF THE LAST PARTITION (not the whole
#      card), so an image built on a large card still flashes onto smaller ones.
set -euo pipefail

OUT=""
if [ $# -ge 1 ]; then
  OUT="$1"
fi

NOW=$(date +%Y%m%d-%H%M%S)

# ── 1. Generalize master (same as generalize.sh, but no poweroff) ──
echo "[capture] generalizing master..."
sudo systemctl stop fleet-client fleet-player fleet-keyboard fleet-local-control \
                       fleet-onboard fleet-hostname 2>/dev/null || true

sudo rm -f /etc/fleet-client/device-id
sudo rm -f /etc/fleet-client/onboard-done /etc/fleet-client/setup-applied
sudo touch /etc/fleet-client/.firstrun-done

for c in preconfigured fleet-venue fleet-cable fleet-hotspot; do
  sudo nmcli con delete "$c" 2>/dev/null || true
done
sudo rm -f /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null || true

sudo rm -f /opt/fleet-media/state.json /opt/fleet-media/player-settings.json \
           /opt/fleet-media/osd.json /opt/fleet-media/playlist.current \
           /opt/fleet-media/.restart-player /opt/fleet-media/.onboarding-active
sudo rm -rf /opt/fleet-media/releases/* 2>/dev/null || true
sudo rm -f /opt/fleet-media/current 2>/dev/null || true
rm -f /media/fleet-sd/.DS_Store 2>/dev/null || true

sudo truncate -s 0 /etc/machine-id 2>/dev/null || true
sudo rm -f /var/lib/dbus/machine-id /etc/ssh/ssh_host_* 2>/dev/null || true
sudo hostnamectl set-hostname raspberrypi 2>/dev/null || true
sudo sed -i 's/127\.0\.1\.1.*/127.0.1.1\traspberrypi/' /etc/hosts 2>/dev/null || true

sudo rm -f /var/log/fleet-*.log 2>/dev/null || true
sudo journalctl --rotate 2>/dev/null || true
sudo journalctl --vacuum-time=1s 2>/dev/null || true
sudo rm -rf /var/lib/systemd/random-seed 2>/dev/null || true
sudo rm -f /home/pi/.bash_history /root/.bash_history 2>/dev/null || true
history -c 2>/dev/null || true

# Safety check: clones must be able to regenerate the SSH host keys we just
# removed, otherwise sshd stays dead on their first boot (see SPECS-ssh-host-
# key-regeneration.md and docs/bugs/ssh_service_failed_to_start.md).
if [ ! -f /etc/systemd/system/fleet-regenerate-hostkeys.service ] && \
   [ ! -f /etc/systemd/system/ssh.service.d/10-fleet-keys.conf ]; then
  echo "WARNING: SSH key regeneration is not armed; clones may fail to start sshd." >&2
  echo "         Re-run the updated golden_image_firstrun.sh on the master first." >&2
fi

# Restore cross-board boot firmware that Debian Trixie/raspi-firmware may have
# removed during the first-boot package upgrade (GitHub raspberrypi/firmware#2034).
# The master only needs its own board's files to run, but clones need files
# matching their own Pi model. Package copies survive in /usr/lib even when
# /boot/firmware has been pruned.
echo "[capture] restoring cross-board boot firmware to /boot/firmware..."
RESTORED=0
for src_dir in /usr/lib/raspberrypi-firmware /usr/lib/linux-image-*-rpi-*; do
  if [ -d "$src_dir" ]; then
    for f in kernel8.img kernel_2712.img initramfs8 initramfs_2712 \
             start.elf start4.elf start_cd.elf start4cd.elf \
             fixup.dat fixup4.dat; do
      if [ -f "$src_dir/$f" ] && [ ! -f "/boot/firmware/$f" ]; then
        sudo cp "$src_dir/$f" /boot/firmware/ && echo "  restored $f from $src_dir" && RESTORED=1
      fi
    done
  fi
done
# Make sure the main files we need on clones actually exist.
missing=""
for required in kernel8.img start.elf start4.elf; do
  [ -f "/boot/firmware/$required" ] || missing="$missing $required"
done
if [ -n "$missing" ]; then
  echo "ERROR: essential boot files missing in /boot/firmware before capture:$missing" >&2
  echo "       Fix by re-provisioning with a stock image and capturing before the" >&2
  echo "       raspi-firmware bug prunes files." >&2
  exit 1
fi
[ "$RESTORED" = "1" ] && sync

# ── 2. Free-space hygiene (makes empty area compress to almost nothing) ──
# Use fstrim on the ext4 rootfs (avoids the dd-to-disk that corrupted a previous
# image's superblock). Zero the exFAT FLEET-MEDIA partition (safe there).
echo "[capture] trimming rootfs free space..."
sudo fstrim -v / 2>/dev/null || true

if [ -d /media/fleet-sd ]; then
  echo "[capture] zeroing free space on FLEET-MEDIA..."
  sudo mount -a 2>/dev/null || true
  if mountpoint -q /media/fleet-sd; then
    sudo dd if=/dev/zero of=/media/fleet-sd/.zero bs=1M status=progress || true
    sudo rm -f /media/fleet-sd/.zero
    sync
  fi
fi

# ── 3. Capture — only up to the end of the LAST partition ──
# Capturing the whole card would bake in this card's 59.5G geometry. We stop at
# the last partition's end so the image flashes onto smaller (e.g. 16GB) cards.
DISK=/dev/mmcblk0
LAST_END=$(sudo sfdisk -d "$DISK" | awk -F'[=,]' '/^\/dev\//{e=$2+$4; if(e>m)m=e} END{print m}')
COUNT_MIB=$(( (LAST_END * 512 + 1048575) / 1048576 ))
echo "[capture] capturing $LAST_END sectors (~$((COUNT_MIB/1024)).$(( (COUNT_MIB%1024)*100/1024 )) GiB of card, up to end of last partition)"

run_capture() {
  # dd bs=1M count=COUNT_MIB stops exactly at the last partition end.
  sudo dd if="$DISK" bs=1M count="$COUNT_MIB" status=progress
}

if [ -z "$OUT" ]; then
  echo "[capture] streaming gzip image to stdout..." >&2
  run_capture | gzip -1 -
  exit 0
fi

# Determine if OUT is a block device or a directory/file path
if [ -b "$OUT" ]; then
  echo "[capture] writing raw image to block device $OUT"
  sudo dd if="$DISK" of="$OUT" bs=1M count="$COUNT_MIB" status=progress conv=fsync
  echo "[capture] done: $OUT"
  exit 0
fi

# OUT is a directory or file path
OUT_FILE="$OUT"
if [ -d "$OUT" ]; then
  mkdir -p "$OUT"
  OUT_FILE="$OUT/aef-golden-${NOW}.img.gz"
fi

echo "[capture] writing gzip image to $OUT_FILE"
run_capture | gzip -1 - > "$OUT_FILE"
echo "[capture] done: $OUT_FILE"
ls -lh "$OUT_FILE" || true
