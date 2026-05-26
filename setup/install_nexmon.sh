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

# ── Detect board-specific firmware path ────────────────────────────────────────
# Pi Zero 2W loads brcmfmac43430-sdio.raspberrypi,model-zero-2-w.bin (a symlink
# to brcmfmac43436s-sdio.bin) rather than the generic brcmfmac43430-sdio.bin.
# That board-specific path takes priority, so we must patch it directly.
FW_BRCM_DIR="/usr/lib/firmware/brcm"
BOARD_COMPAT=$(cat /proc/device-tree/compatible 2>/dev/null | tr '\0' '\n' | grep "raspberrypi" | head -1 || true)
BOARD_FW_LINK=""
if [ -n "$BOARD_COMPAT" ]; then
  CANDIDATE="${FW_BRCM_DIR}/brcmfmac43430-sdio.${BOARD_COMPAT}.bin"
  [ -e "$CANDIDATE" ] && BOARD_FW_LINK="$CANDIDATE"
fi

install_fw() {
  local dest="$1"
  local label="$2"
  # dest may be a symlink — resolve to real file so we patch what the kernel loads
  local real
  real=$(readlink -f "$dest")
  local backup="${real}.orig"
  if [ ! -f "$backup" ]; then
    cp "$real" "$backup"
    log "Backed up ${label} → $(basename $backup)"
  else
    info "Backup already exists for ${label}"
  fi
  cp "$FROM_FILE" "$real"
  log "Nexmon installed to ${label} (${real})"
}

# ── Install to board-specific path (takes kernel priority) ────────────────────
if [ -n "$BOARD_FW_LINK" ]; then
  info "Board: $BOARD_COMPAT"
  install_fw "$BOARD_FW_LINK" "board firmware"
else
  warn "No board-specific firmware path found — installing to generic path only"
fi

# ── Install to generic path as well ───────────────────────────────────────────
GENERIC_FW=""
for f in "${FW_BRCM_DIR}/brcmfmac43430-sdio.bin" \
          /lib/firmware/brcm/brcmfmac43430-sdio.bin; do
  [ -f "$f" ] && GENERIC_FW="$f" && break
done
if [ -n "$GENERIC_FW" ]; then
  install_fw "$GENERIC_FW" "generic firmware"
fi

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
