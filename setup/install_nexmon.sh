#!/bin/bash
# install_nexmon.sh — install nexmon patched firmware to enable monitor mode
#
# Usage:
#   sudo bash setup/install_nexmon.sh --from-file /tmp/brcmfmac43430-sdio.nexmon.bin
#
# Build the firmware on WSL2 first:
#   bash scripts/build_nexmon_wsl.sh [pi-host]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[nexmon]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_nexmon.sh --from-file <path>"

# ── Parse args ─────────────────────────────────────────────────────────────────
FROM_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-file) FROM_FILE="$2"; shift 2 ;;
    *) err "Unknown argument: $1" ;;
  esac
done

[ -z "$FROM_FILE" ] && err "Usage: sudo bash setup/install_nexmon.sh --from-file <firmware.bin>
  Build the firmware first on WSL2:
    bash scripts/build_nexmon_wsl.sh [pi-host]"

[ ! -f "$FROM_FILE" ] && err "Firmware file not found: $FROM_FILE"
SIZE=$(wc -c < "$FROM_FILE")
[ "$SIZE" -lt 10000 ] && err "File too small (${SIZE} bytes) — not a valid firmware"
info "Using pre-built firmware: $FROM_FILE (${SIZE} bytes)"

# ── Locate system firmware ─────────────────────────────────────────────────────
FW_FILE=""
for f in /usr/lib/firmware/brcm/brcmfmac43430-sdio.bin \
          /lib/firmware/brcm/brcmfmac43430-sdio.bin; do
  [ -f "$f" ] && FW_FILE="$f" && break
done
[ -z "$FW_FILE" ] && err "brcmfmac43430-sdio.bin not found — is the WiFi driver loaded?"
info "System firmware: $FW_FILE"

# ── Backup original ────────────────────────────────────────────────────────────
BACKUP="${FW_FILE}.orig"
if [ ! -f "$BACKUP" ]; then
  cp "$FW_FILE" "$BACKUP"
  log "Original firmware backed up to $(basename $BACKUP)"
else
  info "Backup already exists — skipping"
fi

# ── Install ────────────────────────────────────────────────────────────────────
log "Installing nexmon patched firmware..."
cp "$FROM_FILE" "$FW_FILE"

# ── Reload driver ──────────────────────────────────────────────────────────────
log "Reloading brcmfmac with patched firmware..."
ip link set wlan0 down 2>/dev/null || true
modprobe -r brcmfmac 2>/dev/null || true
sleep 2
modprobe brcmfmac 2>/dev/null || true
sleep 3
ip link set wlan0 up 2>/dev/null || true
sleep 1

# ── Test monitor mode ──────────────────────────────────────────────────────────
log "Testing monitor mode..."
if iw dev wlan0 set type monitor 2>/dev/null; then
  log "Monitor mode working on wlan0"
  iw dev wlan0 set type managed 2>/dev/null || true
elif iw phy phy0 interface add mon0 type monitor 2>/dev/null; then
  log "Monitor mode working (phy interface)"
  iw dev mon0 del 2>/dev/null || true
else
  warn "Monitor mode test inconclusive — reboot may be needed"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " nexmon install complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
warn "Reboot to load the patched firmware cleanly: sudo reboot"
echo ""
info "After reboot, verify:"
info "  sudo iw dev wlan0 set type monitor && echo 'monitor mode OK' && sudo iw dev wlan0 set type managed"
info ""
info "To restore original firmware:"
info "  sudo cp ${BACKUP} ${FW_FILE} && sudo reboot"
