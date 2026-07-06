#!/bin/bash
# Bare-metal install of the fleet server on Debian/Ubuntu (alternative to
# docker compose — pick ONE). Run as root on the target server from the
# repo root:  sudo bash deploy/server/install_server.sh
#
# Installs to /opt/pi-fleet, data in /var/lib/fleet-server, runs as the
# 'fleet' system user behind whatever reverse proxy the host already has
# (the service binds 127.0.0.1:8550 — put nginx/caddy/traefik in front
# for TLS; see deploy/server/Caddyfile for the Caddy variant).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR="/opt/pi-fleet"
DATA_DIR="/var/lib/fleet-server"
ENV_FILE="/etc/fleet-server/env"

echo "== Ars Fleet Server install =="

# System user
id -u fleet &>/dev/null || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin fleet

# Python
apt-get update -qq
apt-get install -y --no-install-recommends python3 python3-venv python3-pip

# Code
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_ROOT/server" "$REPO_ROOT/dashboard" "$INSTALL_DIR/"
python3 -m venv "$INSTALL_DIR/server/.venv"
"$INSTALL_DIR/server/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/server/requirements.txt"

# Data dir
mkdir -p "$DATA_DIR"
chown -R fleet:fleet "$DATA_DIR" "$INSTALL_DIR"

# Env file (create once, keep existing)
if [ ! -f "$ENV_FILE" ]; then
  mkdir -p "$(dirname "$ENV_FILE")"
  cat > "$ENV_FILE" <<EOF
FLEET_DATA_DIR=$DATA_DIR
FLEET_ADMIN_USER=admin
FLEET_ADMIN_PASS=$(openssl rand -hex 12)
FLEET_JWT_SECRET=$(openssl rand -hex 32)
FLEET_DEVICE_PSK=$(openssl rand -hex 16)
FLEET_DISABLE_SHELL=1
EOF
  chmod 600 "$ENV_FILE"
  echo "  ✓ Generated $ENV_FILE with random secrets — READ IT and copy the"
  echo "    FLEET_DEVICE_PSK into your SD provisioning (prepare_sd_card.sh)."
else
  echo "  ✓ $ENV_FILE exists — keeping it"
fi

# systemd unit
cp "$REPO_ROOT/deploy/server/fleet-server.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fleet-server.service

echo ""
echo "✅ fleet-server running on 127.0.0.1:8550"
echo "   Put a TLS reverse proxy in front (nginx/caddy) and point it at :8550."
echo "   Dashboard: https://<your-domain>/dashboard/"
echo "   Admin credentials: see $ENV_FILE"
