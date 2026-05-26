#!/bin/bash
# install_nexmon.sh — enable monitor mode on BCM43430/1 (Pi Zero W / Zero 2W)
#
# The stock brcmfmac driver does not support monitor mode on this chip.
# nexmon patches the WiFi firmware to unlock it.
#
# Uses the pre-built firmware blob from seemoo-lab/nexmon — no compilation
# required. The patched firmware (based on 7.45.41.46) replaces the stock
# firmware (7.45.96) and the brcmfmac driver loads it without issue.
#
# Run: sudo bash setup/install_nexmon.sh

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

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_nexmon.sh"

# ── Locate firmware file ───────────────────────────────────────────────────────
log "Locating firmware..."
FW_FILE=""
for f in /usr/lib/firmware/brcm/brcmfmac43430-sdio.bin \
          /lib/firmware/brcm/brcmfmac43430-sdio.bin; do
  [ -f "$f" ] && FW_FILE="$f" && break
done
[ -z "$FW_FILE" ] && err "brcmfmac43430-sdio.bin not found — is the WiFi driver loaded?"
info "System firmware: $FW_FILE"

# ── Install git if needed ──────────────────────────────────────────────────────
apt-get install -y -qq git git-lfs

# ── Clone nexmon and pull the firmware blob via LFS ───────────────────────────
NEXMON_DIR="/opt/nexmon"
PATCH_SUBDIR="patches/bcm43430a1/7_45_41_46/nexmon"
FW_BLOB="$NEXMON_DIR/$PATCH_SUBDIR/brcmfmac43430-sdio.bin"

if [ ! -f "$FW_BLOB" ] || [ "$(wc -c < "$FW_BLOB")" -lt 10000 ]; then
  log "Cloning nexmon with LFS..."
  rm -rf "$NEXMON_DIR"
  git clone --depth=1 --filter=blob:none --sparse -q \
    https://github.com/seemoo-lab/nexmon "$NEXMON_DIR"
  git -C "$NEXMON_DIR" sparse-checkout set "$PATCH_SUBDIR"
  git -C "$NEXMON_DIR" checkout -q
  git -C "$NEXMON_DIR" lfs install --local -q
  git -C "$NEXMON_DIR" lfs pull --include="$PATCH_SUBDIR/brcmfmac43430-sdio.bin"
fi

[ ! -f "$FW_BLOB" ] && err "Firmware blob not found: $FW_BLOB"
[ "$(wc -c < "$FW_BLOB")" -lt 10000 ] && err "Firmware blob too small — LFS pull may have failed"
info "Nexmon firmware: $FW_BLOB"

# ── Backup original firmware ───────────────────────────────────────────────────
BACKUP="${FW_FILE}.orig"
if [ ! -f "$BACKUP" ]; then
  cp "$FW_FILE" "$BACKUP"
  log "Original firmware backed up to $(basename $BACKUP)"
else
  info "Backup already exists — skipping"
fi

# ── Install patched firmware ───────────────────────────────────────────────────
log "Installing nexmon patched firmware..."
cp "$FW_BLOB" "$FW_FILE"

# ── Reload driver ──────────────────────────────────────────────────────────────
log "Reloading brcmfmac..."
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
warn "Reboot required to load the patched firmware cleanly:"
warn "  sudo reboot"
echo ""
info "After reboot, verify with:"
info "  sudo iw dev wlan0 set type monitor && echo OK && sudo iw dev wlan0 set type managed"
info ""
info "To restore the original firmware:"
info "  sudo cp ${BACKUP} ${FW_FILE} && sudo reboot"
