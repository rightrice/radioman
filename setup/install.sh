#!/bin/bash
# radioman install script
# Raspberry Pi Zero 2W — Ubuntu Server 24.04 LTS arm64 (also supports Kali Linux)
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

# ── OS / board detection ─────────────────────────────────────────────────────
OS_ID=$(grep "^ID=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "unknown")
REAL_USER="${SUDO_USER:-ubuntu}"
BOARD=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "unknown")
RAM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
case "$BOARD" in *"Raspberry Pi 5"*) IS_PI5=1 ;; *) IS_PI5=0 ;; esac

log "Starting radioman installation (OS: $OS_ID, user: $REAL_USER)..."
info "Board: $BOARD  |  RAM: ${RAM_MB}MB  |  Pi 5: $([ "$IS_PI5" = 1 ] && echo yes || echo no)"
echo ""

# ── Hostname ───────────────────────────────────────────────────────────────────
CURRENT_HOST=$(hostname)
if [ "$CURRENT_HOST" != "radioman" ]; then
  log "Setting hostname to radioman..."
  hostnamectl set-hostname radioman
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
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

# ── Swap (only needed on low-RAM boards like the 512MB Pi Zero 2W) ─────────────
# A Pi 5 (4–16GB) doesn't need swap or zram; skip it there to save SD wear.
if [ "${RAM_MB:-0}" -gt 0 ] && [ "${RAM_MB}" -lt 1536 ]; then
  log "Low RAM (${RAM_MB}MB) — configuring swap..."
  if command -v dphys-swapfile &>/dev/null; then
    # Raspberry Pi OS only
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
else
  info "RAM ${RAM_MB}MB — swap/zram not needed, skipping"
fi

# ── Core system dependencies ───────────────────────────────────────────────────
log "Installing system dependencies..."
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  git curl wget unzip ca-certificates \
  sqlite3 \
  iw wireless-tools rfkill \
  i2c-tools \
  libssl-dev libffi-dev \
  libpcap-dev \
  libjpeg-dev zlib1g-dev \
  libusb-1.0-0-dev \
  fonts-noto \
  avahi-daemon \
  dkms \
  linux-headers-$(uname -r) 2>/dev/null || true

# libpcap runtime — package name changed in Ubuntu 24.04 (noble)
apt-get install -y -qq libpcap0.8 2>/dev/null || \
  apt-get install -y -qq libpcap0.8t64 2>/dev/null || true

# ── Security tools ─────────────────────────────────────────────────────────────
log "Installing security tools..."

# Tools available in both Kali and Ubuntu apt repos
for tool_pkg in "nmap:nmap" "aircrack-ng:aircrack-ng" "hcxpcapngtool:hcxtools" "hashcat:hashcat" "traceroute:traceroute" "snmpwalk:snmp"; do
  tool="${tool_pkg%%:*}"
  pkg="${tool_pkg##*:}"
  if command -v "$tool" &>/dev/null; then
    info "  [OK] $tool"
  else
    log "  Installing $pkg..."
    apt-get install -y -qq "$pkg" 2>/dev/null && info "  [OK] $tool" || \
      warn "  [MISSING] $tool — install manually: apt-get install $pkg"
  fi
done

# bettercap — in Kali repos; Ubuntu requires downloading from GitHub releases
if command -v bettercap &>/dev/null; then
  info "  [OK] bettercap"
else
  if [ "$OS_ID" = "kali" ]; then
    log "  Installing bettercap (Kali)..."
    apt-get install -y -qq bettercap 2>/dev/null && info "  [OK] bettercap" || \
      warn "  [MISSING] bettercap — install manually: apt-get install bettercap"
  else
    log "  Downloading bettercap binary (arm64)..."
    BETTERCAP_TMP=$(mktemp -d)
    BETTERCAP_URL=$(curl -sf "https://api.github.com/repos/bettercap/bettercap/releases/latest" \
      | grep "browser_download_url.*linux_arm64.*\.zip" | cut -d'"' -f4 | head -1 || echo "")
    if [ -n "$BETTERCAP_URL" ]; then
      wget -q -O "$BETTERCAP_TMP/bettercap.zip" "$BETTERCAP_URL" && \
        unzip -q "$BETTERCAP_TMP/bettercap.zip" bettercap -d /usr/local/bin/ && \
        chmod +x /usr/local/bin/bettercap && \
        info "  [OK] bettercap (GitHub release)" || \
        warn "  [MISSING] bettercap — download from https://github.com/bettercap/bettercap/releases"
    else
      warn "  [MISSING] bettercap — could not resolve latest release URL"
      warn "            Download arm64 zip from https://github.com/bettercap/bettercap/releases"
      warn "            and place bettercap binary in /usr/local/bin/"
    fi
    rm -rf "$BETTERCAP_TMP"
  fi
