#!/bin/bash
# install_monitor.sh — install and verify Wi-Fi monitor mode on Raspberry Pi Zero 2W
#
# On Kali Linux, monitor mode is enabled via the brcmfmac-nexmon-dkms package.
# This patches the brcmfmac kernel driver to allow monitor mode with the stock
# Cypress firmware — no firmware replacement is needed or wanted.
#
# CRITICAL: Do NOT install firmware-nexmon. It replaces Cypress firmware files
# and crashes the BCM43430A1 (Pi Zero 2W) due to chip revision mismatch.
#
# Usage:
#   sudo bash setup/install_monitor.sh
#
# Safe to run multiple times. Also repairs a broken nexmon DKMS install.

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

# ── Block firmware-nexmon ─────────────────────────────────────────────────────
# Hold the package so it can't be pulled in accidentally by apt
apt-mark hold firmware-nexmon 2>/dev/null || true

# If firmware-nexmon is already installed, remove it and restore original firmware
if dpkg -l firmware-nexmon 2>/dev/null | grep -q "^ii"; then
  warn "firmware-nexmon is installed — removing it (crashes BCM43430A1)"
  apt-mark unhold firmware-nexmon 2>/dev/null || true
  apt-get remove -y firmware-nexmon 2>/dev/null || true
  apt-mark hold firmware-nexmon 2>/dev/null || true

  # Restore original Cypress firmware
  log "Restoring stock Cypress firmware..."
  apt-get install -y --reinstall firmware-brcm80211 2>/dev/null && \
    log "firmware-brcm80211 reinstalled" || \
    warn "Could not reinstall firmware-brcm80211 — check dmesg for ENOENT firmware errors"
fi

# ── Ensure linux-headers are installed ────────────────────────────────────────
KERNEL=$(uname -r)
log "Kernel: $KERNEL"

if ! dpkg -l "linux-headers-${KERNEL}" 2>/dev/null | grep -q "^ii"; then
  log "Installing linux-headers-${KERNEL}..."
  apt-get install -y "linux-headers-${KERNEL}" 2>/dev/null || \
    apt-get install -y linux-headers-$(uname -r | sed 's/+.*//')-rpi-v8 2>/dev/null || \
    warn "Could not install kernel headers — DKMS build may fail"
fi

# ── Install brcmfmac-nexmon-dkms ──────────────────────────────────────────────
if dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
  log "brcmfmac-nexmon-dkms already installed"
  DKMS_STATUS=$(dkms status brcmfmac-nexmon 2>/dev/null | head -1 || echo "")
  if echo "$DKMS_STATUS" | grep -qi "installed\|built"; then
    info "DKMS module status: $DKMS_STATUS"
  else
    warn "DKMS module not built — rebuilding for kernel $KERNEL..."
    NEXMON_VER=$(dkms status brcmfmac-nexmon 2>/dev/null | grep -o '[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' | head -1 || echo "")
    if [ -n "$NEXMON_VER" ]; then
      dkms build -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" 2>/dev/null && \
      dkms install -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" 2>/dev/null && \
        log "DKMS module rebuilt and installed" || \
        warn "DKMS rebuild failed — check: dkms status && dmesg | grep nexmon"
    fi
  fi
else
  log "Installing brcmfmac-nexmon-dkms..."
  apt-get update -qq
  if apt-get install -y brcmfmac-nexmon-dkms 2>/dev/null; then
    log "brcmfmac-nexmon-dkms installed"
  else
    # Try adding Kali contrib explicitly
    warn "Package not found — checking sources..."
    SOURCES=$(cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true)
    if ! echo "$SOURCES" | grep -q "kali"; then
      warn "No Kali repository detected. This script is designed for Kali Linux."
      warn "If you're on Kali, try: apt-get install -y brcmfmac-nexmon-dkms"
      warn "If the package still isn't found, run: apt-get update && apt-cache search nexmon"
      err "brcmfmac-nexmon-dkms not available. Cannot enable monitor mode."
    fi
    err "brcmfmac-nexmon-dkms install failed — run: apt-get update && apt-get install brcmfmac-nexmon-dkms"
  fi
fi

# ── Reload brcmfmac with nexmon-patched module ────────────────────────────────
log "Reloading brcmfmac driver..."
ip link set "$MON_IFACE" down 2>/dev/null || true
iw dev "$MON_IFACE" del 2>/dev/null || true
modprobe -r brcmfmac brcmutil 2>/dev/null || true
sleep 3
modprobe brcmfmac 2>/dev/null || true
sleep 4

# ── Verify wlan0 is back ──────────────────────────────────────────────────────
if ! ip link show "$IFACE" &>/dev/null; then
  err "$IFACE not found after driver reload — check: dmesg | grep -i brcm"
fi
info "$IFACE is present"

# ── Test monitor mode ─────────────────────────────────────────────────────────
log "Testing monitor mode..."

# Test 1: VDEV interface (preferred — keeps wlan0 in managed mode)
iw dev "$MON_IFACE" del 2>/dev/null || true
PHY=$(iw dev "$IFACE" info 2>/dev/null | awk '/wiphy/{print "phy"$NF}')
[ -z "$PHY" ] && PHY="phy0"

VDEV_OK=false
if iw phy "$PHY" interface add "$MON_IFACE" type monitor 2>/dev/null; then
  if ip link set "$MON_IFACE" up 2>/dev/null; then
    log "VDEV monitor interface $MON_IFACE is up on $PHY"
    VDEV_OK=true
    iw dev "$MON_IFACE" del 2>/dev/null || true
  fi
fi

# Test 2: airmon-ng check
if command -v airmon-ng &>/dev/null; then
  AIRMON_OUT=$(airmon-ng 2>/dev/null | grep "$IFACE" || echo "")
  if echo "$AIRMON_OUT" | grep -q "monitor"; then
    info "airmon-ng reports $IFACE supports monitor mode"
  fi
fi

if $VDEV_OK; then
  log "Monitor mode: WORKING"
else
  warn "VDEV monitor mode test failed — check: dmesg | grep -i 'brcm\|monitor'"
  warn "If the kernel just changed, a reboot may be required."
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Monitor mode setup complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "radioman creates mon0 automatically when scanning starts."
info "No manual interface setup needed — just start a scan from the dashboard."
echo ""
if ! $VDEV_OK; then
  warn "Monitor mode test failed. Try rebooting: sudo reboot"
  warn "Then re-run this script to verify."
fi
