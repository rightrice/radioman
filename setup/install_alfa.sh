#!/bin/bash
# install_alfa.sh — drivers for Alfa external USB Wi-Fi adapters
#
#   AWUS036ACH   → Realtek  RTL8812AU  → out-of-tree DKMS driver (aircrack-ng)
#   AWUS036AXML  → MediaTek MT7921AU   → in-kernel mt7921u driver + firmware
#
# Both support AP mode, monitor mode, and frame injection — the internal Pi
# Zero 2W radio (Synaptics/Cypress BCM43436s) does NOT. Every active capability
# (external-radio capture, deauth, rogue AP / evil twin) runs on this adapter,
# conventionally brought up as wlan1.
#
# Run as root AFTER install.sh. The model is auto-detected from the plugged-in
# adapter; if it can't be detected you'll be prompted (or pass it explicitly):
#   sudo bash setup/install_alfa.sh           # auto-detect, else prompt
#   sudo bash setup/install_alfa.sh ach        # force AWUS036ACH  (RTL8812AU)
#   sudo bash setup/install_alfa.sh axml       # force AWUS036AXML (MT7921AU)
#
# Safe to run multiple times.

set -e

RTL_SRC="/opt/rtl8812au"
RTL_REPO="https://github.com/aircrack-ng/rtl8812au.git"
RTL_DKMS="8812au"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[alfa]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_alfa.sh"

KERNEL=$(uname -r)
log "Kernel: $KERNEL"

# ── Identify the adapter ────────────────────────────────────────────────────
# Known USB IDs / chipset strings:
#   RTL8812AU (AWUS036ACH):  0bda:8812 / 8813 / 8814, "RTL8812"
#   MT7921AU  (AWUS036AXML): 0e8d:7961 / 7922,        "MT7921" / "MediaTek"
detect_model() {
  local usb
  usb=$(lsusb 2>/dev/null || echo "")
  if echo "$usb" | grep -qiE "0e8d:79(61|22)|MT7921|MEDIATEK.*79(21|61)"; then
    echo "axml"; return
  fi
  if echo "$usb" | grep -qiE "0bda:88(12|13|14)|RTL88(12|14)"; then
    echo "ach"; return
  fi
  echo ""
}

# Normalize an explicit argument, if given.
MODEL=""
case "${1:-}" in
  ach|ACH|AWUS036ACH|rtl8812au|8812au)   MODEL="ach" ;;
  axml|AXML|AWUS036AXML|mt7921|mt7921u)  MODEL="axml" ;;
  "")                                    MODEL="$(detect_model)" ;;
  *) err "Unknown model '$1' — use 'ach' (AWUS036ACH) or 'axml' (AWUS036AXML)" ;;
esac

if [ -n "$MODEL" ] && [ -z "${1:-}" ]; then
  log "Auto-detected adapter: $([ "$MODEL" = ach ] && echo 'AWUS036ACH (RTL8812AU)' || echo 'AWUS036AXML (MT7921AU)')"
fi

# Couldn't detect and none forced → prompt (or bail if non-interactive).
if [ -z "$MODEL" ]; then
  if [ -t 0 ]; then
    echo ""
    echo "Could not auto-detect the adapter (is it plugged in?). Which model?"
    echo "  1) AWUS036ACH  — Realtek  RTL8812AU"
    echo "  2) AWUS036AXML — MediaTek MT7921AU"
    read -rp "Select [1/2]: " choice
    case "$choice" in
      1) MODEL="ach" ;;
      2) MODEL="axml" ;;
      *) err "Invalid selection — re-run and choose 1 or 2" ;;
    esac
  else
    err "Could not auto-detect the adapter and there's no terminal to prompt. Re-run with the model: sudo bash setup/install_alfa.sh [ach|axml]"
  fi
fi

