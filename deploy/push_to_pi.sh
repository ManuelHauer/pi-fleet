#!/bin/bash
# Push fleet client to a Raspberry Pi over SSH
# Usage: ./push_to_pi.sh <pi-ip> [server-url] [group] [label]
set -e

PI_HOST="${1:?Usage: $0 <pi-ip> [server-url] [group] [label]}"
FLEET_SERVER="${2:-http://169.254.180.14:8550}"
DEVICE_GROUP="${3:-default}"
DEVICE_LABEL="${4:-}"
PI_USER="${PI_USER:-pi}"

CLIENT_DIR="$(cd "$(dirname "$0")/../client" && pwd)"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🎬 Pushing fleet client to $PI_USER@$PI_HOST"

# Copy files
scp "$CLIENT_DIR/fleet_client.py" \
    "$CLIENT_DIR/local_control.py" \
    "$CLIENT_DIR/fleet-client.service" \
    "$CLIENT_DIR/fleet-local-control.service" \
    "$DEPLOY_DIR/setup_pi.sh" \
    "$PI_USER@$PI_HOST:/tmp/" \
    || true

# Copy onboarding folder
scp -r "$CLIENT_DIR/onboarding" "$PI_USER@$PI_HOST:/tmp/"

# Run setup
ssh "$PI_USER@$PI_HOST" "sudo bash /tmp/setup_pi.sh '$FLEET_SERVER' '$DEVICE_GROUP' '$DEVICE_LABEL'"

echo "✅ Deployed to $PI_HOST"
