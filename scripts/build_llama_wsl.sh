#!/bin/bash
# >>> READ scripts/BUILD_LLAMA.md FIRST — build context, gotchas, the llama-cli
#     REPL caveat, version pinning, and deploy/verify steps.
# Cross-compile llama-cli for ARM64 Linux inside WSL2 (Ubuntu).
# Run from inside WSL2:
#   bash scripts/build_llama_wsl.sh [zero2w_ip_or_hostname]
#
# Requirements (auto-installed if missing):
#   build-essential cmake git gcc-aarch64-linux-gnu g++-aarch64-linux-gnu
#
# Usage:
#   bash scripts/build_llama_wsl.sh                  # build only
#   bash scripts/build_llama_wsl.sh radioman.local   # build + scp to Zero 2W

set -e

ZERO_ADDR="${1:-}"
OUT_DIR="$HOME/llama_arm64_build"
LLAMA_BIN="$OUT_DIR/llama-cli"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[wsl-build]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

echo ""
log "llama.cpp ARM64 cross-compiler — WSL2 Ubuntu → Pi Zero 2W"
echo ""

# ── Dependencies ──────────────────────────────────────────────────────────────
log "Checking build dependencies..."
MISSING=()
for pkg in cmake git gcc-aarch64-linux-gnu g++-aarch64-linux-gnu; do
  dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done

if [ ${#MISSING[@]} -gt 0 ]; then
  log "Installing: ${MISSING[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq build-essential "${MISSING[@]}"
fi
info "Dependencies OK"

# ── Clone llama.cpp ───────────────────────────────────────────────────────────
mkdir -p "$OUT_DIR"
TMP=$(mktemp -d -p "$OUT_DIR")
trap 'rm -rf "$TMP"' EXIT

# Pinned for reproducibility — floating master drifts (a recent master shipped a
# llama-cli that runs an interactive REPL and never exits on EOF). Override with:
#   LLAMA_REF=bNNNN bash scripts/build_llama_wsl.sh   (tags: github.com/ggml-org/llama.cpp/tags)
LLAMA_REF="${LLAMA_REF:-b9451}"

log "Cloning llama.cpp @ ${LLAMA_REF}..."
git clone --depth=1 --branch "$LLAMA_REF" -q https://github.com/ggml-org/llama.cpp "$TMP/llama.cpp" \
  || err "Could not clone llama.cpp tag '$LLAMA_REF' (check it exists, or set LLAMA_REF=master)"

LLAMA_TAG="$LLAMA_REF"
info "llama.cpp version: $LLAMA_TAG (pinned)"

# ── Cross-compile for aarch64 ─────────────────────────────────────────────────
log "Configuring cmake for aarch64 cross-compilation..."
cmake -B "$TMP/llama.cpp/build" \
  -S "$TMP/llama.cpp" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_C_FLAGS="-O3 -mcpu=cortex-a53" \
  -DCMAKE_CXX_FLAGS="-O3 -mcpu=cortex-a53" \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  2>/dev/null

# WSL2 has plenty of RAM — use all available cores
CORES=$(nproc)
log "Building with -j${CORES} (cross-compile, ~5-10 min)..."

BUILD_LOG="$OUT_DIR/build.log"
set +e
cmake --build "$TMP/llama.cpp/build" \
  --config Release \
  -j"${CORES}" \
  > "$BUILD_LOG" 2>&1
BUILD_EXIT=$?
set -e

# Always show last 20 lines so errors are visible
tail -20 "$BUILD_LOG"

if [ $BUILD_EXIT -ne 0 ]; then
  err "cmake build failed (exit $BUILD_EXIT) — full log: $BUILD_LOG"
fi

# ── Locate binary ─────────────────────────────────────────────────────────────
# Search broadly — binary name changed across llama.cpp versions:
#   older:  examples/bin/llama-cli  or  build/bin/main
#   newer:  build/bin/llama  (unified app combining CLI + server)
BUILT=$(find "$TMP/llama.cpp/build" -name "llama-cli" -type f 2>/dev/null | head -1)
[ -z "$BUILT" ] && \
  BUILT=$(find "$TMP/llama.cpp/build/bin" -name "llama" -type f 2>/dev/null | head -1)
[ -z "$BUILT" ] && \
  BUILT=$(find "$TMP/llama.cpp/build" -name "main" -type f 2>/dev/null | head -1)

if [ -z "$BUILT" ]; then
  echo ""
  warn "Could not find inference binary. Executables in build/bin/:"
  find "$TMP/llama.cpp/build" -path "*/bin/*" -type f 2>/dev/null | head -20
  echo ""
  err "Binary not found — check build log at $BUILD_LOG"
fi

cp "$BUILT" "$LLAMA_BIN"
chmod +x "$LLAMA_BIN"

SIZE=$(du -h "$LLAMA_BIN" | cut -f1)
log "Binary ready: $LLAMA_BIN ($SIZE)"

# Also grab llama-server — clean HTTP /completion API, immune to the CLI's
# interactive-REPL quirks. Robust alternative if the CLI build misbehaves.
SERVER_BIN="$OUT_DIR/llama-server"
SERVER_BUILT=$(find "$TMP/llama.cpp/build" -name "llama-server" -type f 2>/dev/null | head -1)
if [ -n "$SERVER_BUILT" ]; then
  cp "$SERVER_BUILT" "$SERVER_BIN"
  chmod +x "$SERVER_BIN"
  info "Also built: $SERVER_BIN"
else
  warn "llama-server not found in build output — continuing with llama-cli only"
  SERVER_BIN=""
fi

# Verify it's actually an ARM64 ELF (not x86)
FILE_OUT=$(file "$LLAMA_BIN" 2>/dev/null || echo "")
if echo "$FILE_OUT" | grep -q "aarch64\|ARM aarch64"; then
  info "Verified: ARM64 ELF binary"
else
  warn "file output: $FILE_OUT"
  warn "Binary may not be ARM64 — check cross-compiler setup"
fi

# ── Transfer to Zero 2W ───────────────────────────────────────────────────────
# Accept "user@host" directly, else default the user (override: SSH_USER=...).
case "$ZERO_ADDR" in
  *@*) SSH_TARGET="$ZERO_ADDR" ;;
  *)   SSH_TARGET="${SSH_USER:-ubuntu}@${ZERO_ADDR}" ;;
