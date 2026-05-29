#!/bin/bash
# radioman install script
# Raspberry Pi Zero 2W — Kali Linux arm64
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

# ── Hostname ───────────────────────────────────────────────────────────────────
CURRENT_HOST=$(hostname)
if [ "$CURRENT_HOST" != "radioman" ]; then
  log "Setting hostname to radioman..."
  hostnamectl set-hostname radioman
  # Update /etc/hosts to avoid sudo warnings
  if ! grep -q "radioman" /etc/hosts; then
    sed -i "s/127.0.1.1.*/127.0.1.1\tradioman/" /etc/hosts 2>/dev/null || \
      echo "127.0.1.1	radioman" >> /etc/hosts
  fi
  log "Hostname set to radioman (effective after reboot)"
else
  info "Hostname already set to radioman"
fi

# ── System update ──────────────────────────────────────────────────────────────
log "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ── Swap (critical for 512MB Pi Zero 2W) ──────────────────────────────────────
log "Configuring swap..."

if command -v dphys-swapfile &>/dev/null || apt-get install -y -qq dphys-swapfile 2>/dev/null; then
  DPHYS_CONF="/etc/dphys-swapfile"
  if [ -f "$DPHYS_CONF" ]; then
    sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' "$DPHYS_CONF"
    sed -i 's/^#*CONF_MAXSWAP=.*/CONF_MAXSWAP=2048/' "$DPHYS_CONF"
    dphys-swapfile swapoff 2>/dev/null || true
    dphys-swapfile setup
    dphys-swapfile swapon
    log "dphys swap set to 2GB"
  fi
else
  # Fallback: manual swapfile
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    log "Manual 2GB swapfile created"
  else
    info "Swapfile already exists"
  fi
fi

# zram — compressed in-memory swap (lz4, minimal CPU overhead on ARM)
apt-get install -y -qq zram-tools 2>/dev/null || true
if command -v zramctl &>/dev/null; then
  cat > /etc/default/zramswap <<'EOF'
ALGO=lz4
PERCENT=50
EOF
  systemctl enable zramswap 2>/dev/null || true
  systemctl restart zramswap 2>/dev/null || true
  log "zram enabled (lz4, 50% of RAM)"
fi

# ── Core system dependencies ───────────────────────────────────────────────────
log "Installing system dependencies..."
# Kali includes most security tools pre-installed; the command -v guards in the
# security tools section skip re-installation for anything already present.
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  git curl wget unzip ca-certificates \
  sqlite3 \
  iw wireless-tools rfkill \
  dphys-swapfile \
  i2c-tools \
  libssl-dev libffi-dev \
  libpcap-dev libpcap0.8 \
  libjpeg-dev zlib1g-dev \
  libusb-1.0-0-dev \
  fonts-noto \
  avahi-daemon \
  dkms \
  linux-headers-$(uname -r) 2>/dev/null || true

# ── Security tools (pre-installed on Kali, but ensure present) ─────────────────
log "Verifying security tools..."

for tool_pkg in "nmap:nmap" "aircrack-ng:aircrack-ng" "hcxpcapngtool:hcxtools" "hashcat:hashcat" "bettercap:bettercap"; do
  tool="${tool_pkg%%:*}"
  pkg="${tool_pkg##*:}"
  if command -v "$tool" &>/dev/null; then
    info "  [OK] $tool"
  else
    log "Installing $pkg..."
    apt-get install -y -qq "$pkg" 2>/dev/null && info "  [OK] $tool (installed)" || \
      warn "  [MISSING] $tool — install manually: apt-get install $pkg"
  fi
done

# ── nexmon DKMS (monitor mode for BCM43430A1) ──────────────────────────────────
log "Installing nexmon DKMS (monitor mode support)..."
# IMPORTANT: Install brcmfmac-nexmon-dkms ONLY — do NOT install firmware-nexmon.
# firmware-nexmon replaces Cypress firmware and crashes BCM43430A1 (Pi Zero 2W).
# The DKMS module patches the brcmfmac kernel driver to report monitor mode
# capability with the stock Cypress firmware — no firmware replacement needed.
if dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
  info "brcmfmac-nexmon-dkms already installed"
else
  if apt-get install -y brcmfmac-nexmon-dkms 2>/dev/null; then
    log "nexmon DKMS installed — monitor mode enabled"
  else
    warn "brcmfmac-nexmon-dkms not found via apt"
    warn "Run: sudo bash setup/install_monitor.sh  (will retry with contrib sources)"
  fi
fi

