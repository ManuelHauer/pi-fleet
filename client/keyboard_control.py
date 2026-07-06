#!/usr/bin/env python3
"""
Fleet Keyboard Control — a plain USB keyboard plugged into the Pi drives
playback, for venues where a technician has no phone/network handy (this is
how the previous ARS media players worked, brought back for v0.4).

Two kinds of action:
  * SETTINGS (volume / mute / rotation / slide duration) → written to
    player-settings.json via player_settings.save_settings(); fleet_player
    watches that file and applies the change LIVE via mpv IPC (no restart).
    This keeps the keyboard, the phone UI and the dashboard all in sync — the
    dashboard shows the new value on the next heartbeat.
  * TRANSPORT (next / previous / pause) → sent straight to mpv over its IPC
    socket, since these are momentary actions, not persisted settings.

Design notes:
  * Reads every keyboard-like /dev/input device (there may be several: a combo
    keyboard, a media remote, a presenter clicker). Re-scans every few seconds
    so a keyboard hot-plugged mid-festival just starts working.
  * Never grabs the devices exclusively — harmless keypresses also reach the
    (login-less) console; grabbing risks locking out a real console session.
  * Depends on python3-evdev (installed by the golden image). If evdev is
    missing it logs once and exits cleanly so systemd doesn't crash-loop.

Key map (printed to the log at startup, and documented in the tech handbook):
  Vol +   volume up / '+' / '='                 Rotate  'r' (cycles 0→90→180→270)
  Vol -   volume down / '-'                      Slower  '[' (slide +2s)
  Mute    mute key / 'm'                         Faster  ']' (slide -2s)
  Next    → / 'n' / next-track                   Pause   space / play-pause
  Prev    ← / 'p' / prev-track
"""
import logging
import select
import socket
import time
from pathlib import Path

from player_settings import load_settings, save_settings

MPV_IPC_SOCKET = "/tmp/fleet-mpv-ipc"
RESCAN_INTERVAL = 3.0          # seconds — pick up hot-plugged keyboards
VOL_STEP = 5
DURATION_STEP = 2
ROTATIONS = (0, 90, 180, 270)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("fleet-keyboard")


