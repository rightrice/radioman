#!/bin/bash
# radioman install script
# Raspberry Pi Zero 2W — Raspberry Pi OS Lite 64-bit (Bookworm)
# Run as root: sudo bash setup/install.sh

set -e

RADIOMAN_DIR="/opt/radioman"
CAPTURES_DIR="/opt/radioman/captures"
WORDLISTS_DIR="/opt/radioman/wordlists"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[radioman]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install.sh"

log "Starting radioman installation..."
echo ""

# ── System update ──────────────────────────────────────────────────────────────
log "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ── Core system dependencies ───────────────────────────────────────────────────
log "Installing system dependencies..."
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  python3-lgpio \
  git curl wget unzip ca-certificates \
  sqlite3 \
  nmap \
  aircrack-ng \
  iw wireless-tools rfkill \
  i2c-tools \
  libssl-dev libffi-dev \
  libpcap-dev libpcap0.8 \
  libcurl4-openssl-dev \
  libhwloc-dev \
  libjpeg-dev zlib1g-dev \
  libusb-1.0-0-dev \
  swig \
  liblgpio-dev \
  fonts-dejavu-core \
  fonts-noto

# ── hcxtools (PMKID extraction) ────────────────────────────────────────────────
if command -v hcxpcapngtool &>/dev/null; then
  info "hcxtools already installed"
else
  log "Installing hcxtools..."
  if apt-get install -y -qq hcxtools 2>/dev/null; then
    log "hcxtools installed via apt"
  else
    warn "hcxtools not in apt — building from source..."
    apt-get install -y -qq build-essential pkg-config
    TMP=$(mktemp -d)
    git clone --depth=1 -q https://github.com/ZerBea/hcxtools "$TMP/hcxtools"
    make -C "$TMP/hcxtools" -j2 --quiet
    make -C "$TMP/hcxtools" install --quiet
    rm -rf "$TMP"
    log "hcxtools built and installed"
  fi
fi

# ── hashcat (PMKID + EAPOL cracking) ──────────────────────────────────────────
if command -v hashcat &>/dev/null; then
  info "hashcat already installed"
else
  log "Installing hashcat..."
  if apt-get install -y -qq hashcat 2>/dev/null; then
    log "hashcat installed via apt"
  else
    warn "hashcat not in apt — building from source..."
    apt-get install -y -qq build-essential pkg-config libssl-dev
    TMP=$(mktemp -d)
    HC_VER=$(curl -s https://api.github.com/repos/hashcat/hashcat/releases/latest \
             | grep '"tag_name"' | cut -d'"' -f4 | tr -d 'v')
    wget -q "https://github.com/hashcat/hashcat/releases/download/v${HC_VER}/hashcat-${HC_VER}.tar.gz" \
      -O "$TMP/hashcat.tar.gz"
    tar -xzf "$TMP/hashcat.tar.gz" -C "$TMP"
    make -C "$TMP/hashcat-${HC_VER}" -j2 --quiet
    cp "$TMP/hashcat-${HC_VER}/hashcat" /usr/local/bin/hashcat
    cp -r "$TMP/hashcat-${HC_VER}/OpenCL" /usr/local/share/hashcat-opencl 2>/dev/null || true
    rm -rf "$TMP"
    log "hashcat built and installed"
  fi
fi

# ── bettercap ──────────────────────────────────────────────────────────────────
log "Installing bettercap..."
if ! command -v bettercap &>/dev/null; then
  apt-get install -y -qq bettercap && log "bettercap installed via apt" || {
    warn "apt install failed — downloading arm64 binary..."
    BC_VER=$(curl -s https://api.github.com/repos/bettercap/bettercap/releases/latest \
             | grep '"tag_name"' | cut -d'"' -f4)
    BC_URL="https://github.com/bettercap/bettercap/releases/download/${BC_VER}/bettercap_linux_arm64_${BC_VER}.zip"
    wget -q "$BC_URL" -O /tmp/bettercap.zip
    unzip -q /tmp/bettercap.zip -d /tmp/bettercap_tmp
    mv /tmp/bettercap_tmp/bettercap /usr/local/bin/bettercap
    chmod +x /usr/local/bin/bettercap
    rm -rf /tmp/bettercap_tmp /tmp/bettercap.zip
    log "bettercap binary installed"
  }