# Pin firmware-nexmon to prevent accidental install — it crashes BCM43430A1
if ! dpkg -l firmware-nexmon 2>/dev/null | grep -q "^ii"; then
  apt-mark hold firmware-nexmon 2>/dev/null || true
fi

# Reload brcmfmac with nexmon DKMS module if it was just installed
if dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
  log "Reloading brcmfmac with nexmon DKMS module..."
  modprobe -r brcmfmac brcmutil 2>/dev/null || true
  sleep 3
  modprobe brcmfmac 2>/dev/null || true
  sleep 3
fi

# ── bettercap capabilities ─────────────────────────────────────────────────────
if command -v bettercap &>/dev/null; then
  setcap cap_net_raw,cap_net_admin+eip "$(command -v bettercap)" 2>/dev/null || \
    warn "Could not set bettercap capabilities — will require sudo"
  # Disable the bettercap system service — radioman manages bettercap itself
  systemctl disable --now bettercap 2>/dev/null || true
fi

# ── Waveshare e-ink library ────────────────────────────────────────────────────
log "Installing Waveshare e-Paper library..."
if [ ! -d "/opt/waveshare-epd" ]; then
  git clone --depth=1 -q https://github.com/waveshare/e-Paper /opt/waveshare-epd
  log "Waveshare library cloned"
else
  info "Waveshare library already present"
fi

# ── Boot config (SPI, I2C, USB gadget) ────────────────────────────────────────
log "Configuring boot options (SPI, I2C, USB gadget)..."

if ! mountpoint -q /boot/firmware 2>/dev/null; then
  mount /boot/firmware 2>/dev/null && log "Mounted /boot/firmware" || \
    warn "Could not mount /boot/firmware — boot config edits may fail"
fi

CONFIG_FILE="/boot/firmware/config.txt"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_FILE" ] && CMDLINE_FILE="/boot/cmdline.txt"

# Enable SPI
if grep -q "^#dtparam=spi=" "$CONFIG_FILE"; then
  sed -i 's/^#dtparam=spi=.*/dtparam=spi=on/' "$CONFIG_FILE"
elif grep -q "^dtparam=spi=" "$CONFIG_FILE"; then
  sed -i 's/^dtparam=spi=.*/dtparam=spi=on/' "$CONFIG_FILE"
else
  echo "dtparam=spi=on" >> "$CONFIG_FILE"
fi

# Enable I2C
if grep -q "^#dtparam=i2c_arm=" "$CONFIG_FILE"; then
  sed -i 's/^#dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' "$CONFIG_FILE"
elif grep -q "^dtparam=i2c_arm=" "$CONFIG_FILE"; then
  sed -i 's/^dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' "$CONFIG_FILE"
else
  echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
fi
log "SPI and I2C enabled in $CONFIG_FILE"

# USB gadget ethernet — remove any existing dwc2 line and add the bare form
# (Pi Imager sometimes adds dr_mode=host which disables gadget mode)
sed -i '/^dtoverlay=dwc2/d' "$CONFIG_FILE"
echo "dtoverlay=dwc2" >> "$CONFIG_FILE"
log "dtoverlay=dwc2 set in $CONFIG_FILE"

# Add g_ether to modules-load in cmdline.txt (single-line file)
if ! grep -q "modules-load=dwc2,g_ether" "$CMDLINE_FILE"; then
  if grep -q "rootwait" "$CMDLINE_FILE"; then
    sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' "$CMDLINE_FILE"
  elif grep -q "console=" "$CMDLINE_FILE"; then
    sed -i 's/console=/modules-load=dwc2,g_ether console=/' "$CMDLINE_FILE"
  else
    sed -i '1s/$/ modules-load=dwc2,g_ether/' "$CMDLINE_FILE"
  fi
fi

if grep -q "modules-load=dwc2,g_ether" "$CMDLINE_FILE"; then
  log "USB gadget ethernet enabled (modules-load=dwc2,g_ether)"
else
  warn "Could not add g_ether to $CMDLINE_FILE — add it manually"
fi

# Persistent g_ether MAC — prevents macOS from treating each boot as a new device
cat > /etc/modprobe.d/g_ether.conf <<'EOF'
options g_ether host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02
EOF
log "g_ether: persistent MAC configured"

