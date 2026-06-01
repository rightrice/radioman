#!/bin/bash
# install_monitor.sh — Wi-Fi monitor mode for Pi Zero 2W (BCM43430A1)
#
# Kali Linux:    installs brcmfmac-nexmon-dkms from the Kali repo (pre-built)
# Ubuntu Server: builds the nexmon brcmfmac driver patch from source
#
# CRITICAL: firmware-nexmon is NEVER installed — it replaces Cypress firmware
# and crashes the BCM43430A1 (chip revision mismatch). Only the kernel driver
# is patched; the stock Cypress firmware stays untouched.
#
# Usage:
#   sudo bash setup/install_monitor.sh
#
# Safe to run multiple times. Also repairs a broken nexmon install.

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
NEXMON_SRC="/opt/nexmon-src"
BCM_CHIP="bcm43430a1"
BCM_FW_VER="7_45_41_46"

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS_ID=$(grep "^ID=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "unknown")
OS_LIKE=$(grep "^ID_LIKE=" /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")

echo ""
log "OS detected: $OS_ID"

# ── Block firmware-nexmon (always — crashes BCM43430A1) ───────────────────────
apt-mark hold firmware-nexmon 2>/dev/null || true

if dpkg -l firmware-nexmon 2>/dev/null | grep -q "^ii"; then
  warn "firmware-nexmon is installed — removing it (crashes BCM43430A1)"
  apt-mark unhold firmware-nexmon 2>/dev/null || true
  apt-get remove -y firmware-nexmon 2>/dev/null || true
  apt-mark hold firmware-nexmon 2>/dev/null || true
  log "Restoring stock Cypress firmware..."
  apt-get install -y --reinstall firmware-brcm80211 2>/dev/null && \
    log "firmware-brcm80211 reinstalled" || \
    warn "Could not reinstall firmware-brcm80211 — check dmesg for firmware errors"
fi

# ── Kernel headers ────────────────────────────────────────────────────────────
KERNEL=$(uname -r)
log "Kernel: $KERNEL"

if ! dpkg -l "linux-headers-${KERNEL}" 2>/dev/null | grep -q "^ii"; then
  log "Installing kernel headers for $KERNEL..."
  apt-get install -y "linux-headers-${KERNEL}" 2>/dev/null || \
    apt-get install -y "linux-raspi-headers-${KERNEL%%-*}" 2>/dev/null || \
    apt-get install -y linux-headers-generic 2>/dev/null || \
    warn "Could not install kernel headers — DKMS build may fail"
fi

# ══════════════════════════════════════════════════════════════════════════════
#  KALI PATH — pre-built package from Kali repo
# ══════════════════════════════════════════════════════════════════════════════
if [ "$OS_ID" = "kali" ]; then
  log "Kali detected — using brcmfmac-nexmon-dkms package"

  if dpkg -l brcmfmac-nexmon-dkms 2>/dev/null | grep -q "^ii"; then
    log "brcmfmac-nexmon-dkms already installed"
    DKMS_STATUS=$(dkms status brcmfmac-nexmon 2>/dev/null | head -1 || echo "")
    if echo "$DKMS_STATUS" | grep -qi "installed\|built"; then
      info "DKMS module status: $DKMS_STATUS"
    else
      warn "DKMS module not built — rebuilding for kernel $KERNEL..."
      NEXMON_VER=$(dkms status brcmfmac-nexmon 2>/dev/null \
        | grep -o '[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' | head -1 || echo "")
      if [ -n "$NEXMON_VER" ]; then
        dkms build   -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" 2>/dev/null && \
        dkms install -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" 2>/dev/null && \
          log "DKMS module rebuilt" || \
          warn "DKMS rebuild failed — check: dkms status && dmesg | grep nexmon"
      fi
    fi
  else
    log "Installing brcmfmac-nexmon-dkms..."
    apt-get update -qq
    apt-get install -y brcmfmac-nexmon-dkms || \
      err "brcmfmac-nexmon-dkms install failed — run: apt-get update && apt-get install brcmfmac-nexmon-dkms"
    log "brcmfmac-nexmon-dkms installed"
  fi

# ══════════════════════════════════════════════════════════════════════════════
#  UBUNTU PATH — build nexmon brcmfmac driver from source
# ══════════════════════════════════════════════════════════════════════════════
else
  log "Ubuntu/Debian detected — building nexmon brcmfmac driver from source"
  echo ""
  info "This takes ~10-15 minutes on a Pi Zero 2W. Go make a coffee."
  echo ""

  # ── Build dependencies ──────────────────────────────────────────────────────
  log "Installing build dependencies..."
  apt-get install -y \
    git libgmp3-dev gawk qpdf flex bison libfl-dev \
    build-essential cmake automake autoconf libtool texinfo \
    python3 dkms bc gcc-arm-none-eabi 2>/dev/null || \
    warn "Some build deps may be missing — continuing anyway"

  # ── Clone nexmon ────────────────────────────────────────────────────────────
  if [ -d "$NEXMON_SRC/.git" ]; then
    log "Updating existing nexmon source..."
    git -C "$NEXMON_SRC" pull --depth=1 -q 2>/dev/null || \
      warn "Could not update nexmon source — using existing checkout"
  else
    log "Cloning nexmon..."
    rm -rf "$NEXMON_SRC"
    git clone --depth=1 -q https://github.com/seemoo-lab/nexmon.git "$NEXMON_SRC"
  fi

  NEXMON_TAG=$(git -C "$NEXMON_SRC" describe --tags --always 2>/dev/null || echo "1.0.0")
  info "nexmon version: $NEXMON_TAG"

  # ── Fix nexmon toolchain for aarch64 ───────────────────────────────────────
  # Nexmon ships a prebuilt arm-none-eabi-gcc for armv7l (32-bit ARM).
  # On aarch64 Ubuntu this binary cannot execute. Replace it with the system
  # gcc-arm-none-eabi which is built for the host architecture.
  SYSTEM_GCC=$(command -v arm-none-eabi-gcc 2>/dev/null || echo "")
  if [ -n "$SYSTEM_GCC" ]; then
    TC_DIR=$(find "$NEXMON_SRC/buildtools" -maxdepth 1 -type d -name "gcc-arm-none-eabi*armv7l" 2>/dev/null | head -1)
    if [ -n "$TC_DIR" ]; then
      for bin in arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-ld arm-none-eabi-objcopy arm-none-eabi-strip; do
        BUNDLED="$TC_DIR/bin/$bin"
        SYSTEM_BIN=$(command -v "$bin" 2>/dev/null || echo "")
        if [ -f "$BUNDLED" ] && [ -n "$SYSTEM_BIN" ]; then
          mv "$BUNDLED" "${BUNDLED}.orig" 2>/dev/null || true
          ln -sf "$SYSTEM_BIN" "$BUNDLED"
        fi
      done
      log "Nexmon toolchain patched for aarch64 (using system gcc-arm-none-eabi)"
    fi
  else
    warn "gcc-arm-none-eabi not found — firmware compilation may fail"
  fi

  # ── Verify BCM43430A1 patch is present ─────────────────────────────────────
  PATCH_DIR="$NEXMON_SRC/patches/$BCM_CHIP/$BCM_FW_VER/nexmon"
  [ -d "$PATCH_DIR" ] || err "BCM43430A1 patch not found at $PATCH_DIR — check nexmon repo"

  # ── Build nexmon base tools ─────────────────────────────────────────────────
  log "Building nexmon base tools..."
  set +e
  (
    cd "$NEXMON_SRC"
    source setup_env.sh 2>/dev/null
    make -C buildtools 2>/dev/null
  )
  set -e

  # ── Build BCM43430A1 driver patch ───────────────────────────────────────────
  log "Building BCM43430A1 ($BCM_FW_VER) driver patch..."
  BUILD_LOG="/tmp/nexmon_build.log"
  set +e
  (
    cd "$NEXMON_SRC"
    source setup_env.sh 2>/dev/null
    cd "$PATCH_DIR"
    make 2>&1 | tee "$BUILD_LOG" | tail -5
  )
  BUILD_EXIT=$?
  set -e

  if [ $BUILD_EXIT -ne 0 ]; then
    warn "Patch build returned non-zero — checking for brcmfmac driver source anyway"
  fi

  # ── Find patched brcmfmac driver source ────────────────────────────────────
  # Prefer the driver version closest to (and not newer than) the running kernel.
  # Order matters: kernel 6.8 → try 6.6.y first, then descend. Newer driver
  # source means fewer kernel API mismatches (e.g. WLC_*→BRCM_* rename).
  KERNEL_MINOR=$(uname -r | grep -o '^[0-9]*\.[0-9]*' || echo "6.8")
  BRCMFMAC_SRC=""
  for candidate in \
    "$NEXMON_SRC/patches/driver/brcmfmac_${KERNEL_MINOR}.y-nexmon" \
    "$NEXMON_SRC/patches/driver/brcmfmac_6.6.y-nexmon" \
    "$NEXMON_SRC/patches/driver/brcmfmac_6.2.y-nexmon" \
    "$NEXMON_SRC/patches/driver/brcmfmac_6.1.y-nexmon" \
    "$NEXMON_SRC/patches/driver/brcmfmac_5.15.y-nexmon" \
    "$NEXMON_SRC/patches/driver/brcmfmac_5.10.y-nexmon"
  do
    if [ -d "$candidate" ]; then
      BRCMFMAC_SRC="$candidate"
      break
    fi
  done

  # Also search broadly as a last resort — pick highest version available
  if [ -z "$BRCMFMAC_SRC" ]; then
    BRCMFMAC_SRC=$(find "$NEXMON_SRC/patches/driver" -maxdepth 1 -type d -name "brcmfmac*nexmon*" 2>/dev/null \
      | sort -V | tail -1)
  fi

  if [ -z "$BRCMFMAC_SRC" ] || [ ! -d "$BRCMFMAC_SRC" ]; then
    warn "Could not locate patched brcmfmac driver source."
    warn "Check: ls $NEXMON_SRC/patches/driver/"
    warn "Then re-run: sudo bash setup/install_monitor.sh"
  else
    info "Patched brcmfmac driver source: $BRCMFMAC_SRC"

    # Use a clean semver for DKMS — extract from tag or default to 1.0.0
    NEXMON_VER=$(echo "$NEXMON_TAG" | grep -o '[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' | head -1 || true)
    [ -z "$NEXMON_VER" ] && NEXMON_VER="1.0.0"

    # Remove any previous DKMS registration
    dkms remove brcmfmac-nexmon/"$NEXMON_VER" --all 2>/dev/null || true

    DKMS_DIR="/usr/src/brcmfmac-nexmon-${NEXMON_VER}"
    rm -rf "$DKMS_DIR"
    cp -r "$BRCMFMAC_SRC" "$DKMS_DIR"

    # Nexmon's Makefile uses $(NEXMON_ROOT) which is only set when
    # setup_env.sh is sourced — not available during DKMS builds. Replace
    # with $(src) which resolves to the DKMS build directory at compile time.
    # The driver already ships include/defs.h; this makes the path work.
    log "Patching DKMS Makefile: replacing NEXMON_ROOT with \$(src)..."
    NEXMON_MK="$DKMS_DIR/Makefile"
    BRCMFMAC_DIR_NAME="$(basename "$BRCMFMAC_SRC")"
    if [ -f "$NEXMON_MK" ] && grep -q 'NEXMON_ROOT' "$NEXMON_MK"; then
      sed -i \
        -e "s|-I\$(NEXMON_ROOT)/patches/driver/${BRCMFMAC_DIR_NAME}/include|-I\$(src)/include|g" \
        -e "s|-I\$(NEXMON_ROOT)/patches/driver/${BRCMFMAC_DIR_NAME}|-I\$(src)|g" \
        "$NEXMON_MK"
      info "Patched Makefile: NEXMON_ROOT include paths → \$(src)"
    elif [ -f "$NEXMON_MK" ] && ! grep -q '\-I\$(src)' "$NEXMON_MK"; then
      sed -i '1s/^/ccflags-y += -I$(src) -I$(src)\/include\n/' "$NEXMON_MK"
      info "Patched Makefile: added -I\$(src) and -I\$(src)/include"
    fi

    # brcmu_utils.h, brcmu_wifi.h, brcmu_d11.h, defs.h all live in the nexmon
    # driver source's include/ subdirectory — they're reachable via -I$(src)/include
    # from the Makefile patch above. No fetch needed.

    # ── Patch driver source for kernel 6.7+ API changes ────────────────────────
    # nexmon's newest driver targets 6.6.y. Kernel 6.7 made two breaking changes
    # to cfg80211 that the 6.6.y source can't compile against:
    #   1. cfg80211_inform_bss lost its .scan_width member
    #   2. .change_beacon callback takes cfg80211_ap_update* instead of
    #      cfg80211_beacon_data*
    # Both are guarded with LINUX_VERSION_CODE so the source still builds on
    # older kernels. Idempotent — DKMS_DIR is a fresh copy each run.
    CFG="$DKMS_DIR/cfg80211.c"
    if [ -f "$CFG" ]; then
      log "Patching cfg80211.c for kernel 6.7+ API changes..."
      python3 - "$CFG" <<'PYEOF' && info "cfg80211.c patched" || warn "cfg80211.c patch step had issues — build may still fail"
import re, sys
path = sys.argv[1]
with open(path) as f:
    src = f.read()
changed = False

# Ensure LINUX_VERSION_CODE / KERNEL_VERSION are available.
if '#include <linux/version.h>' not in src:
    src = re.sub(r'(#include [<"][^>"]+[>"]\n)',
                 r'\1#include <linux/version.h>\n', src, count=1)
    changed = True

# Patch 1 — guard scan_width (removed in 6.7).
m = re.search(r'^([ \t]*)bss_data\.scan_width = NL80211_BSS_CHAN_WIDTH_20;\s*$',
              src, re.M)
if m and 'scan_width' in src and '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 7, 0)\n' + m.group(0) not in src:
    line = m.group(0)
    src = src.replace(line,
        '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 7, 0)\n' + line + '\n#endif', 1)
    changed = True

# Patch 2 — change_beacon signature (cfg80211_beacon_data -> cfg80211_ap_update).
m = re.search(
    r'(brcmf_cfg80211_change_beacon\(struct wiphy \*wiphy, struct net_device \*ndev,\n)'
    r'([ \t]*)struct cfg80211_beacon_data \*info\)\n\{\n',
    src)
if m and 'info_update' not in src:
    indent = m.group(2)
    repl = (m.group(1)
        + '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 7, 0)\n'
        + indent + 'struct cfg80211_beacon_data *info)\n{\n'
        + '#else\n'
        + indent + 'struct cfg80211_ap_update *info_update)\n{\n'
        + '\tstruct cfg80211_beacon_data *info = &info_update->beacon;\n'
        + '#endif\n')
    src = src[:m.start()] + repl + src[m.end():]
    changed = True

if changed:
    with open(path, 'w') as f:
        f.write(src)
    print("patched")
else:
    print("no changes applied")
PYEOF
    fi

    cat > "$DKMS_DIR/dkms.conf" <<EOF
PACKAGE_NAME="brcmfmac-nexmon"
PACKAGE_VERSION="$NEXMON_VER"
BUILT_MODULE_NAME[0]="brcmfmac"
DEST_MODULE_LOCATION[0]="/kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac"
AUTOINSTALL="yes"
EOF

    log "Registering brcmfmac-nexmon-$NEXMON_VER with DKMS..."
    dkms add -m brcmfmac-nexmon -v "$NEXMON_VER" 2>/dev/null || true

    BUILD_LOG="/var/lib/dkms/brcmfmac-nexmon/$NEXMON_VER/build/make.log"
    if dkms build -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" 2>/dev/null && \
       dkms install -m brcmfmac-nexmon -v "$NEXMON_VER" -k "$KERNEL" --force 2>/dev/null; then
      log "brcmfmac-nexmon DKMS module installed"

      # ── Pin the kernel ───────────────────────────────────────────────────────
      # nexmon has no driver for kernels newer than 6.6.y. This module is
      # patched specifically for $KERNEL. If apt upgrades the kernel, the
      # module won't match and monitor mode breaks. Hold the kernel packages.
      log "Pinning kernel $KERNEL so apt upgrades can't rebreak monitor mode..."
      HELD=""
      for pkg in linux-image-raspi linux-headers-raspi linux-raspi \
                 "linux-image-${KERNEL}" "linux-headers-${KERNEL}" \
                 "linux-modules-${KERNEL}"; do
        if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
          apt-mark hold "$pkg" >/dev/null 2>&1 && HELD="$HELD $pkg"
        fi
      done
      if [ -n "$HELD" ]; then
        info "Held:$HELD"
        info "To allow kernel upgrades later: sudo apt-mark unhold$HELD"
      else
        warn "Could not hold kernel packages — run manually:"
        warn "  sudo apt-mark hold linux-image-raspi linux-headers-raspi linux-raspi"
      fi
    else
      warn "DKMS build failed for kernel $KERNEL."
      warn "The driver source ($(basename $BRCMFMAC_SRC)) may not be compatible with kernel $KERNEL."
      [ -f "$BUILD_LOG" ] && warn "Build log: $BUILD_LOG" && tail -40 "$BUILD_LOG" || true
    fi
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
#  COMMON — reload driver and verify monitor mode
# ══════════════════════════════════════════════════════════════════════════════

log "Reloading brcmfmac driver..."
ip link set "$MON_IFACE" down 2>/dev/null || true
iw dev "$MON_IFACE" del 2>/dev/null || true
modprobe -r brcmfmac brcmutil 2>/dev/null || true
sleep 3
modprobe brcmfmac 2>/dev/null || true
sleep 4

if ! ip link show "$IFACE" &>/dev/null; then
  err "$IFACE not found after driver reload — check: dmesg | grep -i brcm"
fi
info "$IFACE is present"

# ── Test monitor mode ─────────────────────────────────────────────────────────
log "Testing monitor mode..."

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

if command -v airmon-ng &>/dev/null; then
  AIRMON_OUT=$(airmon-ng 2>/dev/null | grep "$IFACE" || echo "")
  echo "$AIRMON_OUT" | grep -q "monitor" && \
    info "airmon-ng reports $IFACE supports monitor mode"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Monitor mode setup complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "OS:     $OS_ID"
info "Kernel: $KERNEL"
info "Chip:   BCM43430A1 ($BCM_FW_VER)"
info "Iface:  $IFACE → $MON_IFACE (created automatically when scanning starts)"
echo ""

if $VDEV_OK; then
  log "Monitor mode: WORKING"
else
  warn "Monitor mode test failed."
  warn "If the kernel just changed, try: sudo reboot"
  warn "Then re-run: sudo bash setup/install_monitor.sh"
  warn "For manual diagnosis: dmesg | grep -i 'brcm\|monitor\|nexmon'"
fi
echo ""