fi

# ── bettercap capabilities ─────────────────────────────────────────────────────
if command -v bettercap &>/dev/null; then
  setcap cap_net_raw,cap_net_admin+eip "$(command -v bettercap)" 2>/dev/null || \
    warn "Could not set bettercap capabilities — will require sudo"
  systemctl disable --now bettercap 2>/dev/null || true
fi

# ── Monitor mode ───────────────────────────────────────────────────────────────
# DO NOT auto-build/install nexmon here. This board's radio is a Synaptics 43436s
# (and the Pi 5's is a CYW43455) — neither is nexmon-supported, and the nexmon
# brcmfmac DKMS module FAILS TO BIND the chip (probe error -110), leaving NO wlan0
# on every boot. Monitor mode (capture/deauth/rogue-AP) requires the external Alfa
# adapter regardless — see setup/install_alfa.sh.
log "Monitor mode: internal radio can't do it on this hardware — skipping nexmon."
info "  Attach a USB adapter and run: sudo bash setup/install_alfa.sh"
info "  (Only run setup/install_monitor.sh if you have a genuine nexmon-supported"
info "   BCM43430A1 — it will break wlan0 on the Synaptics 43436s.)"
# Safety: keep the firmware-nexmon package from ever being pulled in (it crashes
# the chip), and make sure no stale nexmon DKMS module is lurking from a prior run.
apt-mark hold firmware-nexmon 2>/dev/null || true
if dkms status 2>/dev/null | grep -qi "brcmfmac-nexmon"; then
  warn "A nexmon brcmfmac DKMS module is registered — it breaks wlan0 on this board."
  warn "Remove it permanently with: sudo bash setup/fix_wlan0.sh"
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

if [ -d /boot/firmware ] && ! mountpoint -q /boot/firmware 2>/dev/null; then
  mount /boot/firmware 2>/dev/null && log "Mounted /boot/firmware" || \
    warn "Could not mount /boot/firmware — boot config edits may fail"
fi

# /boot/firmware/config.txt = Ubuntu Pi; /boot/config.txt = Kali / Pi OS
CONFIG_FILE="/boot/firmware/config.txt"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_FILE" ] && CMDLINE_FILE="/boot/cmdline.txt"

if [ ! -f "$CONFIG_FILE" ]; then
  warn "No config.txt found — SPI/I2C/USB gadget config skipped"
else
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

  # USB gadget ethernet — Zero 2W / Pi 4 management crutch. The Pi 5 has real
  # Gigabit Ethernet, so gadget mode is skipped there (manage it over the LAN).
  if [ "$IS_PI5" != 1 ]; then
    sed -i '/^dtoverlay=dwc2/d' "$CONFIG_FILE"
    echo "dtoverlay=dwc2" >> "$CONFIG_FILE"
    log "dtoverlay=dwc2 set in $CONFIG_FILE"

    if [ -f "$CMDLINE_FILE" ]; then
      # Migrate g_ether → g_ncm if present (g_ncm has better macOS compatibility)
      sed -i 's/modules-load=dwc2,g_ether/modules-load=dwc2,g_ncm/' "$CMDLINE_FILE"
      if ! grep -q "modules-load=dwc2,g_ncm" "$CMDLINE_FILE"; then
        if grep -q "rootwait" "$CMDLINE_FILE"; then
          sed -i 's/rootwait/rootwait modules-load=dwc2,g_ncm/' "$CMDLINE_FILE"
        elif grep -q "console=" "$CMDLINE_FILE"; then
          sed -i 's/console=/modules-load=dwc2,g_ncm console=/' "$CMDLINE_FILE"
        else
          sed -i '1s/$/ modules-load=dwc2,g_ncm/' "$CMDLINE_FILE"
        fi
      fi
      grep -q "modules-load=dwc2,g_ncm" "$CMDLINE_FILE" && \
        log "USB gadget ethernet enabled (modules-load=dwc2,g_ncm)" || \
        warn "Could not add g_ncm to $CMDLINE_FILE — add it manually"
    fi
  else
    info "Pi 5 detected — skipping USB gadget (use the onboard Ethernet/Wi-Fi to manage)"
  fi
fi

# USB gadget networking (usb0) — Zero 2W / Pi 4 only; the Pi 5 uses real Ethernet.
if [ "$IS_PI5" = 1 ]; then
  info "Pi 5 detected — skipping usb0 gadget networking"
else
# Persistent MAC addresses for g_ncm — prevents host OS treating each reboot as new device
# g_ncm has better macOS compatibility than g_ether on Apple Silicon
rm -f /etc/modprobe.d/g_ether.conf
cat > /etc/modprobe.d/g_ncm.conf <<'EOF'
options g_ncm host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02
EOF
log "g_ncm: persistent MAC configured"

