#!/bin/bash
# install_monitor.sh — set up Wi-Fi monitor mode on Raspberry Pi Zero 2W
#
# The Pi Zero 2W's BCM43430A1 chip (Cypress firmware 7.45.96.s1) supports
# creating a VDEV monitor interface alongside the managed wlan0 interface.
# No nexmon firmware patching is required.
#
# Usage:
#   sudo bash setup/install_monitor.sh
#
# What it does:
#   1. Removes nexmon packages if installed (they crash this chip revision)
#   2. Restores original Cypress firmware if nexmon modified it
#   3. Removes the 6.12 kernel boot override from config.txt if present
#   4. Verifies VDEV monitor mode works

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[monitor]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_monitor.sh"

IFACE="${1:-wlan0}"
MON_IFACE="mon0"

# ── Remove nexmon packages ────────────────────────────────────────────────────
for pkg in brcmfmac-nexmon-dkms firmware-nexmon; do
  if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
    log "Removing $pkg..."
    apt-get remove -y "$pkg" 2>/dev/null || dpkg --remove "$pkg" 2>/dev/null || true
  fi
done

# ── Restore original firmware if nexmon modified it ───────────────────────────
# Download and extract the original firmware package to restore any modified files
BRCM_FW="/usr/lib/firmware/brcm"
CYPRESS_FW="/usr/lib/firmware/cypress"

# Check if the board-specific firmware is the nexmon build (374608 bytes)
BOARD_FW=$(readlink -f "$BRCM_FW/brcmfmac43430-sdio.raspberrypi,model-zero-2-w.bin" 2>/dev/null || true)
if [ -n "$BOARD_FW" ]; then
  SIZE=$(wc -c < "$BOARD_FW" 2>/dev/null || echo 0)
  if [ "$SIZE" -lt 390000 ]; then
    warn "Board firmware looks like nexmon (${SIZE} bytes) — restoring original..."
    ORIG_DEB=$(mktemp -d)
    apt-get download firmware-brcm80211 -o Dir::Cache="$ORIG_DEB" 2>/dev/null || \
      apt-get download firmware-brcm80211 2>/dev/null || true
    DEB=$(find "$ORIG_DEB" /tmp -name "firmware-brcm80211*.deb" 2>/dev/null | head -1)
    if [ -n "$DEB" ]; then
      EXTRACT=$(mktemp -d)
      dpkg-deb -x "$DEB" "$EXTRACT"
      for src in \
        "$EXTRACT/usr/lib/firmware/brcm/brcmfmac43436s-sdio.bin" \
        "$EXTRACT/usr/lib/firmware/cypress/cyfmac43430-sdio.bin" \
        "$EXTRACT/usr/lib/firmware/brcm/brcmfmac43430-sdio.raspberrypi,model-zero-2-w.txt"; do
        [ -f "$src" ] && cp "$src" "${src/$EXTRACT/}" && info "Restored $(basename $src)"
      done
      rm -rf "$EXTRACT" "$ORIG_DEB"
    else
      warn "Could not download firmware-brcm80211 — restore manually if needed"
    fi
  else
    info "Firmware looks like original Cypress (${SIZE} bytes) — no restore needed"
  fi
fi

# Remove any empty CLM blob we created as a nexmon workaround
CLM_STUB="$BRCM_FW/brcmfmac43430-sdio.raspberrypi,model-zero-2-w.clm_blob"
if [ -f "$CLM_STUB" ] && [ ! -s "$CLM_STUB" ]; then
  rm -f "$CLM_STUB"
  info "Removed empty CLM blob stub"
fi

# ── Remove 6.12 kernel boot override ─────────────────────────────────────────
CONFIG_TXT="/boot/firmware/config.txt"
if grep -q "^kernel=vmlinuz-6.12" "$CONFIG_TXT" 2>/dev/null; then
  log "Removing 6.12 kernel override from config.txt..."
  # Remove the block we added (kernel= and initramfs= lines + comment)
  sed -i '/^# Boot kernel 6\.12/d' "$CONFIG_TXT"
  sed -i '/^kernel=vmlinuz-6\.12/d' "$CONFIG_TXT"
  sed -i '/^initramfs initrd-6\.12/d' "$CONFIG_TXT"
  info "config.txt restored — will boot default kernel on next reboot"
fi

# Remove copied kernel/initrd files from boot partition
for f in /boot/firmware/vmlinuz-6.12-rpi-v8 /boot/firmware/initrd-6.12-rpi-v8; do
  [ -f "$f" ] && rm -f "$f" && info "Removed $(basename $f)"
done

# ── Reload driver with original firmware ──────────────────────────────────────
log "Reloading brcmfmac..."
ip link set "$MON_IFACE" down 2>/dev/null || true
iw dev "$MON_IFACE" del 2>/dev/null || true
modprobe -r brcmfmac brcmutil 2>/dev/null || true
sleep 3
modprobe brcmfmac 2>/dev/null || true
sleep 5

# ── Verify wlan0 is back ──────────────────────────────────────────────────────
if ! ip link show "$IFACE" &>/dev/null; then
  err "$IFACE not found after driver reload — check dmesg for errors"
fi
info "$IFACE is up"

# ── Test VDEV monitor mode ────────────────────────────────────────────────────
log "Testing VDEV monitor mode..."
iw dev "$MON_IFACE" del 2>/dev/null || true

PHY=$(iw dev "$IFACE" info 2>/dev/null | awk '/wiphy/{print "phy"$NF}')
[ -z "$PHY" ] && PHY="phy0"

if iw phy "$PHY" interface add "$MON_IFACE" type monitor 2>/dev/null && \
   ip link set "$MON_IFACE" up 2>/dev/null; then
  log "Monitor mode working: $MON_IFACE on $PHY"
  iw dev "$MON_IFACE" del 2>/dev/null || true
else
  warn "VDEV monitor mode test failed — check 'dmesg | grep brcm'"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Monitor mode setup complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "radioman creates mon0 automatically when scanning starts."
info "No manual setup needed — just start a scan from the dashboard."
if grep -q "^kernel=vmlinuz-6\.12" "$CONFIG_TXT" 2>/dev/null; then
  : # already removed above
else
  KERNEL=$(uname -r)
  if [[ "$KERNEL" == 6.12* ]]; then
    warn "Still booted into kernel 6.12 — reboot to return to default kernel"
  fi
fi