# Configure usb0 static IP with gateway and DNS so internet sharing works
# without any extra Pi-side steps after reboot.
if command -v nmcli &>/dev/null; then
  nmcli connection delete "usb-gadget" 2>/dev/null || true
  nmcli connection add \
    type ethernet \
    ifname usb0 \
    con-name "usb-gadget" \
    ipv4.method manual \
    ipv4.addresses "10.55.0.1/24" \
    ipv4.gateway "10.55.0.2" \
    ipv4.dns "1.1.1.1 8.8.8.8" \
    ipv4.never-default no \
    ipv6.method disabled \
    connection.autoconnect yes 2>/dev/null && \
    log "usb0 configured (10.55.0.1, gw 10.55.0.2, DNS 1.1.1.1)" || \
    warn "Could not create usb0 NM profile — configure manually after reboot"
fi

# ── Radioman directories and source files ──────────────────────────────────────
log "Setting up radioman directories..."
mkdir -p "$RADIOMAN_DIR" "$CAPTURES_DIR" "$WORDLISTS_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Copying source files from $SCRIPT_DIR..."
cp "$SCRIPT_DIR/daemon/"*.py "$RADIOMAN_DIR/"
rm -rf "$RADIOMAN_DIR/web"
cp -r "$SCRIPT_DIR/web" "$RADIOMAN_DIR/web"

if [ ! -f "$RADIOMAN_DIR/radioman.conf" ]; then
  cp "$SCRIPT_DIR/config/radioman.conf.example" "$RADIOMAN_DIR/radioman.conf"
  warn "Created radioman.conf from example — edit it to add your XPLT keys."
else
  info "radioman.conf already exists — skipping (your keys are safe)."
fi
cp "$SCRIPT_DIR/setup/radioman.cap" "$RADIOMAN_DIR/radioman.cap"

# ── Python virtual environment ─────────────────────────────────────────────────
log "Creating Python virtual environment..."
python3 -m venv --system-site-packages "$RADIOMAN_DIR/venv"
"$RADIOMAN_DIR/venv/bin/pip" install --quiet --upgrade pip
"$RADIOMAN_DIR/venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
log "Python dependencies installed"

# ── Waveshare library into venv ────────────────────────────────────────────────
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
  # Kali ships rockyou at /usr/share/wordlists/
  if [ -f "/usr/share/wordlists/rockyou.txt" ]; then
    cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
    log "rockyou.txt copied from /usr/share/wordlists"
  elif [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
    cp /usr/share/wordlists/rockyou.txt.gz "$WORDLISTS_DIR/"
    gunzip "$WORDLISTS_DIR/rockyou.txt.gz"
    log "rockyou.txt extracted"
  else
    # Kali wordlists package
    apt-get install -y -qq wordlists 2>/dev/null || true
    if [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
      gunzip -k /usr/share/wordlists/rockyou.txt.gz
      cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
      log "rockyou.txt installed via wordlists package"
    else
      warn "rockyou.txt not found — place it manually at $WORDLISTS_DIR/rockyou.txt"
    fi
  fi
else
  info "rockyou.txt already present"
fi

# ── Permissions ────────────────────────────────────────────────────────────────
log "Setting permissions..."
chmod +x "$RADIOMAN_DIR/radioman.py"
chmod +x "$RADIOMAN_DIR/ignore_cli.py" 2>/dev/null || true
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

# Verify nexmon DKMS built successfully
if dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
  DKMS_STATUS=$(dkms status brcmfmac-nexmon 2>/dev/null | head -1 || echo "")
  if echo "$DKMS_STATUS" | grep -qi "installed\|built"; then
    info "  [OK] brcmfmac-nexmon-dkms ($(echo "$DKMS_STATUS" | awk '{print $1}'))"
  else
    warn "  [CHECK] brcmfmac-nexmon-dkms installed but DKMS status unclear: $DKMS_STATUS"
    warn "          Run: sudo bash setup/install_monitor.sh  to diagnose"
  fi
else
  warn "  [MISSING] brcmfmac-nexmon-dkms — run: sudo bash setup/install_monitor.sh"
fi

$ALL_OK || warn "Some tools are missing — check warnings above."

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " radioman installation complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
warn "REBOOT REQUIRED to activate USB gadget / SPI / I2C changes."
echo ""
info "After reboot:"
info "  USB SSH:    ssh kali@10.55.0.1  (Windows: run scripts\\win_connect.ps1 as Admin)"
info "  WiFi SSH:   ssh kali@radioman.local  (while not scanning)"
info "  Logs:       journalctl -u radioman -f"
info "  Dashboard:  http://radioman.local:8080"
echo ""
warn "NOTE: bettercap puts wlan0 into monitor mode — WiFi SSH drops during scanning."
warn "Use the USB cable (10.55.0.1) as your primary management connection."
echo ""
warn "PiSugar battery: run setup/install_pisugar.sh separately if needed."
echo ""
