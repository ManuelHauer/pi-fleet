#!/usr/bin/env python3
"""
HDMI framebuffer status display.
Shows setup instructions and connection status on the connected screen.
Uses a simple approach: write to framebuffer via Python PIL or fallback to console text.
"""
import subprocess
import logging
from pathlib import Path

log = logging.getLogger("hdmi-status")

# Try to use framebuffer directly, fallback to console
FB_DEVICE = Path("/dev/fb0")


def _write_console(lines: list):
    """Write status to virtual console (tty1)."""
    try:
        text = "\n".join(lines)
        # Clear screen and write
        subprocess.run(
            ["sudo", "bash", "-c", f'echo -e "\\033[2J\\033[H{text}" > /dev/tty1'],
            capture_output=True, timeout=5
        )
    except Exception as e:
        log.warning(f"Console write failed: {e}")


def show_setup_screen(ap_name: str, ap_password: str, portal_url: str = "http://192.168.4.1"):
    """Show the setup instructions on HDMI."""
    lines = [
        "",
        "  ╔══════════════════════════════════════════╗",
        "  ║     🎬 ARS FESTIVAL MEDIA PLAYER         ║",
        "  ║         — SETUP MODE —                    ║",
        "  ╠══════════════════════════════════════════╣",
        "  ║                                           ║",
        f" ║  Wi-Fi Network:  {ap_name:<24}║",
        f" ║  Password:       {ap_password:<24}║",
        "  ║                                           ║",
        "  ║  INSTRUCTIONS:                            ║",
        "  ║  1. Connect phone to Wi-Fi above          ║",
        "  ║  2. Open browser (portal auto-opens)      ║",
        f" ║  3. Or go to: {portal_url:<27}║",
        "  ║  4. Enter venue Wi-Fi credentials         ║",
        "  ║  5. Wait for 'Connected' confirmation     ║",
        "  ║                                           ║",
        "  ╚══════════════════════════════════════════╝",
        "",
    ]
    _write_console(lines)
    log.info(f"Setup screen displayed: AP={ap_name}")


def show_connecting(ssid: str):
    """Show connecting status."""
    lines = [
        "",
        "  ╔══════════════════════════════════════════╗",
        "  ║     🎬 ARS FESTIVAL MEDIA PLAYER         ║",
        "  ╠══════════════════════════════════════════╣",
        "  ║                                           ║",
        f" ║  Connecting to: {ssid:<25}║",
        "  ║                                           ║",
        "  ║  Please wait…                             ║",
        "  ║                                           ║",
        "  ╚══════════════════════════════════════════╝",
    ]
    _write_console(lines)


def show_connected(ssid: str, ip: str, device_id: str):
    """Show connected + ready status."""
    lines = [
        "",
        "  ╔══════════════════════════════════════════╗",
        "  ║     🎬 ARS FESTIVAL MEDIA PLAYER         ║",
        "  ║           ✅ CONNECTED                    ║",
        "  ╠══════════════════════════════════════════╣",
        "  ║                                           ║",
        f" ║  Wi-Fi:     {ssid:<29}║",
        f" ║  IP:        {ip:<29}║",
        f" ║  Device ID: {device_id:<29}║",
        "  ║                                           ║",
        "  ║  Syncing media from server…               ║",
        "  ║  Playback will start automatically.       ║",
        "  ║                                           ║",
        "  ╚══════════════════════════════════════════╝",
    ]
    _write_console(lines)
    log.info(f"Connected screen: SSID={ssid} IP={ip}")


def show_failed(error: str):
    """Show connection failure."""
    lines = [
        "",
        "  ╔══════════════════════════════════════════╗",
        "  ║     🎬 ARS FESTIVAL MEDIA PLAYER         ║",
        "  ║           ❌ CONNECTION FAILED             ║",
        "  ╠══════════════════════════════════════════╣",
        "  ║                                           ║",
        f" ║  Error: {error[:33]:<33}║",
        "  ║                                           ║",
        "  ║  Returning to setup mode…                 ║",
        "  ║  Please try again.                        ║",
        "  ║                                           ║",
        "  ╚══════════════════════════════════════════╝",
    ]
    _write_console(lines)
    log.warning(f"Failed screen: {error}")


def show_playing(device_id: str, manifest_version: str = "—"):
    """Show playback active status (brief, then VLC takes over display)."""
    lines = [
        "",
        f"  🎬 Ars Festival Player | {device_id} | v{manifest_version[:15]}",
        "  Playback active — VLC will take over display shortly.",
        "",
    ]
    _write_console(lines)