# ── AWUS036ACH — Realtek RTL8812AU (out-of-tree DKMS) ───────────────────────
install_rtl8812au() {
  log "AWUS036ACH — building Realtek RTL8812AU via DKMS"
  apt-get install -y -qq \
    build-essential dkms git bc \
    "linux-headers-${KERNEL}" 2>/dev/null || \
    apt-get install -y -qq build-essential dkms git bc linux-headers-generic 2>/dev/null || \
    warn "Some build deps may be missing — DKMS build may fail"

  if ! dpkg -l "linux-headers-${KERNEL}" 2>/dev/null | grep -q "^ii" \
     && [ ! -d "/lib/modules/${KERNEL}/build" ]; then
    warn "Kernel headers for ${KERNEL} not found — the DKMS build will likely fail."
    warn "On Ubuntu Pi: sudo apt-get install linux-modules-extra-raspi linux-headers-raspi"
  fi

  if [ -d "$RTL_SRC/.git" ]; then
    log "Updating existing rtl8812au source..."
    git -C "$RTL_SRC" pull -q 2>/dev/null || warn "Could not update — using existing checkout"
  else
    log "Cloning aircrack-ng/rtl8812au..."
    rm -rf "$RTL_SRC"
    git clone --depth=1 -q "$RTL_REPO" "$RTL_SRC"
  fi

  local ver
  ver=$(grep -m1 'PACKAGE_VERSION' "$RTL_SRC/dkms.conf" 2>/dev/null | cut -d'"' -f2 || echo "5.6.4.2")
  info "rtl8812au version: $ver"
  dkms remove "${RTL_DKMS}/${ver}" --all 2>/dev/null || true

  log "Building rtl8812au via DKMS (~5-10 min on the Pi Zero 2W)..."
  if [ -x "$RTL_SRC/dkms-install.sh" ]; then
    ( cd "$RTL_SRC" && ./dkms-install.sh ) || warn "dkms-install.sh returned non-zero — checking status"
  else
    cp -r "$RTL_SRC" "/usr/src/${RTL_DKMS}-${ver}"
    dkms add     -m "$RTL_DKMS" -v "$ver" 2>/dev/null || true
    dkms build   -m "$RTL_DKMS" -v "$ver" -k "$KERNEL" || warn "DKMS build failed"
    dkms install -m "$RTL_DKMS" -v "$ver" -k "$KERNEL" --force || warn "DKMS install failed"
  fi

  modprobe 88XXau 2>/dev/null || modprobe 8812au 2>/dev/null || \
    warn "Could not modprobe the driver — a reboot may be required"

  local st
  st=$(dkms status "$RTL_DKMS" 2>/dev/null | head -1 || echo "")
  if echo "$st" | grep -qi "installed"; then
    log "DKMS module installed: $st"
  else
    warn "DKMS status unclear: ${st:-none}  (check: dkms status && dmesg | grep -i 8812)"
  fi
}

# ── AWUS036AXML — MediaTek MT7921AU (in-kernel mt7921u + firmware) ──────────
install_mt7921() {
  log "AWUS036AXML — MediaTek MT7921AU (in-kernel mt7921u driver)"
  info "MT7921 is supported in the mainline kernel (>= 5.12) — no DKMS build needed,"
  info "but it requires the MediaTek firmware blobs from linux-firmware."

  apt-get install -y -qq linux-firmware 2>/dev/null || \
    warn "Could not install linux-firmware — monitor mode may fail without MT7921 firmware"

  if modinfo mt7921u >/dev/null 2>&1; then
    modprobe mt7921u 2>/dev/null || warn "modprobe mt7921u failed — a reboot may be required"
    log "mt7921u module present and loaded"
  else
    warn "mt7921u is not available in this kernel (${KERNEL})."
    warn "MT7921 needs kernel >= 5.12. Update the kernel + firmware, then reboot:"
    warn "  sudo apt-get update && sudo apt-get install linux-firmware && sudo apt-get full-upgrade"
  fi

  # MT7921 firmware filenames (presence check is best-effort, just informative).
  if ls /lib/firmware/mediatek/WIFI_MT7961* >/dev/null 2>&1 \
     || ls /lib/firmware/mediatek/mt7921* >/dev/null 2>&1; then
    info "MT7921 firmware present in /lib/firmware/mediatek"
  else
    warn "MT7921 firmware not found in /lib/firmware/mediatek — install/upgrade linux-firmware"
  fi
}

# ── Run the chosen path ─────────────────────────────────────────────────────
case "$MODEL" in
  ach)  install_rtl8812au ;;
  axml) install_mt7921 ;;
esac

# ── Verify (model-agnostic) ─────────────────────────────────────────────────
echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Alfa adapter driver setup complete ($MODEL)"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Plug in the adapter, then confirm it appears and supports AP + monitor:"
info "  ip link                       # expect a new wlanX interface"
info "  iw dev wlan1 info             # adapter info"
info "  iw list | grep -A10 'Supported interface modes'   # must list 'AP' and 'monitor'"
echo ""
info "Set its interface name in radioman.conf:"
info "  [offensive]  ap_interface = wlan1     (rogue AP / evil twin)"
info "  point the capture interface at wlan1 for external-radio capture/deauth"
echo ""
warn "If the interface only appears as wlan1 after a reboot, that's normal."
echo ""
