# Spec: Fix SSH Host Key Regeneration for Fleet Golden Images

## Overview

Cloned fleet Pis fail to start `ssh.service` because their SSH host keys were removed during golden-image generalization and not regenerated before sshd starts. This document specifies the minimal changes needed so every clone generates unique host keys on its first boot and SSH is reachable regardless of whether the first-run installer is skipped.

**Background**
- `deploy/golden/generalize.sh` and `deploy/golden/capture_on_pi.sh` delete `/etc/ssh/ssh_host_*` and truncate `/etc/machine-id`.
- `deploy/golden_image_firstrun.sh` installs a systemd drop-in (`/etc/systemd/system/ssh.service.d/10-fleet-keys.conf`) that runs `ssh-keygen -A` before sshd.
- Because clones inherit `.firstrun-done` from the master, the first-run installer is skipped, so any re-arming performed inside it will not run again. The current drop-in is fragile and has failed in production (`docs/bugs/ssh_service_failed_to_start.md`).

## In scope

1. Add a dedicated, self-disabling systemd unit that regenerates SSH host keys + machine-id early on first boot.
2. Install and enable that unit during the fleet first-run install.
3. Ensure the unit (and its enablement state) survives generalization and is present in the golden image.
4. Keep the existing `ssh.service.d` drop-in as a fallback.
5. Update `docs/bugs/ssh_service_failed_to_start.md` with the new fix once verified.

## Out of scope

- Changing how fleet client/server registration works.
- Modifying the image-capture mechanism (`dd` / gzip streaming).
- Adding Tailscale/Headscale integration.
- Removing or replacing the existing `pi` user creation flow.

## Design decisions

| Decision | Rationale |
|----------|-----------|
| Use a dedicated systemd service, not just a drop-in | Standard Debian/Raspberry Pi OS pattern; survives when `.firstrun-done` is present; self-disabling via `ConditionPathExistsGlob`. |
| Run after `time-set.target` and before `ssh.service` | Ensures machine-id and key generation happen before sshd attempts to start. |
| Regenerate `/etc/machine-id` in the same service | Host keys and machine-id are logically tied; this mirrors cloud-init’s behavior and avoids dbus/journald races. |
| Use `ConditionPathExistsGlob=!/etc/ssh/ssh_host_*key` | Service runs only while keys are missing; after first boot it is effectively no-op and remains harmless. |
| Keep the existing `ssh.service.d` drop-in | Belt-and-suspenders fallback; costs nothing and protects against edge cases where the new service is not enabled. |
| Place the unit file in `deploy/fleet-regenerate-hostkeys.service` and copy it during first boot | Allows the installer to install it exactly once while the boot partition is still accessible; no need to generate it at flash time on the host. |

## Component-level changes

### 1. New unit file

Create `deploy/fleet-regenerate-hostkeys.service`:

```ini
[Unit]
Description=Regenerate SSH host keys and machine-id on first boot
After=systemd-remount-fs.service time-set.target
Before=ssh.service
ConditionPathExistsGlob=!/etc/ssh/ssh_host_*key

[Service]
Type=oneshot
ExecStartPre=-/bin/rm -f /etc/ssh/ssh_host_* /var/lib/dbus/machine-id /etc/machine-id
ExecStart=-/usr/bin/systemd-machine-id-setup
ExecStart=-/usr/bin/ssh-keygen -A
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Notes:
- `ConditionPathExistsGlob=!/etc/ssh/ssh_host_*key` evaluates to false once an `ssh_host_*key` file exists, so the service does not run on subsequent boots.
- `ExecStartPre` with `-` ignores failures.
- `systemd-machine-id-setup` regenerates `/etc/machine-id` when it is empty, which is needed for dbus/journald after generalization.

### 2. `deploy/golden_image_firstrun.sh`

Add a new step **after** the existing `ssh.service.d` drop-in block (around line 228):

```bash
# Ensure cloned images regenerate SSH host keys even when first-run is skipped.
FLEET_REGEN_KEYS_UNIT=/etc/systemd/system/fleet-regenerate-hostkeys.service
cp "$FLEET_DIR/deploy/fleet-regenerate-hostkeys.service" "$FLEET_REGEN_KEYS_UNIT"
chmod 644 "$FLEET_REGEN_KEYS_UNIT"
systemctl daemon-reload
systemctl enable fleet-regenerate-hostkeys.service
```

Also update the existing `ssh.service.d` block comment to reference the new service, e.g.:

```bash
# Ensure SSH host keys exist on every boot. They are removed during
# golden-image generalization; some Bookworm/Trixie paths fail to recreate
# them automatically, which leaves sshd dead on first boot of a clone.
# The primary fix is fleet-regenerate-hostkeys.service; this drop-in is a
# fallback for images that were already generalized before that service
# existed.
```

### 3. `deploy/golden/capture_on_pi.sh`

No change is required to the generalization block if the new service is already installed on the master. The service, its enable symlink under `/etc/systemd/system/multi-user.target.wants/`, and the drop-in will all be captured in the `.img.gz`.

If desired, add a safety check before zeroing/capture that warns when neither the service nor the drop-in is present:

```bash
if [ ! -f /etc/systemd/system/fleet-regenerate-hostkeys.service ] && \
   [ ! -f /etc/systemd/system/ssh.service.d/10-fleet-keys.conf ]; then
  echo "WARNING: SSH key regeneration is not armed; clones may fail to start sshd." >&2
  # In an interactive/development build you may want to abort here.
