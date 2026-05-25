#!/bin/bash
# radioman uninstall script
# Removes the radioman installation so install.sh can be run cleanly again.
# Run as root: sudo bash setup/uninstall.sh
#
# By default: preserves radioman.conf, captures, and wordlists.
# Use --full to remove everything including user data and installed tools.

set -e

RADIOMAN_DIR="/opt/radioman"
WAVESHARE_DIR="/opt/waveshare-epd"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[uninstall]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
info() { echo -e "${BLUE}[info]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/uninstall.sh"

FULL=false
[ "$1" = "--full" ] && FULL=true

echo ""
log "Radioman uninstall"
if $FULL; then
  warn "Full uninstall — removes user data and installed tools"
else
  info "Safe uninstall — preserves radioman.conf, captures, and wordlists"
  info "Use --full to remove everything"
fi
echo ""

# ── Stop and disable radioman service ─────────────────────────────────────────
log "Stopping radioman service..."
systemctl stop radioman 2>/dev/null && info "  radioman stopped" || info "  radioman was not running"
systemctl disable radioman 2>/dev/null || true
rm -f /etc/systemd/system/radioman.service
systemctl daemon-reload
log "radioman service removed"

# ── Remove NetworkManager usb-gadget profile ───────────────────────────────────
if command -v nmcli &>/dev/null; then
  nmcli connection delete "usb-gadget" 2>/dev/null && \
    log "usb-gadget NM profile removed" || true
fi

# ── Remove radioman application files ─────────────────────────────────────────
log "Removing application files..."
if [ -d "$RADIOMAN_DIR" ]; then
  if $FULL; then
    rm -rf "$RADIOMAN_DIR"
    log "Removed $RADIOMAN_DIR (full)"
  else
    # Remove app files and venv; preserve user data
    rm -f  "$RADIOMAN_DIR"/*.py
    rm -rf "$RADIOMAN_DIR/venv"
    rm -rf "$RADIOMAN_DIR/web"
    rm -f  "$RADIOMAN_DIR/radioman.cap"
    info "Preserved: radioman.conf, captures/, wordlists/"
    log "Removed app files and venv from $RADIOMAN_DIR"
  fi
else
  info "$RADIOMAN_DIR not found — nothing to remove"
fi

# ── Full uninstall extras ──────────────────────────────────────────────────────
if $FULL; then
  # Waveshare library
  if [ -d "$WAVESHARE_DIR" ]; then
    rm -rf "$WAVESHARE_DIR"
    log "Removed Waveshare library ($WAVESHARE_DIR)"
  fi

  # Revert boot config changes
  log "Reverting boot config..."
  CONFIG_FILE="/boot/firmware/config.txt"
  [ -f "/boot/config.txt" ] && CONFIG_FILE="/boot/config.txt"
  CMDLINE_FILE="/boot/firmware/cmdline.txt"
  [ -f "/boot/cmdline.txt" ] && CMDLINE_FILE="/boot/cmdline.txt"

  if ! mountpoint -q /boot/firmware 2>/dev/null; then
    mount /boot/firmware 2>/dev/null || warn "Could not mount /boot/firmware — boot config not reverted"
  fi

  if mountpoint -q /boot/firmware 2>/dev/null || [ -f "$CONFIG_FILE" ]; then
    sed -i 's/^dtparam=spi=on.*/#dtparam=spi=on/'     "$CONFIG_FILE" 2>/dev/null || true
    sed -i 's/^dtparam=i2c_arm=on.*/#dtparam=i2c_arm=on/' "$CONFIG_FILE" 2>/dev/null || true
    sed -i '/^dtoverlay=dwc2$/d'                         "$CONFIG_FILE" 2>/dev/null || true
    sed -i 's/ modules-load=dwc2,g_ether//'             "$CMDLINE_FILE" 2>/dev/null || true
    log "Boot config reverted (SPI, I2C, USB gadget)"
    warn "REBOOT REQUIRED for boot config changes to take effect"
  fi

  # Remove installed tools (bettercap, hcxtools, hashcat)
  echo ""
  read -rp "Remove bettercap, hcxtools, and hashcat? [y/N]: " REMOVE_TOOLS
  if [[ "${REMOVE_TOOLS,,}" == "y" ]]; then
    apt-get remove -y bettercap hcxtools hashcat 2>/dev/null || true
    rm -f /usr/local/bin/bettercap /usr/local/bin/hcxpcapngtool /usr/local/bin/hashcat
    log "Tools removed"
  else
    info "Tools kept"
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Uninstall complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Re-install:  sudo bash setup/install.sh"
echo ""