def _mpv(cmd: list) -> None:
    """Fire-and-forget mpv IPC command (transport actions)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(MPV_IPC_SOCKET)
        import json
        s.sendall((json.dumps({"command": cmd}) + "\n").encode())
        s.close()
    except Exception as e:
        log.debug(f"mpv IPC failed ({cmd}): {e}")


# ── Actions ──

def _adjust_volume(delta: int):
    s = load_settings()
    new = max(0, min(200, s["volume_pct"] + delta))
    save_settings({"volume_pct": new, "muted": False}, updated_by="keyboard")
    log.info(f"volume → {new}%")


def _toggle_mute():
    s = load_settings()
    save_settings({"muted": not s["muted"]}, updated_by="keyboard")
    log.info(f"mute → {not s['muted']}")


def _cycle_rotation():
    s = load_settings()
    try:
        nxt = ROTATIONS[(ROTATIONS.index(s["rotation"]) + 1) % len(ROTATIONS)]
    except ValueError:
        nxt = 0
    save_settings({"rotation": nxt}, updated_by="keyboard")
    log.info(f"rotation → {nxt}°")


def _adjust_duration(delta: int):
    s = load_settings()
    new = max(1, min(3600, s["image_duration_s"] + delta))
    save_settings({"image_duration_s": new}, updated_by="keyboard")
    log.info(f"slide duration → {new}s")


def _build_keymap(ec):
    """Map evdev key codes → action callables. Built at runtime so we only
    reference ecodes that exist on this kernel."""
    def code(name):
        return getattr(ec, name, None)

    table = {
        # volume
        "KEY_VOLUMEUP": lambda: _adjust_volume(VOL_STEP),
        "KEY_KPPLUS": lambda: _adjust_volume(VOL_STEP),
        "KEY_EQUAL": lambda: _adjust_volume(VOL_STEP),
        "KEY_VOLUMEDOWN": lambda: _adjust_volume(-VOL_STEP),
        "KEY_KPMINUS": lambda: _adjust_volume(-VOL_STEP),
        "KEY_MINUS": lambda: _adjust_volume(-VOL_STEP),
        "KEY_MUTE": _toggle_mute,
        "KEY_M": _toggle_mute,
        # rotation
        "KEY_R": _cycle_rotation,
        # slide duration
        "KEY_LEFTBRACE": lambda: _adjust_duration(DURATION_STEP),   # '[' slower
        "KEY_RIGHTBRACE": lambda: _adjust_duration(-DURATION_STEP),  # ']' faster
        # transport
        "KEY_RIGHT": lambda: _mpv(["playlist-next"]),
        "KEY_N": lambda: _mpv(["playlist-next"]),
        "KEY_NEXTSONG": lambda: _mpv(["playlist-next"]),
        "KEY_LEFT": lambda: _mpv(["playlist-prev"]),
        "KEY_P": lambda: _mpv(["playlist-prev"]),
        "KEY_PREVIOUSSONG": lambda: _mpv(["playlist-prev"]),
        "KEY_SPACE": lambda: _mpv(["cycle", "pause"]),
        "KEY_PLAYPAUSE": lambda: _mpv(["cycle", "pause"]),
    }
    # allow key auto-repeat (value==2) only for volume/duration
    repeatable = {code(n) for n in ("KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_KPPLUS",
                                    "KEY_KPMINUS", "KEY_EQUAL", "KEY_MINUS",
                                    "KEY_LEFTBRACE", "KEY_RIGHTBRACE")}
    keymap = {code(n): fn for n, fn in table.items() if code(n) is not None}
    return keymap, {c for c in repeatable if c is not None}


def _is_keyboard(dev, ec) -> bool:
    """A device that reports normal typing keys (skip mice, touchpads)."""
    try:
        keys = dev.capabilities().get(ec.EV_KEY, [])
        return ec.KEY_ENTER in keys and ec.KEY_A in keys
    except Exception:
        return False


def main():
    try:
        from evdev import InputDevice, ecodes as ec, list_devices
    except ImportError:
        log.error("python3-evdev not installed — keyboard control disabled. "
                  "Install with: apt install python3-evdev")
        return

    keymap, repeatable = _build_keymap(ec)
    log.info("Fleet keyboard control started. Keys: vol ±/mute, r=rotate, "
             "[ ]=slide slower/faster, ←/→=prev/next, space=pause.")

    devices = {}   # path -> InputDevice
    last_scan = 0.0

    def rescan():
        nonlocal devices
        want = {}
        for path in list_devices():
            if path in devices:
                want[path] = devices[path]
                continue
            try:
                d = InputDevice(path)
                if _is_keyboard(d, ec):
                    want[path] = d
                    log.info(f"keyboard attached: {path} ({d.name})")
                else:
                    d.close()
            except Exception:
                pass
        # drop vanished devices
        for path, d in devices.items():
            if path not in want:
                log.info(f"keyboard removed: {path}")
                try:
                    d.close()
                except Exception:
                    pass
        devices = want

    while True:
        now = time.monotonic()
        if now - last_scan >= RESCAN_INTERVAL:
            rescan()
            last_scan = now

        if not devices:
            time.sleep(1.0)
            continue

        try:
            r, _, _ = select.select(list(devices.values()), [], [], RESCAN_INTERVAL)
        except Exception:
            # a device disappeared mid-select; force a rescan
            last_scan = 0.0
            continue

        for dev in r:
            try:
                for event in dev.read():
                    if event.type != ec.EV_KEY:
                        continue
                    # value 1 = keydown, 2 = auto-repeat (only some actions)
                    if event.value == 1 or (event.value == 2 and event.code in repeatable):
                        fn = keymap.get(event.code)
                        if fn:
                            try:
                                fn()
                            except Exception as e:
                                log.warning(f"action error: {e}")
            except OSError:
                # device unplugged — will be pruned on next rescan
                last_scan = 0.0


if __name__ == "__main__":
    main()
