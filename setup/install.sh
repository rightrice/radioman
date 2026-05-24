#!/bin/bash
# radioman install script
# Raspberry Pi Zero 2W — Raspberry Pi OS Lite 64-bit (Bookworm)
# Run as root: sudo bash install.sh

set -e

RADIOMAN_DIR="/opt/radioman"
RADIOMAN_USER="pi"
CAPTURES_DIR="/opt/radioman/captures"
WORDLISTS_DIR="/opt/radioman/wordlists"
LOG_DIR="/var/log/radioman"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[radioman]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
  err "Please run as root: sudo bash install.sh"
fi

log "Starting radioman installation..."
echo ""

# ── System update ──────────────────────────────────────────────────────────────
log "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ── Core dependencies ──────────────────────────────────────────────────────────
log "Installing core dependencies..."
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  git curl wget \
  aircrack-ng \
  nmap \
  sqlite3 \
  libssl-dev libffi-dev \
  fonts-dejavu-core \
  i2c-tools \
  libjpeg-dev zlib1g-dev \
  hostapd dnsmasq \
  iw wireless-tools rfkill

# ── Enable SPI and I2C ─────────────────────────────────────────────────────────
log "Enabling SPI and I2C interfaces..."
if ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=spi=on" /boot/config.txt 2>/dev/null; then
  CONFIG_FILE="/boot/firmware/config.txt"
  [ -f "/boot/config.txt" ] && CONFIG_FILE="/boot/config.txt"
  echo "dtparam=spi=on" >> "$CONFIG_FILE"
  echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
  log "SPI and I2C enabled in $CONFIG_FILE"
else
  info "SPI/I2C already enabled"
fi

# ── bettercap ──────────────────────────────────────────────────────────────────
log "Installing bettercap..."
if ! command -v bettercap &>/dev/null; then
  BETTERCAP_VER=$(curl -s https://api.github.com/repos/bettercap/bettercap/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
  BETTERCAP_URL="https://github.com/bettercap/bettercap/releases/download/${BETTERCAP_VER}/bettercap_linux_armv6l_${BETTERCAP_VER}.zip"
  warn "Downloading bettercap ${BETTERCAP_VER} (armv6l for Pi Zero 2W)..."
  wget -q "$BETTERCAP_URL" -O /tmp/bettercap.zip
  unzip -q /tmp/bettercap.zip -d /tmp/bettercap
  mv /tmp/bettercap/bettercap /usr/local/bin/bettercap
  chmod +x /usr/local/bin/bettercap
  rm -rf /tmp/bettercap /tmp/bettercap.zip
  bettercap -eval "caplets.update; quit" || true
  log "bettercap installed"
else
  info "bettercap already installed"
fi

# ── PiSugar 2 software ─────────────────────────────────────────────────────────
log "Installing PiSugar 2 server..."
if ! command -v pisugar-server &>/dev/null; then
  curl -s https://cdn.pisugar.com/release/pisugar-power-manager.sh | bash || \
    warn "PiSugar install script failed — battery readings will use direct I2C fallback"
else
  info "PiSugar server already installed"
fi

# ── Waveshare e-ink library ────────────────────────────────────────────────────
log "Installing Waveshare e-Paper library..."
if [ ! -d "/opt/waveshare-epd" ]; then
  git clone --depth=1 -q https://github.com/waveshare/e-Paper /opt/waveshare-epd
  log "Waveshare library cloned to /opt/waveshare-epd"
else
  info "Waveshare library already installed"
fi

# ── Radioman directory setup ───────────────────────────────────────────────────
log "Setting up radioman directories..."
mkdir -p "$RADIOMAN_DIR" "$CAPTURES_DIR" "$WORDLISTS_DIR" "$LOG_DIR"
chown -R "$RADIOMAN_USER:$RADIOMAN_USER" "$RADIOMAN_DIR" "$LOG_DIR"

# Copy source files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Copying radioman source from $SCRIPT_DIR..."
cp -r "$SCRIPT_DIR/daemon/"* "$RADIOMAN_DIR/"
cp -r "$SCRIPT_DIR/web" "$RADIOMAN_DIR/web"
cp -r "$SCRIPT_DIR/config/radioman.conf" "$RADIOMAN_DIR/radioman.conf"
cp "$SCRIPT_DIR/setup/radioman.cap" "$RADIOMAN_DIR/radioman.cap"

# Symlink Waveshare library into daemon
ln -sf /opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib/waveshare_epd \
  "$RADIOMAN_DIR/waveshare_epd" 2>/dev/null || \
  warn "Could not symlink waveshare_epd — check /opt/waveshare-epd path"

# ── Python virtual environment ─────────────────────────────────────────────────
log "Creating Python virtual environment..."
python3 -m venv "$RADIOMAN_DIR/venv"
"$RADIOMAN_DIR/venv/bin/pip" install --quiet --upgrade pip
"$RADIOMAN_DIR/venv/bin/pip" install --quiet \
  flask \
  flask-cors \
  pillow \
  smbus2 \
  RPi.GPIO \
  requests \
  schedule

log "Python dependencies installed"

# ── rockyou wordlist ───────────────────────────────────────────────────────────
log "Setting up rockyou wordlist..."
if [ ! -f "$WORDLISTS_DIR/rockyou.txt" ]; then
  if [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
    cp /usr/share/wordlists/rockyou.txt.gz "$WORDLISTS_DIR/"
    gunzip "$WORDLISTS_DIR/rockyou.txt.gz"
    log "rockyou.txt extracted from system wordlists"
  elif [ -f "/usr/share/wordlists/rockyou.txt" ]; then
    cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
    log "rockyou.txt copied from system wordlists"
  else
    warn "rockyou.txt not found. Install wordlists package: sudo apt install wordlists"
    warn "Or place rockyou.txt manually at $WORDLISTS_DIR/rockyou.txt"
  fi
else
  info "rockyou.txt already present"
fi

# ── systemd service ────────────────────────────────────────────────────────────
log "Installing systemd service..."
cp "$SCRIPT_DIR/setup/radioman.service" /etc/systemd/system/radioman.service
systemctl daemon-reload
systemctl enable radioman.service
log "radioman service enabled (will start on next boot)"

# ── bettercap capabilities ─────────────────────────────────────────────────────
log "Granting bettercap raw socket capabilities..."
setcap cap_net_raw,cap_net_admin+eip /usr/local/bin/bettercap 2>/dev/null || \
  warn "Could not set bettercap capabilities — run as root"

# ── Wi-Fi monitor mode check ───────────────────────────────────────────────────
log "Checking wireless interface..."
IFACE=$(iw dev 2>/dev/null | awk '/Interface/{print $2}' | head -1)
if [ -n "$IFACE" ]; then
  log "Wireless interface found: $IFACE"
else
  warn "No wireless interface detected. Ensure wlan0 is available."
fi

# ── Permissions ────────────────────────────────────────────────────────────────
log "Setting permissions..."
chown -R "$RADIOMAN_USER:$RADIOMAN_USER" "$RADIOMAN_DIR" "$LOG_DIR"
chmod +x "$RADIOMAN_DIR/radioman.py"

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " radioman installation complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Commands:"
info "  Start now:    sudo systemctl start radioman"
info "  View logs:    journalctl -u radioman -f"
info "  Dashboard:    http://<pi-ip>:8080"
info "  Captures:     $CAPTURES_DIR"
echo ""
warn "A reboot is recommended to activate SPI/I2C changes."
echo ""
