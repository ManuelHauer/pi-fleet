# SSH service fails to start on first boot

## Symptom
`ssh` service fails during boot; connecting to the Pi gives `Connection refused`.

## Why it happens
The fleet image deletes SSH host keys during golden-image generalization
(`deploy/golden/generalize.sh`). They are supposed to be recreated on first boot,
but if the `pi` user is missing or the filesystem races with `machine-id` setup,
`sshd` can refuse to start. In headless Bookworm/Trixie images this is compounded
by the fact that no user is created unless `userconf.txt` is present or
`FLEET_PI_PASSWORD` was set during `prepare_sd_card.sh`.

## Quick fix
From a shell on the Pi:

```bash
sudo rm -f /etc/ssh/ssh_host_*
sudo ssh-keygen -A
sudo systemctl enable ssh
sudo systemctl restart ssh
sudo systemctl status ssh
```

If `id pi` fails, create the user first:

```bash
sudo useradd -m -G sudo,adm,users,audio,video,render,gpio,i2c,spi,input -s /bin/bash pi
sudo passwd pi
```

## Prevention
`prepare_sd_card.sh` should either require `FLEET_PI_PASSWORD` or generate one
when no `userconf.txt` exists.

The root cause is fixed by `fleet-regenerate-hostkeys.service`
(`deploy/fleet-regenerate-hostkeys.service`), a self-disabling oneshot unit that
`golden_image_firstrun.sh` installs and enables on the master. It survives
generalization inside the golden image, and on a clone's first boot — while the
host keys are still missing (`ConditionPathExistsGlob=!/etc/ssh/ssh_host_*key`) —
it regenerates `/etc/machine-id` and the SSH host keys before `ssh.service` /
`ssh.socket` start. Once keys exist, the unit is a no-op.

The older systemd override (`/etc/systemd/system/ssh.service.d/10-fleet-keys.conf`,
runs `ssh-keygen -A` before `sshd`) is kept as a belt-and-suspenders fallback,
mainly for images generalized before the service existed.

## Verification (on a clone booted from the golden image)
```bash
systemctl is-enabled fleet-regenerate-hostkeys.service
ls /etc/ssh/ssh_host_*
systemctl status ssh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```
Then connect from another machine: `ssh pi@<clone-ip-or-hostname>`. Each clone
must show host keys that differ from the master's.
