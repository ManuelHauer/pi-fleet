# Spec: QR Code on Setup Screen

## Overview

Add a scannable Wi-Fi QR code to the HDMI setup screen shown while a Pi fleet device is in onboarding mode.

The technician can point a phone camera at the screen to auto-join the temporary setup hotspot (`AEC-PI-XXXX`) instead of reading and typing the password.

## In scope

- HDMI setup card rendered by `client/onboarding/hdmi_status.py`.
- QR code encodes the hosted AP credentials: `WIFI:S:<ap_name>;T:WPA;P:<ap_password>;;`
- Fallback behavior when the QR library is absent or fails.
- Minimal new dependency: `python3-segno` installed on the Pi image.

## Out of scope

- QR code on the phone portal page (`client/onboarding/templates/setup.html`).
- QR code for the venue Wi-Fi.
- Camera auto-detection logic on the Pi itself.
- Changes to AP credential generation or networking.

## Background

Current setup flow:

1. `onboard_service.py` starts `nm_manager.start_hotspot()`.
2. SSID and password are deterministic: `AEC-PI-<serial4>` / `aec<serial4>setup`.
3. `hdmi_status.show_setup_screen()` renders a 1920×1080 PNG with the text credentials.
4. `fleet_player.py` shows that PNG while onboarding is active.

The screen is already fully in our control as a PIL image, so adding a QR code is a render-time change.

## Design decisions

### 1. QR payload format: Wi-Fi credentials

Encode the temporary setup network, not the portal URL.

```text
WIFI:S:AEC-PI-XXXX;T:WPA;P:aecXXXXsetup;;
```

Rationale:
- A phone camera can join the network directly with a single scan.
- The portal opens automatically via captive-portal DNS wildcard once connected.
- URL-only codes would still require the technician to enter the Wi-Fi password manually.

Chosen encoding: `segno.helpers.make_wifi_data()` with `security="WPA"`. This handles escaping of `;`, `:`, `,`, and `\` in the SSID/password correctly.

### 2. QR rendering library: `segno`

Add `python3-segno` to the first-boot apt install list.

Rationale:
- Pure Python, no extra dependencies.
- Available in Debian/Raspberry Pi OS Bookworm repos.
- Has a dedicated `make_wifi_data()` helper and PIL export.
- `qrcode` package is also viable but pulls `python3-pil` + `libzbar` if using extras; segno is smaller and sufficient.

Rejected: bundling a custom QR renderer in PIL. Too much code and risk of edge-case bugs.

### 3. Visual layout

Split the 1920×1080 card:
- Left 55%: title and text fields (Wi-Fi network, password, portal URL, instruction).
- Right side: centered QR code at ~420–480 px with caption underneath.

Keep all existing text labels visible.

Rationale:
- Must remain usable for technicians with older phones or low-quality cameras.
- QR should be large enough to scan reliably from a typical viewing distance.
- The setup card is shown full-screen by `mpv`; 420–480 px is comfortably scannable at TV sizes.

### 4. Graceful fallback

If `segno` import fails or QR generation raises, the screen renders exactly as before.

Implementation:
- `wifi_qr.py` exposes a single function `wifi_qr_image()`.
- `show_setup_screen()` catches exceptions, logs a warning, and falls back to the existing text-only card.
- `import segno` happens inside or guarded so the module failing to load does not crash onboarding.

## Component-level changes

### New file: `client/onboarding/wifi_qr.py`

Responsibility: generate a PIL `Image` for a Wi-Fi QR code.

```python
from PIL import Image
import segno.helpers


def wifi_qr_image(ssid: str, password: str, box_size: int = 12) -> Image.Image:
    data = segno.helpers.make_wifi_data(
        ssid=ssid, password=password, security="WPA"
    )
    qr = segno.make(data, error="m")
    img = qr.to_pil(scale=box_size).convert("RGB")
    # Add white quiet zone equivalent to ~4 modules
    border = box_size * 4
    canvas = Image.new("RGB", (img.width + border * 2, img.height + border * 2), "white")
    canvas.paste(img, (border, border))
    return canvas
```

Notes:
- The `box_size` parameter controls module size; the caller may adjust for layout.
- No filesystem I/O here; the HDMI renderer composes the image.

### Modified: `client/onboarding/hdmi_status.py`

1. Import the new helper:
   ```python
   import wifi_qr
   ```

2. New internal helper to render the card with an optional QR:
   ```python
   def _render_with_qr(title, title_color, lines, qr_img=None, footer=""):
       ...
   ```
   - Draw title and text lines on the left using the existing `_render()` logic.
   - If `qr_img` is provided, place it on the right and add caption.

3. Update `show_setup_screen()`:
   - Build the existing lines list plus instruction line.
   - Call `wifi_qr.wifi_qr_image(ap_name, ap_password, box_size=12)`.
   - Catch exceptions, log warning, proceed with text-only card.
   - Keep the TTY fallback path unchanged.

### Modified: `deploy/golden_image_firstrun.sh`

Add `python3-segno` to the first-boot apt install block.

```bash
apt-get install -y --no-install-recommends \
    network-manager dnsmasq-base \
    python3 python3-pip python3-pil python3-flask python3-requests python3-evdev \
    python3-segno \
    mpv \
    exfatprogs parted \
    rfkill iw \
    curl ca-certificates gnupg
```

## Data/model/API changes

None.

The QR payload is computed from values already produced by `nm_manager.get_ap_name()` and `nm_manager.get_ap_password()`.

No new files need to be persisted on disk; the composed PNG is written to the existing `/opt/fleet-media/system/setup.png` path.

## Asset reference list

None.

The QR is generated on the fly from text. No image assets are required.

## Risks, tradeoffs, and open questions

### Risks

| Risk | Mitigation |
|------|------------|
| `python3-segno` not available in future Pi OS versions or repo flaky | Keep graceful fallback; screen still shows text credentials. |
| QR invisible or unscannable on small/distant screens | Use 420–480 px QR and keep quiet zone; verify with real hardware. |
| Special characters in SSID/password break QR parsing | Use `segno.helpers.make_wifi_data()`. Test with special characters. |
| Existing dev/test images lack `segno` | Do not make onboarding dependent on QR; text path remains primary. |

### Tradeoffs

- **Security vs. convenience:** The QR encodes the AP password. Anyone who can see or photograph the screen can join the setup network. This is acceptable because the hotspot is temporary and exists only during onboarding.
- **TV-only vs. portal QR:** We are intentionally not adding QR to the phone portal. The TV is where the technician looks first, and the portal is already open on the connected phone.

### Open questions

None remaining.

## Verification

1. Off-Pi render test:
   - Install `python3-segno` locally (or in a venv).
   - Run a small script that imports `hdmi_status` and calls `show_setup_screen("AEC-PI-TEST", "aectestsetup")`.
   - Open `setup.png` and scan with phones; confirm it offers to join the network.

2. On-device test:
   - Install `python3-segno` on a test Pi or rebuild the golden image with the updated script.
   - Boot into onboarding mode.
   - Verify the QR appears on the TV and scans successfully.
   - Verify older text credentials remain readable.
   - Temporarily uninstall `python3-segno` and reboot; verify the text-only card still works.

3. Edge-case test:
   - Change the test SSID/password to include `;`, `:`, `,`, `\`, and spaces; confirm scanning still works.
