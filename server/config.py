"""
Configuration for the Ars Festival Media Server.
"""
import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FLEET_DATA_DIR", BASE_DIR / "data"))
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "fleet.db"

# Server
HOST = os.environ.get("FLEET_HOST", "0.0.0.0")
PORT = int(os.environ.get("FLEET_PORT", "8550"))

# Auth
ADMIN_USER = os.environ.get("FLEET_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("FLEET_ADMIN_PASS", "aec2026!")
JWT_SECRET = os.environ.get("FLEET_JWT_SECRET", "change-me-in-production-aec2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 480  # 8 hours

# Device tokens — pre-shared secret devices use to register
DEVICE_PSK = os.environ.get("FLEET_DEVICE_PSK", "aec-device-psk-2026")

# Update cadence default (seconds) — advisory for clients
DEFAULT_POLL_INTERVAL = int(os.environ.get("FLEET_POLL_INTERVAL", "30"))  # 30s

# Ensure dirs
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
