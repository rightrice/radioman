#!/bin/bash
# radioman update script
# Run from the cloned git repo after git pull:
#   cd /path/to/radioman && git pull && sudo bash setup/update.sh
#
# Updates deployed app files, web assets, caplet, and service.
# Never touches: radioman.conf, captures/, wordlists/

set -e

RADIOMAN_DIR="/opt/radioman"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()    { echo -e "${GREEN}[update]${NC} $1"; }
warn()   { echo -e "${YELLOW}[warning]${NC} $1"; }
err()    { echo -e "${RED}[error]${NC} $1"; exit 1; }
info()   { echo -e "${BLUE}[info]${NC} $1"; }
change() { echo -e "  ${GREEN}↑${NC} $1"; CHANGED=true; }
same()   { echo -e "  ${BLUE}·${NC} $1 (unchanged)"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/update.sh"
[ ! -d "$RADIOMAN_DIR" ] && err "radioman not installed at $RADIOMAN_DIR — run setup/install.sh first"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGED=false
REBOOT_NEEDED=false

echo ""
log "Checking for updates..."
echo ""

# ── Git status ─────────────────────────────────────────────────────────────────
if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
  CURRENT=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  BRANCH=$(git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null || echo "unknown")
  info "Repo: $SCRIPT_DIR  branch=$BRANCH  commit=$CURRENT"
else
  warn "Not a git repo — skipping version info"
fi
echo ""

# ── Helper: copy file if changed ───────────────────────────────────────────────
update_file() {
  local src="$1" dst="$2" label="$3"
  if [ ! -f "$dst" ]; then
    cp "$src" "$dst"
    change "$label (new)"
  elif ! cmp -s "$src" "$dst"; then
    cp "$src" "$dst"
    change "$label"
  else
    same "$label"
  fi
}

# ── Helper: copy dir if any file inside changed ────────────────────────────────
update_dir() {
  local src="$1" dst="$2" label="$3"
  local dir_changed=false

  # Find files in src that differ from dst
  while IFS= read -r -d '' f; do
    rel="${f#$src/}"
    dst_file="$dst/$rel"
    if [ ! -f "$dst_file" ] || ! cmp -s "$f" "$dst_file"; then
      dir_changed=true
      break
    fi
  done < <(find "$src" -type f -print0)

  if $dir_changed; then
    rm -rf "$dst"
    cp -r "$src" "$dst"
    change "$label"
  else
    same "$label"
  fi
}

# ── Swap + zram ────────────────────────────────────────────────────────────────
log "Swap / zram..."

DPHYS_CONF="/etc/dphys-swapfile"
if [ -f "$DPHYS_CONF" ]; then
  CURRENT_SWAP=$(grep "^CONF_SWAPSIZE=" "$DPHYS_CONF" | cut -d= -f2 || echo "0")
  if [ "${CURRENT_SWAP}" -lt 2048 ] 2>/dev/null; then
    sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' "$DPHYS_CONF"
    sed -i 's/^#*CONF_MAXSWAP=.*/CONF_MAXSWAP=2048/' "$DPHYS_CONF"
    dphys-swapfile swapoff 2>/dev/null || true
    dphys-swapfile setup
    dphys-swapfile swapon
    change "swap: expanded to 2GB (was ${CURRENT_SWAP}MB)"
  else
    same "swap: ${CURRENT_SWAP}MB"
  fi
fi

ZRAM_CONF="/etc/default/zramswap"
ZRAM_WANT=$'ALGO=lz4\nPERCENT=50'
if ! command -v zramctl &>/dev/null || [ ! -f "$ZRAM_CONF" ]; then
  apt-get install -y -qq zram-tools
  printf '%s\n' "$ZRAM_WANT" > "$ZRAM_CONF"
  systemctl enable zramswap 2>/dev/null || true
  systemctl restart zramswap 2>/dev/null || true
  change "zram: installed and enabled (lz4, 50%)"
elif ! grep -q "ALGO=lz4" "$ZRAM_CONF" || ! grep -q "PERCENT=50" "$ZRAM_CONF"; then
  printf '%s\n' "$ZRAM_WANT" > "$ZRAM_CONF"
  systemctl restart zramswap 2>/dev/null || true
  change "zram: config updated"
else
  same "zram: lz4 50%"
fi
echo ""

# ── Boot config (dwc2 gadget mode + g_ether) ──────────────────────────────────
log "Boot config..."

CONFIG_FILE="/boot/firmware/config.txt"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_FILE" ] && CMDLINE_FILE="/boot/cmdline.txt"

# Ensure boot partition is mounted
if ! mountpoint -q /boot/firmware 2>/dev/null; then
  mount /boot/firmware 2>/dev/null || true
fi

# Fix dwc2 — delete any existing dtoverlay=dwc2 line (catches dr_mode=host variant)
# and append the correct bare form.
if grep -q "dtoverlay=dwc2,dr_mode=host" "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^dtoverlay=dwc2/d' "$CONFIG_FILE"
  echo "dtoverlay=dwc2" >> "$CONFIG_FILE"
  change "config.txt: fixed dtoverlay=dwc2 (removed dr_mode=host)"
  REBOOT_NEEDED=true
elif ! grep -q "^dtoverlay=dwc2$" "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^dtoverlay=dwc2/d' "$CONFIG_FILE"
  echo "dtoverlay=dwc2" >> "$CONFIG_FILE"
  change "config.txt: added dtoverlay=dwc2"
  REBOOT_NEEDED=true
else
  same "config.txt: dtoverlay=dwc2"
fi

# Fix cmdline.txt — add modules-load=dwc2,g_ether if missing
if ! grep -q "modules-load=dwc2,g_ether" "$CMDLINE_FILE" 2>/dev/null; then
  if grep -q "rootwait" "$CMDLINE_FILE"; then
    sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' "$CMDLINE_FILE"
  elif grep -q "console=" "$CMDLINE_FILE"; then
    sed -i 's/console=/modules-load=dwc2,g_ether console=/' "$CMDLINE_FILE"
  else
    sed -i '1s/$/ modules-load=dwc2,g_ether/' "$CMDLINE_FILE"
  fi
  if grep -q "modules-load=dwc2,g_ether" "$CMDLINE_FILE"; then
    change "cmdline.txt: added modules-load=dwc2,g_ether"
    REBOOT_NEEDED=true
  else
    warn "cmdline.txt: could not add g_ether — edit $CMDLINE_FILE manually"
  fi
else
  same "cmdline.txt: modules-load=dwc2,g_ether"
fi

# Ensure usb0 NM profile exists
if ! nmcli connection show usb-gadget &>/dev/null; then
  nmcli connection add \
    type ethernet ifname usb0 con-name "usb-gadget" \
    ipv4.method manual ipv4.addresses "10.55.0.1/24" \
    ipv6.method disabled connection.autoconnect yes 2>/dev/null && \
    change "NetworkManager: usb-gadget profile created (10.55.0.1)" || \
    warn "NetworkManager: could not create usb-gadget profile"
else
  same "NetworkManager: usb-gadget profile"
fi
echo ""

# ── Daemon Python files ────────────────────────────────────────────────────────
log "Daemon files..."
for f in "$SCRIPT_DIR"/daemon/*.py; do
  fname="$(basename "$f")"
  update_file "$f" "$RADIOMAN_DIR/$fname" "daemon/$fname"
done
echo ""

# ── Web assets ────────────────────────────────────────────────────────────────
log "Web assets..."
update_dir "$SCRIPT_DIR/web" "$RADIOMAN_DIR/web" "web/"
echo ""

# ── Caplet ────────────────────────────────────────────────────────────────────
log "Bettercap caplet..."
update_file "$SCRIPT_DIR/setup/radioman.cap" "$RADIOMAN_DIR/radioman.cap" "radioman.cap"
echo ""

# ── Systemd service ────────────────────────────────────────────────────────────
log "Systemd service..."
SERVICE_SRC="$SCRIPT_DIR/setup/radioman.service"
SERVICE_DST="/etc/systemd/system/radioman.service"
if [ ! -f "$SERVICE_DST" ] || ! cmp -s "$SERVICE_SRC" "$SERVICE_DST"; then
  cp "$SERVICE_SRC" "$SERVICE_DST"
  systemctl daemon-reload
  change "radioman.service (daemon-reload done)"
else
  same "radioman.service"
fi
echo ""

# ── Waveshare library (re-sync if e-Paper repo is present) ────────────────────
log "Waveshare library..."
WAVESHARE_SRC="/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_PKG="$RADIOMAN_DIR/venv/lib/python${PY_VER}/site-packages"

if [ -d "$WAVESHARE_SRC" ]; then
  update_dir "$WAVESHARE_SRC" "$SITE_PKG/waveshare_epd" "waveshare_epd (venv)"
else
  same "waveshare_epd (source not found — skipping)"
fi
echo ""

# ── Python packages ────────────────────────────────────────────────────────────
log "Python packages..."
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
VENV_PIP="$RADIOMAN_DIR/venv/bin/pip"

if [ -f "$REQUIREMENTS" ]; then
  # Check if installed packages match requirements
  MISSING=$("$VENV_PIP" install --dry-run -r "$REQUIREMENTS" --quiet 2>&1 | grep "Would install" || true)
  if [ -n "$MISSING" ]; then
    "$VENV_PIP" install --quiet -r "$REQUIREMENTS"
    change "Python packages: $MISSING"
  else
    same "Python packages"
  fi
else
  same "Python packages (no requirements.txt — skipping)"
fi
echo ""

# ── Permissions ────────────────────────────────────────────────────────────────
chmod +x "$RADIOMAN_DIR/radioman.py" 2>/dev/null || true
chmod +x "$RADIOMAN_DIR/ignore_cli.py" 2>/dev/null || true

# ── Restart if anything changed ────────────────────────────────────────────────
if $CHANGED; then
  log "Changes detected — restarting radioman..."
  systemctl restart radioman
  sleep 2
  STATUS=$(systemctl is-active radioman 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "active" ]; then
    log "radioman restarted successfully"
  else
    warn "radioman may not have started cleanly — check: journalctl -u radioman -n 30"
  fi
else
  log "Nothing changed — radioman not restarted"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Update complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Logs:      journalctl -u radioman -f"
info "Dashboard: http://radioman.local:8080"
if $REBOOT_NEEDED; then
  echo ""
  warn "REBOOT REQUIRED — boot config changed (USB gadget / SPI / I2C)."
  warn "Run: sudo reboot"
  warn "After reboot, set your Mac USB interface to 10.55.0.2 / 255.255.255.0"
  warn "then SSH: ssh pi@10.55.0.1"
fi
echo ""