# usb0 static IP — netplan (Ubuntu) or NetworkManager (Kali)
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
    log "usb0 configured via NetworkManager (10.55.0.1, gw 10.55.0.2)" || \
    warn "Could not create usb0 NM profile — configure manually after reboot"
elif command -v netplan &>/dev/null; then
  cat > /etc/netplan/10-usb-gadget.yaml <<'EOF'
network:
  version: 2
  ethernets:
    usb0:
      dhcp4: false
      addresses:
        - 10.55.0.1/24
      routes:
        - to: default
          via: 10.55.0.2
          metric: 200
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
      optional: true
EOF
  chmod 600 /etc/netplan/10-usb-gadget.yaml
  netplan apply 2>/dev/null || true
  log "usb0 configured via netplan (10.55.0.1, gw 10.55.0.2)"
else
  warn "Neither nmcli nor netplan found — configure usb0 manually after reboot"
  warn "  Create /etc/netplan/10-usb-gadget.yaml with address 10.55.0.1/24"
fi
fi   # end USB gadget (non-Pi-5)

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

WAVESHARE_SRC=""
for candidate in \
    "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib/waveshare_epd" \
    "/opt/waveshare-epd/raspberrypi/python/lib/waveshare_epd" \
    "/opt/waveshare-epd/python/lib/waveshare_epd"; do
  [ -d "$candidate" ] && WAVESHARE_SRC="$candidate" && break
done
[ -z "$WAVESHARE_SRC" ] && [ -d "/opt/waveshare-epd" ] && \
  WAVESHARE_SRC=$(find /opt/waveshare-epd -type d -name "waveshare_epd" 2>/dev/null | head -1)

if [ -n "$WAVESHARE_SRC" ] && [ -d "$WAVESHARE_SRC" ]; then
  rm -rf "$SITE_PKG/waveshare_epd"
  cp -r "$WAVESHARE_SRC" "$SITE_PKG/waveshare_epd"
  log "waveshare_epd installed to venv (from $WAVESHARE_SRC)"
else
  warn "Waveshare library not found under /opt/waveshare-epd — display may not work"
fi

# ── rockyou wordlist ───────────────────────────────────────────────────────────
log "Setting up rockyou wordlist..."
if [ ! -f "$WORDLISTS_DIR/rockyou.txt" ]; then
  # Kali ships rockyou at /usr/share/wordlists/
  if [ -f "/usr/share/wordlists/rockyou.txt" ]; then
    cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
    log "rockyou.txt copied from /usr/share/wordlists"
  elif [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
    gunzip -k /usr/share/wordlists/rockyou.txt.gz
    cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
    log "rockyou.txt extracted from /usr/share/wordlists"
  else
    # Ubuntu: try the Kali wordlists package, then fall back to direct download
    apt-get install -y -qq wordlists 2>/dev/null || true
    if [ -f "/usr/share/wordlists/rockyou.txt.gz" ]; then
      gunzip -k /usr/share/wordlists/rockyou.txt.gz
      cp /usr/share/wordlists/rockyou.txt "$WORDLISTS_DIR/"
      log "rockyou.txt installed via wordlists package"
    else
      log "Downloading rockyou.txt..."
      wget -q --show-progress \
        -O "$WORDLISTS_DIR/rockyou.txt.gz" \
        "https://github.com/praetorian-inc/Hob0Rules/raw/master/wordlists/rockyou.txt.gz" \
        2>/dev/null && \
        gunzip "$WORDLISTS_DIR/rockyou.txt.gz" && \
        log "rockyou.txt downloaded" || \
        warn "Could not download rockyou.txt — place it manually at $WORDLISTS_DIR/rockyou.txt"
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

# Monitor mode status
DKMS_STATUS=$(dkms status brcmfmac-nexmon 2>/dev/null | head -1 || echo "")
if echo "$DKMS_STATUS" | grep -qi "installed\|built"; then
  info "  [OK] brcmfmac-nexmon DKMS"
elif [ "$OS_ID" != "kali" ]; then
  warn "  [CHECK] brcmfmac-nexmon — run: sudo bash setup/install_monitor.sh to verify"
else
  warn "  [MISSING] brcmfmac-nexmon-dkms — run: sudo bash setup/install_monitor.sh"
  ALL_OK=false
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
info "  USB SSH:    ssh ${REAL_USER}@10.55.0.1"
info "  WiFi SSH:   ssh ${REAL_USER}@radioman.local  (while not scanning)"
info "  Logs:       journalctl -u radioman -f"
info "  Dashboard:  http://radioman.local:8080"
echo ""
warn "NOTE: bettercap puts wlan0 into monitor mode — WiFi SSH drops during scanning."
warn "Use the USB cable (10.55.0.1) as your primary management connection."
echo ""
warn "PiSugar battery: run setup/install_pisugar.sh separately if needed."
echo ""