else
  info "bettercap already installed"
fi

setcap cap_net_raw,cap_net_admin+eip "$(command -v bettercap)" 2>/dev/null || \
  warn "Could not set bettercap capabilities — will require sudo"

# ── PiSugar 2 ─────────────────────────────────────────────────────────────────
log "Installing PiSugar 2 server..."
if ! command -v pisugar-server &>/dev/null; then
  curl -s https://cdn.pisugar.com/release/pisugar-power-manager.sh | bash || \
    warn "PiSugar install failed — battery will use direct I2C fallback"

  # Stop immediately — never let it run with a blank config.
  # pisugar-server with no model set sends unrecognized I2C commands that can
  # corrupt the IP5312 chip state and leave the bus unresponsive.
  systemctl stop pisugar-server 2>/dev/null || true

  # Set model non-interactively via debconf then write safe defaults to config.
  echo "pisugar-server pisugar-server/model select PiSugar 2 (2-LEDs)" \
    | debconf-set-selections
  DEBIAN_FRONTEND=noninteractive dpkg-reconfigure pisugar-server 2>/dev/null || \
    warn "dpkg-reconfigure pisugar-server failed — run manually: sudo dpkg-reconfigure pisugar-server"

  # Write auto_power_on so the Pi stays up when USB is disconnected.
  PISUGAR_CFG="/etc/pisugar-server/config.json"
  if [ -f "$PISUGAR_CFG" ]; then
    python3 - "$PISUGAR_CFG" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    c = json.load(f)
c["auto_power_on"] = True
with open(path, "w") as f:
    json.dump(c, f, indent=2)
PYEOF
    log "PiSugar auto_power_on enabled"
  fi

  systemctl enable pisugar-server
  systemctl start pisugar-server
  log "PiSugar server configured and started (model: PiSugar 2 (2-LEDs))"
else
  info "PiSugar server already installed — skipping configuration"
fi

# ── Waveshare e-ink library ────────────────────────────────────────────────────
log "Installing Waveshare e-Paper library..."
if [ ! -d "/opt/waveshare-epd" ]; then
  git clone --depth=1 -q https://github.com/waveshare/e-Paper /opt/waveshare-epd
  log "Waveshare library cloned"
else
  info "Waveshare library already present"
fi

# ── Enable SPI, I2C, and USB gadget mode ──────────────────────────────────────
log "Configuring boot options (SPI, I2C, USB gadget)..."

CONFIG_FILE="/boot/firmware/config.txt"
[ -f "/boot/config.txt" ] && CONFIG_FILE="/boot/config.txt"

# Enable SPI — change dtparam=spi=off → on, or append if absent
if grep -q "^dtparam=spi" "$CONFIG_FILE"; then
  sed -i 's/^dtparam=spi=.*/dtparam=spi=on/' "$CONFIG_FILE"
else
  echo "dtparam=spi=on" >> "$CONFIG_FILE"
fi

# Enable I2C — same pattern
if grep -q "^dtparam=i2c_arm" "$CONFIG_FILE"; then
  sed -i 's/^dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' "$CONFIG_FILE"
else
  echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
fi

log "SPI and I2C enabled in $CONFIG_FILE"

# USB gadget ethernet (SSH over USB data cable)
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[ -f "/boot/cmdline.txt" ] && CMDLINE_FILE="/boot/cmdline.txt"

grep -q "^dtoverlay=dwc2" "$CONFIG_FILE" || echo "dtoverlay=dwc2" >> "$CONFIG_FILE"

if ! grep -q "modules-load=dwc2,g_ether" "$CMDLINE_FILE"; then
  sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' "$CMDLINE_FILE"
  log "USB gadget ethernet enabled — SSH via USB cable after reboot"
fi

# ── Radioman directories and source files ──────────────────────────────────────
log "Setting up radioman directories..."
mkdir -p "$RADIOMAN_DIR" "$CAPTURES_DIR" "$WORDLISTS_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Copying source files from $SCRIPT_DIR..."
cp "$SCRIPT_DIR/daemon/"*.py "$RADIOMAN_DIR/"
rm -rf "$RADIOMAN_DIR/web"
cp -r "$SCRIPT_DIR/web"     "$RADIOMAN_DIR/web"

