#!/bin/bash
# Build llama-cli on a Pi 4 and transfer it to the Pi Zero 2W.
# Run on the Pi 4 via SSH:
#   bash build_llama_pi4.sh [pi_zero_ip]
#
# The binary is built with GGML_NATIVE=OFF so it runs on both Pi 4 (A72)
# and Pi Zero 2W (A53) without needing a separate cross-compile step.
#
# Usage:
#   bash build_llama_pi4.sh              # build only, copy manually
#   bash build_llama_pi4.sh 10.55.0.1   # build and scp to Zero 2W

set -e

ZERO_IP="${1:-}"
OUT_DIR="$HOME/llama_build"
LLAMA_BIN="$OUT_DIR/llama-cli"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[pi4-build]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

echo ""
log "llama.cpp builder — Pi 4 → Pi Zero 2W"
echo ""

# ── Check we're on a Pi 4 (or similar capable board) ─────────────────────────
RAM_MB=$(free -m | awk '/Mem:/{print $2}')
if [ "$RAM_MB" -lt 1024 ] 2>/dev/null; then
  warn "Less than 1GB RAM detected (${RAM_MB}MB) — this script is intended for Pi 4."
  warn "Continuing anyway, but the build may OOM."
fi
info "RAM: ${RAM_MB}MB — good for build"

# ── Install build dependencies ────────────────────────────────────────────────
log "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential cmake pkg-config git

# ── Clone llama.cpp ───────────────────────────────────────────────────────────
mkdir -p "$OUT_DIR"
TMP=$(mktemp -d -p "$OUT_DIR")
trap 'rm -rf "$TMP"' EXIT

log "Cloning llama.cpp (latest)..."
git clone --depth=1 -q https://github.com/ggerganov/llama.cpp "$TMP/llama.cpp"

LLAMA_TAG=$(git -C "$TMP/llama.cpp" describe --tags --always 2>/dev/null || echo "unknown")
info "llama.cpp version: $LLAMA_TAG"

# ── Build ─────────────────────────────────────────────────────────────────────
log "Configuring cmake..."
cmake -B "$TMP/llama.cpp/build" \
  -S "$TMP/llama.cpp" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_SERVER=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DCMAKE_C_FLAGS="-O2" \
  -DCMAKE_CXX_FLAGS="-O2" \
  -DGGML_NATIVE=OFF \
  2>/dev/null

# -j4 is safe on Pi 4 with 4GB RAM. GGML_NATIVE=OFF ensures the binary
# runs on Cortex-A53 (Zero 2W) even though we're building on Cortex-A72 (Pi 4).
CORES=$(nproc)
BUILD_JOBS=$(( CORES > 4 ? 4 : CORES ))
log "Building with -j${BUILD_JOBS} (this takes ~5-10 minutes on Pi 4)..."
cmake --build "$TMP/llama.cpp/build" \
  --config Release \
  -j"${BUILD_JOBS}" \
  2>&1 | grep -v "^\[" | tail -5

# ── Find and copy binary ──────────────────────────────────────────────────────
BUILT=$(find "$TMP/llama.cpp/build" -name "llama-cli" -type f 2>/dev/null | head -1)
[ -z "$BUILT" ] && BUILT=$(find "$TMP/llama.cpp/build" -name "main" -type f 2>/dev/null | head -1)
[ -z "$BUILT" ] && err "Build failed — llama-cli binary not found under build/"

cp "$BUILT" "$LLAMA_BIN"
chmod +x "$LLAMA_BIN"

SIZE=$(du -h "$LLAMA_BIN" | cut -f1)
log "Binary ready: $LLAMA_BIN ($SIZE)"

# Quick sanity check
if "$LLAMA_BIN" --version 2>/dev/null | grep -q "version\|llama\|build"; then
  info "Binary verified: $("$LLAMA_BIN" --version 2>/dev/null | head -1)"
fi

# ── Transfer to Zero 2W ───────────────────────────────────────────────────────
echo ""
if [ -n "$ZERO_IP" ]; then
  log "Transferring to pi@${ZERO_IP}:/opt/radioman/llama/llama-cli ..."
  ssh "pi@${ZERO_IP}" "mkdir -p /opt/radioman/llama"
  scp "$LLAMA_BIN" "pi@${ZERO_IP}:/opt/radioman/llama/llama-cli"
  ssh "pi@${ZERO_IP}" "chmod +x /opt/radioman/llama/llama-cli"
  log "Transfer complete."
  echo ""
  info "On the Zero 2W, run:"
  info "  sudo systemctl restart radioman"
else
  warn "No Zero IP given — copy manually:"
  warn "  scp $LLAMA_BIN pi@<zero_ip>:/opt/radioman/llama/llama-cli"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Build complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Binary: $LLAMA_BIN"
info "Built:  $LLAMA_TAG  (GGML_NATIVE=OFF — runs on Cortex-A53 + A72)"
echo ""
