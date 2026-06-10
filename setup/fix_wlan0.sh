#!/bin/bash
# fix_wlan0.sh — permanently repair a Pi Zero 2W that loses wlan0 every boot.
#
# Cause: the nexmon brcmfmac DKMS module (from an earlier monitor-mode attempt)
# fails to bind this board's Synaptics 43436s chip (dmesg: "brcmfmac: probe ...
# failed with error -110"), so NO wlan0 comes up. It returns on every power
# cycle because the patched module is still in the module tree / initramfs and
# DKMS rebuilds it on kernel updates.
#
# This script removes the nexmon module for good, restores the stock brcmfmac
# driver, and rebuilds the initramfs so the fix survives reboots.
#
# Monitor mode on the internal radio is NOT possible on this chip — use the Alfa
# USB adapter (setup/install_alfa.sh) for capture/deauth/rogue-AP.
#
# Usage:  sudo bash setup/fix_wlan0.sh   (then reboot to confirm it persists)

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[fix-wlan0]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/fix_wlan0.sh"

KERNEL=$(uname -r)
OS_ID=$(grep "^ID=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "unknown")
log "Kernel: $KERNEL  |  OS: $OS_ID"
echo ""

# ── Before state ──────────────────────────────────────────────────────────────
info "Current state:"
ip link show wlan0 >/dev/null 2>&1 && info "  wlan0 present" || warn "  wlan0 MISSING"
dkms status 2>/dev/null | grep -i "brcmfmac-nexmon" && warn "  ↑ nexmon DKMS module is registered" || info "  no nexmon DKMS registered"
dmesg 2>/dev/null | grep -i "brcmfmac" | grep -i "error -110\|probe.*failed" | tail -2 || true
echo ""

# ── 1. Remove every nexmon brcmfmac DKMS module ──────────────────────────────
if command -v dkms >/dev/null 2>&1; then
  MODVERS=$(dkms status 2>/dev/null | grep -i 'brcmfmac-nexmon' \
    | sed -E 's#^([A-Za-z0-9_-]+)[,/ ]+([0-9][0-9.]*).*#\1/\2#' | sort -u)
  if [ -n "$MODVERS" ]; then
    for mv in $MODVERS; do
      log "Removing DKMS module: $mv"
      dkms remove "$mv" --all 2>/dev/null || warn "  dkms remove $mv returned non-zero"
    done
  else
    info "No nexmon DKMS module to remove."
  fi
else
  info "dkms not installed — skipping DKMS removal."
fi

# ── 2. Purge DKMS source + state so it can't auto-rebuild on kernel updates ───
log "Purging nexmon DKMS source/state..."
rm -rf /usr/src/brcmfmac-nexmon-* /var/lib/dkms/brcmfmac-nexmon 2>/dev/null || true

# ── 3. Remove any leftover patched module that overrides the stock driver ─────
PATCHED=$(find "/lib/modules/$KERNEL" -name "brcmfmac.ko*" -path "*updates*" 2>/dev/null || true)
if [ -n "$PATCHED" ]; then
  warn "Removing patched brcmfmac module(s):"
  echo "$PATCHED" | while read -r f; do warn "  $f"; rm -f "$f"; done
fi

# ── 4. On Kali, drop the apt package too ─────────────────────────────────────
if [ "$OS_ID" = "kali" ] && dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
  log "Removing brcmfmac-nexmon-dkms apt package..."
  apt-get remove -y brcmfmac-nexmon-dkms 2>/dev/null || true
fi

# ── 5. Restore the stock brcmfmac driver (best-effort reinstall) ─────────────
log "Restoring the stock brcmfmac driver..."
apt-get install --reinstall -y "linux-modules-$KERNEL" 2>/dev/null \
  || apt-get install --reinstall -y "linux-image-$KERNEL" 2>/dev/null \
  || apt-get install --reinstall -y firmware-brcm80211 2>/dev/null \
  || warn "Could not reinstall the kernel module package — depmod may still suffice."

# Never let firmware-nexmon come back (it crashes the chip).
apt-mark hold firmware-nexmon 2>/dev/null || true

# ── 6. Rebuild module deps + initramfs so the stock driver loads at next boot ─
log "Rebuilding module dependencies + initramfs..."
depmod -a "$KERNEL" 2>/dev/null || depmod -a 2>/dev/null || true
if command -v update-initramfs >/dev/null 2>&1; then
  update-initramfs -u -k "$KERNEL" 2>/dev/null || update-initramfs -u 2>/dev/null || \
    warn "update-initramfs failed — reboot still likely fine"
fi

# ── 7. Reload the driver now ─────────────────────────────────────────────────
log "Reloading brcmfmac..."
rfkill unblock wifi 2>/dev/null || true
ip link set mon0 down 2>/dev/null || true
iw dev mon0 del 2>/dev/null || true
modprobe -r brcmfmac brcmutil 2>/dev/null || true
sleep 2
modprobe brcmfmac 2>/dev/null || true
sleep 4

# ── 8. Verify ─────────────────────────────────────────────────────────────────
echo ""
if ip link show wlan0 >/dev/null 2>&1; then
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log " wlan0 is BACK on the stock driver ✓"
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  ip -brief link show wlan0 2>/dev/null || true
  echo ""
  info "Reconnect WiFi (this box has no nmcli — use netplan/wpa_supplicant):"
  info "  sudo netplan apply        # if you configured netplan"
  info "  # or: sudo wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf"
  echo ""
  warn "REBOOT now to confirm wlan0 survives a power cycle:  sudo reboot"
else
  warn "wlan0 still not present. Check:  dmesg | grep -i brcmfmac | tail -20"
  warn "If you see firmware load errors, run:  sudo apt-get install --reinstall firmware-brcm80211"
  warn "Then reboot."
fi
echo ""
info "Monitor mode on the internal radio is not possible (Synaptics 43436s)."
info "For capture/deauth/rogue-AP, attach a USB adapter: sudo bash setup/install_alfa.sh"
echo ""