# Only create radioman.conf from the example if one doesn't exist yet.
# This preserves any API keys the user has already configured.
if [ ! -f "$RADIOMAN_DIR/radioman.conf" ]; then
  cp "$SCRIPT_DIR/config/radioman.conf.example" "$RADIOMAN_DIR/radioman.conf"
  warn "Created radioman.conf from example — edit it to add your XPLT keys."
else
  info "radioman.conf already exists — skipping (your keys are safe)."
fi
cp "$SCRIPT_DIR/setup/radioman.cap"   "$RADIOMAN_DIR/radioman.cap"

# ── Python virtual environment ─────────────────────────────────────────────────
log "Creating Python virtual environment..."
python3 -m venv --system-site-packages "$RADIOMAN_DIR/venv"
"$RADIOMAN_DIR/venv/bin/pip" install --quiet --upgrade pip

"$RADIOMAN_DIR/venv/bin/pip" install --quiet \
  flask \
  flask-cors \
  pillow \
  smbus2 \
  requests \
  spidev \
  gpiozero \
  lgpio

log "Python dependencies installed"

# ── Install Waveshare library into venv site-packages ─────────────────────────
log "Installing Waveshare library into venv..."
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_PKG="$RADIOMAN_DIR/venv/lib/python${PY_VER}/site-packages"
WAVESHARE_SRC="/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib/waveshare_epd"

if [ -d "$WAVESHARE_SRC" ]; then
  rm -rf "$SITE_PKG/waveshare_epd"
  cp -r "$WAVESHARE_SRC" "$SITE_PKG/waveshare_epd"
  log "waveshare_epd installed to venv"
else
  warn "Waveshare library not found at $WAVESHARE_SRC — display may not work"
fi

# ── rockyou wordlist ───────────────────────────────────────────────────────────
log "Setting up rockyou wordlist..."
if [ ! -f "$WORDLISTS_DIR/rockyou.txt" ]; then
  if [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
    cp /usr/share/wordlists/rockyou.txt.gz "$WORDLISTS_DIR/"
    gunzip "$WORDLISTS_DIR/rockyou.txt.gz"
    log "rockyou.txt extracted"
  elif [ -f "/usr/share/wordlists/rockyou.txt" ]; then
    cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
    log "rockyou.txt copied"
  else
    warn "rockyou.txt not found — place it manually at $WORDLISTS_DIR/rockyou.txt"
  fi
else
  info "rockyou.txt already present"
fi

# ── Permissions ────────────────────────────────────────────────────────────────
log "Setting permissions..."
chmod +x "$RADIOMAN_DIR/radioman.py"
chmod +x "$RADIOMAN_DIR/ignore_cli.py"
chmod 755 "$CAPTURES_DIR" "$WORDLISTS_DIR"

# ── systemd service ────────────────────────────────────────────────────────────
log "Installing systemd service..."
cp "$SCRIPT_DIR/setup/radioman.service" /etc/systemd/system/radioman.service
systemctl daemon-reload
systemctl enable radioman.service
log "radioman service installed and enabled"

# ── Verify critical tools ──────────────────────────────────────────────────────
echo ""
log "Verifying tool installation..."
ALL_OK=true
for tool in bettercap aircrack-ng hcxpcapngtool hashcat nmap iw; do
  if command -v "$tool" &>/dev/null; then
    info "  [OK] $tool"
  else
    warn "  [MISSING] $tool"
    ALL_OK=false
  fi
done
$ALL_OK || warn "Some tools are missing — check warnings above."

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " radioman installation complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
warn "REBOOT REQUIRED to activate SPI / I2C / USB gadget changes."
echo ""
info "After reboot:"
info "  USB SSH:    ssh pi@radioman.local  (via USB data cable)"
info "  WiFi SSH:   ssh pi@radioman.local  (while not scanning)"
info "  Logs:       journalctl -u radioman -f"
info "  Dashboard:  http://radioman.local:8080"
echo ""
warn "NOTE: bettercap puts wlan0 into monitor mode — WiFi SSH drops during scanning."
warn "Use the USB cable as your primary management connection."
echo ""
