#!/bin/bash
# tune.sh — reduce resource usage on Pi Zero 2W (Ubuntu Server)
# Disables unused system services and applies memory/IO tweaks.
# Safe to run multiple times. Does not touch radioman dependencies.
#
# Usage: sudo bash setup/tune.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[tune]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && { echo "Please run as root: sudo bash setup/tune.sh"; exit 1; }

echo ""
log "Tuning Ubuntu Server for Pi Zero 2W / radioman..."
echo ""

# ── Helper ────────────────────────────────────────────────────────────────────
disable_service() {
  local svc="$1"
  if systemctl list-unit-files "$svc" 2>/dev/null | grep -q "$svc"; then
    systemctl disable --now "$svc" 2>/dev/null && info "  disabled: $svc" || true
    systemctl mask "$svc" 2>/dev/null || true
  else
    info "  not present: $svc (skip)"
  fi
}

# ── snapd ─────────────────────────────────────────────────────────────────────
# snapd polls for updates, mounts snap loop devices, and uses ~40MB RAM.
# Only disable if no snaps are installed.
log "snapd..."
SNAP_COUNT=$(snap list 2>/dev/null | tail -n +2 | wc -l || echo 0)
if [ "$SNAP_COUNT" -eq 0 ]; then
  apt-get remove --purge -y snapd 2>/dev/null || true
  rm -rf /snap /var/snap /var/lib/snapd /var/cache/snapd /root/snap 2>/dev/null || true
  # Prevent apt from reinstalling it
  cat > /etc/apt/preferences.d/no-snapd <<'EOF'
Package: snapd
Pin: release *
Pin-Priority: -1
EOF
  log "snapd removed and pinned to never reinstall"
else
  warn "snapd skipped — $SNAP_COUNT snap(s) installed. Remove them first if you want to purge snapd."
fi

# ── cloud-init ────────────────────────────────────────────────────────────────
# Only needed for cloud VM provisioning. Slows boot and wastes RAM on Pi.
log "cloud-init..."
disable_service cloud-init.service
disable_service cloud-init-local.service
disable_service cloud-config.service
disable_service cloud-final.service
# Prevent cloud-init from running on next boot
touch /etc/cloud/cloud-init.disabled 2>/dev/null || true

# ── Unattended upgrades + apt timers ─────────────────────────────────────────
# These cause random CPU + IO spikes. We update manually.
log "Unattended upgrades / apt timers..."
disable_service unattended-upgrades.service
disable_service apt-daily.timer
disable_service apt-daily-upgrade.timer
disable_service apt-daily.service
disable_service apt-daily-upgrade.service

# ── Ubuntu-specific daemons ───────────────────────────────────────────────────
log "Ubuntu-specific daemons..."
disable_service ua-messaging.service       2>/dev/null || true
disable_service ubuntu-advantage.service   2>/dev/null || true
disable_service update-notifier.service    2>/dev/null || true
disable_service motd-news.timer            2>/dev/null || true
disable_service apport.service             2>/dev/null || true

# ── Hardware daemons not used on Zero 2W ─────────────────────────────────────
log "Unused hardware daemons..."
disable_service bluetooth.service
disable_service ModemManager.service
disable_service multipathd.service
disable_service multipathd.socket

# ── Limit journald size ───────────────────────────────────────────────────────
# Default is no limit — can eat hundreds of MB on an SD card.
log "Journald size limit..."
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/radioman.conf <<'EOF'
[Journal]
SystemMaxUse=50M
RuntimeMaxUse=20M
EOF
systemctl restart systemd-journald 2>/dev/null || true
info "  journald capped at 50MB persistent / 20MB runtime"

# ── Sysctl tweaks ─────────────────────────────────────────────────────────────
log "Kernel / VM tweaks..."
cat > /etc/sysctl.d/99-radioman.conf <<'EOF'
# Reduce swap aggressiveness — prefer RAM, only swap under real pressure
vm.swappiness=10

# Keep more filesystem cache rather than dropping it
vm.vfs_cache_pressure=50

# Larger socket buffers for packet capture (bettercap/tcpdump)
net.core.rmem_max=8388608
net.core.wmem_max=8388608
EOF
sysctl -p /etc/sysctl.d/99-radioman.conf >/dev/null 2>&1
info "  vm.swappiness=10, vfs_cache_pressure=50, rmem/wmem_max=8MB"

# ── GPU memory ────────────────────────────────────────────────────────────────
# Reduce GPU memory to 16MB (minimum) — Linux doesn't use the GPU framebuffer.
# Frees RAM for radioman and the kernel.
log "GPU memory..."
CONFIG_FILE=""
for f in /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$f" ] && CONFIG_FILE="$f" && break
done

if [ -n "$CONFIG_FILE" ]; then
  if grep -q "^gpu_mem=" "$CONFIG_FILE"; then
    sed -i 's/^gpu_mem=.*/gpu_mem=16/' "$CONFIG_FILE"
  else
    echo "gpu_mem=16" >> "$CONFIG_FILE"
  fi
  info "  gpu_mem=16 set in $CONFIG_FILE"
else
  warn "  config.txt not found — set gpu_mem=16 manually"
fi

# ── earlyoom (out-of-memory killer) ──────────────────────────────────────────
# The kernel OOM killer is brutal — it can kill radioman mid-scan.
# earlyoom kills the largest non-critical process at 80% RAM (before kernel OOM).
log "earlyoom..."
if ! command -v earlyoom &>/dev/null; then
  apt-get install -y -qq earlyoom 2>/dev/null && info "  earlyoom installed" || \
    warn "  earlyoom not available — kernel OOM killer will be used instead"
fi
if command -v earlyoom &>/dev/null; then
  # Trigger at 10% free RAM / 5% free swap
  cat > /etc/default/earlyoom <<'EOF'
EARLYOOM_ARGS="-m 10 -s 5 --avoid '(radioman|python3|bettercap)' --prefer '(llama-cli|hashcat)'"
EOF
  systemctl enable --now earlyoom 2>/dev/null || true
  info "  earlyoom enabled (protects radioman/bettercap, prefers killing llama-cli/hashcat)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Tuning complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "What was done:"
info "  • snapd removed (if no snaps were installed)"
info "  • cloud-init disabled and masked"
info "  • unattended-upgrades + apt timers disabled"
info "  • bluetooth, ModemManager, multipathd disabled"
info "  • journald capped at 50MB"
info "  • vm.swappiness=10, socket buffers enlarged"
info "  • gpu_mem=16 (frees ~48MB RAM vs default 64MB)"
info "  • earlyoom protecting radioman/bettercap from OOM"
echo ""
warn "REBOOT REQUIRED for gpu_mem and cloud-init changes to take full effect."
warn "Run: sudo reboot"
echo ""