fi
```

This is optional because the fix lives in `golden_image_firstrun.sh`, not in capture.

### 4. `deploy/golden/generalize.sh`

`generalize.sh` must **not** delete the new service or its enablement symlink. It already only removes `/etc/ssh/ssh_host_*`, `/var/lib/dbus/machine-id`, `/etc/machine-id`, logs, and fleet state. No destructive changes are needed.

Optionally, make `generalize.sh` more explicit:

```bash
# Keep SSH re-generation armed for clones.
# /etc/systemd/system/ssh.service.d/10-fleet-keys.conf and
# /etc/systemd/system/fleet-regenerate-hostkeys.service are intentionally kept.
```

### 5. `docs/bugs/ssh_service_failed_to_start.md`

After the fix is deployed and tested, update the file to:
1. Mention that the root cause is fixed by `fleet-regenerate-hostkeys.service`.
2. Keep the manual quick fix as a fallback.
3. Add a verification step:

```bash
systemctl is-enabled fleet-regenerate-hostkeys.service
ls /etc/ssh/ssh_host_*
```

## Data/model/API changes

No API or server changes. The new systemd unit file and installer changes affect only the SD-card provisioning path.

## Risks, tradeoffs, and open questions

| Risk | Mitigation |
|------|------------|
| New service not installed before capture | Master must be booted at least once with the updated `golden_image_firstrun.sh` before capture. Document this. |
| `ConditionPathExistsGlob` does not match in older systemd versions | Systemd has supported `ConditionPathExistsGlob` since v198 (2013); Bookworm/Trixie are fine. |
| Concurrent execution with `ssh.socket` instead of `ssh.service` (Trixie default in some configurations) | Add `Before=ssh.socket` as well as `Before=ssh.service`. |
| `systemd-machine-id-setup` fails because `/etc/machine-id` is read-only early in boot | Precede it with `After=systemd-remount-fs.service`. |
| Performance impact | The service runs once and exits; negligible overhead. |

**Open questions**
1. Should the new service also be installed by `prepare_sd_card.sh` directly onto the boot partition, so first boot has it even if `golden_image_firstrun.sh` is interrupted?
   - *Recommendation:* No. The current architecture already depends on the first-run installer succeeding; duplicating logic adds maintenance. Add a one-time health check instead.
2. Should the `ssh.service.d` drop-in be removed after the service is proven?
   - *Recommendation:* Keep it as a fallback. It is low-risk and helps older images without the service.

## Verification

1. Flash a fresh SD with the updated repo and boot the master.
2. After first-run completes and the master reboots, run:
   ```bash
   systemctl is-enabled fleet-regenerate-hostkeys.service
   systemctl status fleet-regenerate-hostkeys.service
   ls -l /etc/ssh/ssh_host_*
   ```
3. Capture the master to a golden image using `capture_on_pi.sh` or `capture_golden.sh`.
4. Flash the golden image to a second SD and boot the clone.
5. On the clone, verify:
   ```bash
   systemctl status ssh
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```
6. From another machine, SSH into the clone and confirm it accepts the connection.

## Acceptance criteria

- [x] `deploy/fleet-regenerate-hostkeys.service` exists.
- [x] `deploy/golden_image_firstrun.sh` installs and enables the service.
- [x] `generalize.sh` does not remove the service or its enablement symlink.
- [ ] A clone booted from the captured golden image starts `ssh.service` successfully and accepts SSH connections.
- [x] `docs/bugs/ssh_service_failed_to_start.md` is updated with the new verification steps.
