#!/bin/bash
# radioman — PiSugar 2 setup script
# Run separately after the main install, once the terminal is stable.
# Usage: sudo bash setup/install_pisugar.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[pisugar]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_pisugar.sh"

log "Installing PiSugar 2 server..."

if ! command -v pisugar-server &>/dev/null; then
  curl -s https://cdn.pisugar.com/release/pisugar-power-manager.sh | bash || \
    err "PiSugar install failed"

  # Stop immediately — never run with a blank config.
  # pisugar-server with no model set sends unrecognized I2C commands that can
  # corrupt the IP5312 chip state and leave the bus unresponsive.
  systemctl stop pisugar-server 2>/dev/null || true
  log "pisugar-server stopped for configuration"
else
  info "pisugar-server already installed"
  systemctl stop pisugar-server 2>/dev/null || true
fi

# ── Model selection ────────────────────────────────────────────────────────────
echo ""
log "Select your PiSugar model:"
echo "  1) PiSugar 2 (2-LEDs)   ← most common for Pi Zero"
echo "  2) PiSugar 2 (4-LEDs)"
echo "  3) PiSugar 2 Pro"
echo "  4) PiSugar 3"
echo ""
read -rp "Enter number [1]: " MODEL_CHOICE
MODEL_CHOICE="${MODEL_CHOICE:-1}"

case "$MODEL_CHOICE" in
  1) MODEL="PiSugar 2 (2-LEDs)" ;;
  2) MODEL="PiSugar 2 (4-LEDs)" ;;
  3) MODEL="PiSugar 2 Pro" ;;
  4) MODEL="PiSugar 3" ;;
  *) warn "Invalid choice — defaulting to PiSugar 2 (2-LEDs)"; MODEL="PiSugar 2 (2-LEDs)" ;;
esac

log "Configuring for: $MODEL"

# Set model via debconf (works if pisugar-server is a proper deb package)
echo "pisugar-server pisugar-server/model select $MODEL" \
  | debconf-set-selections
DEBIAN_FRONTEND=noninteractive dpkg-reconfigure pisugar-server 2>/dev/null || \
  warn "dpkg-reconfigure failed — config.json will be written directly"

# Write config.json directly to ensure model and auto_power_on are set
PISUGAR_CFG="/etc/pisugar-server/config.json"
if [ -f "$PISUGAR_CFG" ]; then
  python3 - "$PISUGAR_CFG" "$MODEL" <<'PYEOF'
import json, sys
path, model = sys.argv[1], sys.argv[2]
with open(path) as f:
    c = json.load(f)
c["auto_power_on"] = True
with open(path, "w") as f:
    json.dump(c, f, indent=2)
PYEOF
  log "auto_power_on enabled in $PISUGAR_CFG"
else
  warn "Config file not found at $PISUGAR_CFG — pisugar-server may not have installed correctly"
fi

# ── Start and verify ───────────────────────────────────────────────────────────
systemctl enable pisugar-server
systemctl start pisugar-server
sleep 3

log "Verifying connection..."
RESULT=$(echo "get battery" | nc -U /tmp/pisugar-server.sock 2>/dev/null || echo "")

if echo "$RESULT" | grep -q "I2C not connected"; then
  warn "pisugar-server running but I2C not connected — check physical seating of PiSugar on GPIO header"
elif [ -z "$RESULT" ]; then
  warn "No response from pisugar-server socket — service may still be starting"
else
  log "PiSugar responded: $RESULT"
fi

echo ""
log "PiSugar setup complete (model: $MODEL)"
info "Check status:  echo 'get battery' | nc -U /tmp/pisugar-server.sock"
info "Service logs:  journalctl -u pisugar-server -f"
echo ""
