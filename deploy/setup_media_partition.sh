#!/bin/bash
# Create the FLEET-MEDIA partition on the Pi's own SD card. Runs ON THE PI
# (from golden_image_firstrun.sh) — macOS/Windows have no ext4 tools, so all
# partition surgery happens here at first boot.
#
# Layout after this script (example 32 GB card):
#   p1  bootfs        512 MB  FAT32   (visible on any laptop)
#   p2  rootfs       ~8  GB   ext4    (OS + /opt/fleet-media releases)
#   p3  FLEET-MEDIA  ~23 GB   exFAT   (visible on any laptop — drop media here)
#
# Requires: the Pi OS auto-expand hook must be REMOVED from cmdline.txt before
# first boot (prepare_sd_card.sh does that), otherwise rootfs already fills
# the card and there is no room left.
#
# Idempotent: exits 0 if p3 already exists. If the card is too small for a
# useful media partition (< ROOTFS_GB + 4 GB), rootfs gets the whole card
# instead — the Pi then simply has no SD media feature (USB still works).
#
# Config: ROOTFS_GB env or "rootfs_gb" in fleet-boot-config.json (default 8).

set -euo pipefail

log() { echo "[media-partition] $*"; }

DISK="/dev/mmcblk0"
P2="${DISK}p2"
P3="${DISK}p3"
MOUNTPOINT="/media/fleet-sd"
LABEL="FLEET-MEDIA"
ROOTFS_GB="${ROOTFS_GB:-8}"

# Root must actually live on the SD card (not NVMe/USB boot)
ROOT_SRC=$(findmnt -n -o SOURCE / || true)
if [ "$ROOT_SRC" != "$P2" ]; then
  log "root is on $ROOT_SRC (not $P2) — skipping media partition setup"
  exit 0
fi

ensure_fstab_and_mount() {
  mkdir -p "$MOUNTPOINT"
  if ! grep -q "LABEL=$LABEL" /etc/fstab; then
    # uid/gid 1000 = user 'pi': fleet daemons read it, laptops write it
    echo "LABEL=$LABEL  $MOUNTPOINT  exfat  defaults,nofail,uid=1000,gid=1000,fmask=0022,dmask=0022  0  0" >> /etc/fstab
    log "fstab entry added"
  fi
  systemctl daemon-reload 2>/dev/null || true
  mount -a 2>/dev/null || true
}

# Already done?
if [ -b "$P3" ]; then
  log "$P3 already exists — ensuring it is formatted and mounted"
  if ! blkid -o value -s TYPE "$P3" >/dev/null 2>&1; then
    log "formatting existing $P3 as exFAT ($LABEL)"
    mkfs.exfat -L "$LABEL" "$P3"
  fi
  ensure_fstab_and_mount
  # If the partition was resized but the filesystem never expanded (e.g. image
  # already had p3), grow it now. resize2fs is idempotent if already full.
  resize2fs "$P2" >/dev/null 2>&1 || true
  exit 0
fi

SECTOR=512
TOTAL_SECTORS=$(blockdev --getsz "$DISK")
# sfdisk -d prints: "/dev/mmcblk0p2 : start=  1056768, size= 59768832, type=83"
P2_START=$(sfdisk -d "$DISK" | grep "^$P2" | sed -E 's/.*start=[[:space:]]*([0-9]+).*/\1/')
if ! [[ "${P2_START:-}" =~ ^[0-9]+$ ]]; then
  log "ERROR: cannot determine start of $P2 — aborting (no changes made)"
  exit 1
fi

ROOTFS_SECTORS=$(( ROOTFS_GB * 1024 * 1024 * 1024 / SECTOR ))
P2_NEW_END=$(( P2_START + ROOTFS_SECTORS - 1 ))
# Align p3 to the next 1 MiB boundary (2048 sectors)
P3_START=$(( ( (P2_NEW_END / 2048) + 1 ) * 2048 ))
MIN_MEDIA_SECTORS=$(( 4 * 1024 * 1024 * 1024 / SECTOR ))  # 4 GB