esac

echo ""
if [ -n "$ZERO_ADDR" ]; then
  log "Transferring to ${SSH_TARGET}:/tmp/ ..."
  scp "$LLAMA_BIN" "${SSH_TARGET}:/tmp/llama-cli"
  if [ -n "$SERVER_BIN" ]; then scp "$SERVER_BIN" "${SSH_TARGET}:/tmp/llama-server"; fi
  log "Transfer complete."
  echo ""
  info "On the Zero 2W, run:"
  info "  sudo mkdir -p /opt/radioman/llama"
  info "  sudo mv /tmp/llama-cli /opt/radioman/llama/llama-cli"
  [ -n "$SERVER_BIN" ] && info "  sudo mv /tmp/llama-server /opt/radioman/llama/llama-server" || true
  info "  sudo chmod +x /opt/radioman/llama/llama-*"
  info "  sudo systemctl restart radioman"
else
  echo ""
  warn "No Zero 2W address given — copy manually (default user 'ubuntu', or pass user@host):"
  warn "  scp $LLAMA_BIN ubuntu@<zero_ip>:/tmp/llama-cli"
  warn "  ssh ubuntu@<zero_ip> 'sudo mv /tmp/llama-cli /opt/radioman/llama/llama-cli && sudo chmod +x /opt/radioman/llama/llama-cli'"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Build complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Binary: $LLAMA_BIN"
info "Target: aarch64 Linux (Cortex-A53, Pi Zero 2W)"
info "Built:  $LLAMA_TAG"
echo ""
