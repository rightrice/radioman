#!/bin/bash
# build_nexmon_wsl.sh — build nexmon patched firmware on WSL2 Ubuntu, scp to Pi
#
# Run on WSL2 Ubuntu (NOT on the Pi):
#   bash scripts/build_nexmon_wsl.sh [pi-host]
#
# pi-host defaults to radioman.local. Use 10.55.0.1 if on USB.
#
# Output: brcmfmac43430-sdio.nexmon.bin copied to /tmp on the Pi.
# Then on the Pi run: sudo bash setup/install_nexmon.sh --from-file /tmp/brcmfmac43430-sdio.nexmon.bin

set -e

PI_HOST="${1:-radioman.local}"
NEXMON_DIR="/tmp/nexmon_build"
PATCH_DIR="patches/bcm43430a1/7_45_41_46/nexmon"
OUT_BIN="$NEXMON_DIR/$PATCH_DIR/brcmfmac43430-sdio.bin"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[nexmon]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

# ── Sanity check ──────────────────────────────────────────────────────────────
if uname -r | grep -qi microsoft && [ "$(uname -m)" = "aarch64" ]; then
  err "Run this on WSL2 x86_64 Ubuntu, not on the Pi"
fi

echo ""
log "Building nexmon BCM43430A1 firmware on $(uname -m) — target: $PI_HOST"
echo ""

# ── Install build dependencies ────────────────────────────────────────────────
log "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  git git-lfs \
  libgmp-dev gawk qpdf flex bison libfl-dev \
  build-essential autoconf automake libtool pkg-config \
  libssl-dev

# ── Clone nexmon ──────────────────────────────────────────────────────────────
if [ ! -d "$NEXMON_DIR/.git" ]; then
  log "Cloning seemoo-lab/nexmon..."
  git clone --depth=1 https://github.com/seemoo-lab/nexmon "$NEXMON_DIR"
else
  log "nexmon already cloned at $NEXMON_DIR — pulling..."
  git -C "$NEXMON_DIR" pull -q
fi

# Pull LFS files — nexmon stores original firmware blobs in LFS.
# These are needed to patch against (firmwares/bcm43430a1/7_45_41_46/).
log "Pulling LFS objects (firmware source blobs)..."
git -C "$NEXMON_DIR" lfs install
git -C "$NEXMON_DIR" lfs pull --include="firmwares/bcm43430a1/7_45_41_46/*,$PATCH_DIR/*"

# Verify the source firmware files are real binaries (not LFS pointers)
FW_SRC="$NEXMON_DIR/firmwares/bcm43430a1/7_45_41_46"
for f in "$FW_SRC/brcmfmac43430-sdio.bin" "$FW_SRC/ucode.bin"; do
  SIZE=$(wc -c < "$f" 2>/dev/null || echo 0)
  [ "$SIZE" -lt 1000 ] && err "LFS file too small: $f (${SIZE}B) — lfs pull may have failed"
  info "LFS ok: $(basename $f) (${SIZE} bytes)"
done

# ── Build ──────────────────────────────────────────────────────────────────────
cd "$NEXMON_DIR"

log "Setting up nexmon build environment..."
source setup_env.sh

log "Building nexmon base libraries..."
BUILD_LOG="$NEXMON_DIR/build_base.log"
set +e
make -j$(nproc) 2>&1 | tee "$BUILD_LOG" | tail -5
BUILD_EXIT=${PIPESTATUS[0]}
set -e
if [ $BUILD_EXIT -ne 0 ]; then
  tail -20 "$BUILD_LOG"
  err "Base build failed (exit $BUILD_EXIT)"
fi

PATCH_PATH="$NEXMON_DIR/$PATCH_DIR"
[ ! -d "$PATCH_PATH" ] && err "Patch directory not found: $PATCH_PATH"

log "Building patched firmware for BCM43430A1..."
cd "$PATCH_PATH"

BUILD_LOG="$NEXMON_DIR/build_patch.log"
set +e
make -j$(nproc) 2>&1 | tee "$BUILD_LOG" | tail -10
BUILD_EXIT=${PIPESTATUS[0]}
set -e

if [ $BUILD_EXIT -ne 0 ]; then
  warn "Last 30 lines of build log:"
  tail -30 "$BUILD_LOG"
  err "Patch build failed (exit $BUILD_EXIT)"
fi

# ── Verify output ─────────────────────────────────────────────────────────────
[ ! -f "$OUT_BIN" ] && err "Build did not produce firmware at $OUT_BIN"
SIZE=$(wc -c < "$OUT_BIN")
[ "$SIZE" -lt 10000 ] && err "Output firmware too small (${SIZE} bytes)"
info "Firmware built: $OUT_BIN (${SIZE} bytes)"

# ── Copy to Pi ────────────────────────────────────────────────────────────────
log "Copying firmware to pi@${PI_HOST}:/tmp/brcmfmac43430-sdio.nexmon.bin ..."
scp "$OUT_BIN" "pi@${PI_HOST}:/tmp/brcmfmac43430-sdio.nexmon.bin"

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Build complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Now on the Pi, install the firmware:"
info "  sudo bash setup/install_nexmon.sh --from-file /tmp/brcmfmac43430-sdio.nexmon.bin"