if [ $(( TOTAL_SECTORS - P3_START )) -lt "$MIN_MEDIA_SECTORS" ]; then
  log "card too small for a ${ROOTFS_GB}G rootfs + >=4G media partition"
  log "→ expanding rootfs to the whole card instead (no FLEET-MEDIA)"
  echo ", +" | sfdisk --force --no-reread -N 2 "$DISK"
  partx -u "$DISK" 2>/dev/null || partprobe "$DISK" || true
  resize2fs "$P2"
  exit 0
fi

log "resizing rootfs to ${ROOTFS_GB}G and creating $LABEL partition"
log "  p2: start=$P2_START sectors=$ROOTFS_SECTORS"
log "  p3: start=$P3_START (to end of card)"

# Rewrite the partition table: p2 gets a fixed size, p3 takes the rest.
# type=7 (exFAT/NTFS) so laptops recognize it.
sfdisk -d "$DISK" > /tmp/fleet-pt-backup.dump
log "partition table backup: /tmp/fleet-pt-backup.dump"

python3 - "$DISK" "$P2" "$ROOTFS_SECTORS" "$P3_START" <<'PY' > /tmp/fleet-pt-new.dump
import subprocess, sys
disk, p2, p2_size, p3_start = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
dump = subprocess.check_output(["sfdisk", "-d", disk], text=True)
out = []
for line in dump.splitlines():
    if line.startswith(p2):
        # keep start=, force size=
        import re
        line = re.sub(r"size=\s*\d+", f"size={p2_size}", line)
    if line.startswith("last-lba"):
        continue  # let sfdisk recompute
    out.append(line)
out.append(f"{disk}p3 : start={p3_start}, type=7")
print("\n".join(out))
PY

sfdisk --force --no-reread "$DISK" < /tmp/fleet-pt-new.dump
partx -u "$DISK" 2>/dev/null || partprobe "$DISK" || true
sleep 2

if [ ! -b "$P3" ]; then
  log "ERROR: $P3 did not appear after repartition — restoring backup table"
  sfdisk --force --no-reread "$DISK" < /tmp/fleet-pt-backup.dump || true
  partx -u "$DISK" 2>/dev/null || true
  exit 1
fi

if resize2fs "$P2"; then
  log "rootfs resized"
else
  log "WARNING: rootfs resize failed — partition table may need a reboot to be reloaded"
  log "Rebooting and will retry on next boot"
  sync
  reboot
  exit 0
fi

# exfatprogs may not be installed yet (this script runs BEFORE the main apt
# block, so growing rootfs frees the space apt needs). Now that rootfs has
# room, pull in mkfs.exfat on demand.
if ! command -v mkfs.exfat >/dev/null 2>&1; then
  log "installing exfatprogs (for FLEET-MEDIA format)…"
  DEBIAN_FRONTEND=noninteractive apt-get install -y exfatprogs >/dev/null 2>&1 \
    || { log "ERROR: could not install exfatprogs — leaving p3 unformatted"; exit 1; }
fi
mkfs.exfat -L "$LABEL" "$P3"
log "$P3 formatted as exFAT ($LABEL)"

ensure_fstab_and_mount

# A README so anyone opening the volume on a laptop knows what to do
if mountpoint -q "$MOUNTPOINT"; then
  cat > "$MOUNTPOINT/DROP-MEDIA-HERE.txt" <<'EOF'
FLEET-MEDIA — Ars Festival media player

Put video/image files directly into this folder (not in subfolders):
  videos:  .mp4 .mkv .mov .webm    images:  .jpg .png

On the next boot the player switches to these files automatically
(the device is then "pinned" — the dashboard shows it and can release it).

Optional: a fleet-setup.toml here pre-configures venue Wi-Fi and playback
settings — see the technician handbook.
EOF
  log "README written to $MOUNTPOINT"
fi

log "done: $(df -h "$MOUNTPOINT" | tail -1)"
