#!/usr/bin/env python3
"""
On-screen onboarding status — rendered THROUGH the fleet player.

v0.2 wrote text to /dev/tty1, but fleet-player's mpv owns the display via
DRM from boot, so the technician never saw the AP credentials. v0.3 renders
each onboarding state as a PNG and hands it to the player:

  /opt/fleet-media/.onboarding-active   flag — while present (and no playlist)
                                        fleet_player shows setup.png, not idle.png
  /opt/fleet-media/system/setup.png     the rendered status card
  /opt/fleet-media/.restart-player      touched after each render

Falls back to tty1 text if PIL is unavailable (lab images).
"""
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("hdmi-status")

MEDIA_BASE = Path("/opt/fleet-media")
SYSTEM_DIR = MEDIA_BASE / "system"
SETUP_IMAGE = SYSTEM_DIR / "setup.png"
ONBOARD_FLAG = MEDIA_BASE / ".onboarding-active"
RESTART_TRIGGER = MEDIA_BASE / ".restart-player"

W, H = 1920, 1080
BG = (10, 10, 15)
INK = (224, 224, 232)
DIM = (136, 136, 160)
ACCENT = (124, 108, 240)
OK = (61, 220, 132)
WARN = (255, 176, 32)
BAD = (255, 107, 107)


def _fonts():
    from PIL import ImageFont
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    path = next((p for p in paths if Path(p).exists()), None)

    def font(size):
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return font


def _center(draw, text, font, y, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, y), text, fill=fill, font=font)


def _render(title, title_color, lines, footer=""):
    """lines: list of (label, value) tuples or plain strings."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _tty_fallback(title, lines, footer)
        return

    font = _fonts()
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    _center(draw, "Ars Festival Media Player — Setup", font(40), 90, DIM)
    _center(draw, title, font(78), 240, title_color)

    y = 440
    for item in lines:
        if isinstance(item, tuple):
            label, value = item
            f_l, f_v = font(34), font(48)
            lb = draw.textbbox((0, 0), label, font=f_l)
            draw.text((W // 2 - 40 - (lb[2] - lb[0]), y + 10), label, fill=DIM, font=f_l)
            draw.text((W // 2 + 20, y), value, fill=INK, font=f_v)
            y += 96
        else:
            _center(draw, item, font(32), y, DIM)
            y += 62

    if footer:
        _center(draw, footer, font(26), H - 90, DIM)

    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETUP_IMAGE.with_suffix(".png.tmp")
    img.save(tmp)
    tmp.rename(SETUP_IMAGE)


def _tty_fallback(title, lines, footer):
    rows = ["", f"  == ARS FLEET SETUP: {title} =="]
    for item in lines:
        rows.append(f"  {item[0]}: {item[1]}" if isinstance(item, tuple) else f"  {item}")
    if footer:
        rows += ["", f"  {footer}"]
    try:
        text = "\\n".join(rows)
        subprocess.run(["sudo", "bash", "-c",
                        f'echo -e "\\033[2J\\033[H{text}" > /dev/tty1'],
                       capture_output=True, timeout=5)
    except Exception as e:
        log.warning(f"tty fallback failed: {e}")


def _signal_player():
    try:
        ONBOARD_FLAG.parent.mkdir(parents=True, exist_ok=True)
        ONBOARD_FLAG.write_text("1")
        RESTART_TRIGGER.touch()
    except Exception as e:
        log.warning(f"Player signal failed: {e}")


def clear():
    """Onboarding over — hand the screen back to normal player behavior."""
    try:
        if ONBOARD_FLAG.exists():
            ONBOARD_FLAG.unlink()
        RESTART_TRIGGER.touch()
    except Exception as e:
        log.warning(f"Onboard flag clear failed: {e}")


def show_setup_screen(ap_name: str, ap_password: str, portal_url: str = "http://10.42.0.1"):
    _render("SETUP MODE", ACCENT, [
        ("Wi-Fi network", ap_name),
        ("Password", ap_password),
        ("Setup page", portal_url),
        "Connect with a phone — the setup page opens automatically.",
    ], footer="This screen disappears once the device is configured.")
    _signal_player()
    log.info(f"Setup screen displayed: AP={ap_name}")


def show_connecting(ssid: str):
    _render("CONNECTING…", WARN, [
        ("Network", ssid),
        "Joining the venue Wi-Fi. This can take up to a minute.",
    ])
    _signal_player()


def show_connected(ssid: str, ip: str, device_id: str):
    _render("CONNECTED", OK, [
        ("Network", ssid or "—"),
        ("IP address", ip or "—"),
        ("Device ID", device_id),
        "Setup complete. Media playback starts automatically.",
    ])
    _signal_player()
    log.info(f"Connected screen: SSID={ssid} IP={ip}")


def show_failed(reason: str):
    _render("SETUP FAILED", BAD, [
        reason[:70],
        "The setup Wi-Fi is coming back up — reconnect and try again.",
    ])
    _signal_player()
    log.warning(f"Failed screen: {reason}")
