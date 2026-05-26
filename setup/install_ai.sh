#!/bin/bash
# radioman AI installer
# Downloads pre-built llama-cli for ARM64 and IBM Granite 1B Q2_K GGUF.
# Run as root AFTER install.sh: sudo bash setup/install_ai.sh
#
# Disk: ~5MB binary + ~400MB model
# RAM during inference: model is loaded per-request by llama-cli subprocess

set -e

RADIOMAN_DIR="/opt/radioman"
LLAMA_DIR="$RADIOMAN_DIR/llama"
MODEL_DIR="$RADIOMAN_DIR/models"
MODEL_FILE="$MODEL_DIR/granite.gguf"
LLAMA_BIN="$LLAMA_DIR/llama-cli"

# Model: IBM Granite 3.2 1B A400M Instruct at Q2_K quantization (~400MB)
MODEL_REPO="bartowski/granite-3.2-1b-a400m-instruct-GGUF"
MODEL_FILENAME="granite-3.2-1b-a400m-instruct-Q2_K.gguf"
HF_BASE="https://huggingface.co"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[ai-install]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_ai.sh"
[ ! -d "$RADIOMAN_DIR" ] && err "radioman not installed at $RADIOMAN_DIR — run setup/install.sh first"

log "Starting radioman AI installation..."
echo ""

mkdir -p "$LLAMA_DIR" "$MODEL_DIR"

# ── llama-cli binary ──────────────────────────────────────────────────────────
if [ -f "$LLAMA_BIN" ]; then
  info "llama-cli already present at $LLAMA_BIN"
else
  log "Fetching latest llama.cpp release tag..."

  # Follow the redirect on /releases/latest to get the tag — no API key or JSON parsing needed
  LATEST_URL=$(curl -sf -o /dev/null -w "%{url_effective}" -L \
    "https://github.com/ggerganov/llama.cpp/releases/latest" 2>/dev/null || echo "")
  RELEASE_TAG=$(basename "$LATEST_URL")

  if [ -z "$RELEASE_TAG" ] || [ "$RELEASE_TAG" = "latest" ]; then
    err "Could not determine latest llama.cpp release — check internet connection"
  fi

  info "Latest release: $RELEASE_TAG"

  # Probe known ARM64 asset URL patterns with HEAD requests — no API needed.
  # llama.cpp has used several naming conventions across release series.
  ASSET_URL=""
  BASE="https://github.com/ggerganov/llama.cpp/releases/download/${RELEASE_TAG}"
  for CANDIDATE in \
    "${BASE}/llama-${RELEASE_TAG}-bin-ubuntu-arm64.zip" \
    "${BASE}/llama-${RELEASE_TAG}-bin-linux-arm64.zip" \
    "${BASE}/llama-${RELEASE_TAG}-bin-ubuntu-aarch64.zip" \
    "${BASE}/llama-${RELEASE_TAG}-bin-linux-aarch64.zip"
  do
    STATUS=$(curl -sf -o /dev/null -w "%{http_code}" -L --max-time 10 "$CANDIDATE" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
      ASSET_URL="$CANDIDATE"
      break
    fi
  done

  if [ -z "$ASSET_URL" ]; then
    warn "No pre-built ARM64 binary found for $RELEASE_TAG."
    warn "llama.cpp may not ship Linux ARM64 binaries for this release."
    warn "Falling back to build from source (takes 30-60 min on Pi Zero 2W)."
    echo ""
    BUILD_FROM_SOURCE=true
  else
    info "Asset: $(basename "$ASSET_URL")"
    BUILD_FROM_SOURCE=false
  fi

  # Use /var/tmp (disk-backed) not /tmp (tmpfs/RAM) — llama.cpp build produces
  # several GB of object files that will exhaust RAM-backed /tmp on a Pi Zero 2W.
  TMP=$(mktemp -d -p /var/tmp)
  trap 'rm -rf "$TMP"' EXIT

  if $BUILD_FROM_SOURCE; then
    # ── Build from source fallback ───────────────────────────────────────────
    apt-get install -y -qq build-essential cmake pkg-config

    # llama-model.cpp needs ~1.5GB to compile — we need disk-backed swap.
    # zram (in-memory) does not help here; create a real swapfile on the SD card.
    BUILD_SWAPFILE="/swapfile_llama_build"
    TOTAL_DISK_SWAP=$(swapon --show=NAME,TYPE --noheadings 2>/dev/null | grep -v zram | wc -l)

    if [ "$TOTAL_DISK_SWAP" -eq 0 ]; then
      log "Creating 2GB disk-backed swapfile for build (zram alone is not enough)..."
      fallocate -l 2G "$BUILD_SWAPFILE" 2>/dev/null || \
        dd if=/dev/zero of="$BUILD_SWAPFILE" bs=1M count=2048 status=progress
      chmod 600 "$BUILD_SWAPFILE"
      mkswap "$BUILD_SWAPFILE"
      swapon "$BUILD_SWAPFILE"
      SWAPFILE_CREATED=true
      info "Disk swap added: $(free -h | awk '/Swap:/{print $2}') total"
    else
      SWAPFILE_CREATED=false
      info "Disk-backed swap already present"
    fi

    log "Cloning llama.cpp..."
    git clone --depth=1 -q https://github.com/ggerganov/llama.cpp "$TMP/llama.cpp"

    log "Building llama-cli (-j1, low priority — takes ~30-60 min)..."
    cmake -B "$TMP/llama.cpp/build" -S "$TMP/llama.cpp" \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=OFF \
      -DLLAMA_BUILD_SERVER=OFF \
      -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_EXAMPLES=ON \
      -DCMAKE_C_FLAGS="-O2" \
      -DCMAKE_CXX_FLAGS="-O2" \
      -DGGML_NATIVE=OFF \
      2>/dev/null

    nice -n 15 ionice -c 3 \
      cmake --build "$TMP/llama.cpp/build" --config Release -j1 \
      2>&1 | grep -v "^\[" | tail -5

    LLAMA_BIN_FOUND=$(find "$TMP/llama.cpp/build" -name "llama-cli" -type f 2>/dev/null | head -1)
    [ -z "$LLAMA_BIN_FOUND" ] && \
      LLAMA_BIN_FOUND=$(find "$TMP/llama.cpp/build" -name "main" -type f 2>/dev/null | head -1)
    [ -z "$LLAMA_BIN_FOUND" ] && err "Build failed — llama-cli binary not found"

    cp "$LLAMA_BIN_FOUND" "$LLAMA_BIN"
    chmod +x "$LLAMA_BIN"
    log "llama-cli built and installed to $LLAMA_BIN"

    # Remove the temporary build swapfile — not needed after compile
    if ${SWAPFILE_CREATED:-false}; then
      swapoff "$BUILD_SWAPFILE" 2>/dev/null || true
      rm -f "$BUILD_SWAPFILE"
      info "Build swapfile removed"
    fi
  else
    # ── Download pre-built binary ────────────────────────────────────────────
    log "Downloading: $(basename "$ASSET_URL")"
    if ! wget -q --show-progress -O "$TMP/llama.zip" "$ASSET_URL" 2>&1; then
      curl -L --progress-bar -o "$TMP/llama.zip" "$ASSET_URL" \
        || err "Download failed. Check internet connection and try again."
    fi

    log "Extracting llama-cli..."
    unzip -q "$TMP/llama.zip" -d "$TMP/llama_extracted"

    LLAMA_BIN_FOUND=$(find "$TMP/llama_extracted" -name "llama-cli" -type f 2>/dev/null | head -1)
    [ -z "$LLAMA_BIN_FOUND" ] && \
      LLAMA_BIN_FOUND=$(find "$TMP/llama_extracted" -name "main" -type f 2>/dev/null | head -1)

    if [ -z "$LLAMA_BIN_FOUND" ]; then
      warn "Contents of zip:"
      find "$TMP/llama_extracted" -type f | head -20
      err "Could not find llama-cli binary in the downloaded zip"
    fi

    cp "$LLAMA_BIN_FOUND" "$LLAMA_BIN"
    chmod +x "$LLAMA_BIN"
    log "llama-cli installed to $LLAMA_BIN"

    if "$LLAMA_BIN" --version 2>/dev/null | grep -q "version\|llama"; then
      info "Binary verified: $("$LLAMA_BIN" --version 2>/dev/null | head -1)"
    else
      warn "Binary did not respond to --version — may still work, continuing"
    fi
  fi
fi

# ── Download IBM Granite GGUF model ───────────────────────────────────────────
if [ -f "$MODEL_FILE" ] && [ "$(stat -c%s "$MODEL_FILE" 2>/dev/null || echo 0)" -gt 100000000 ]; then
  info "Granite model already present at $MODEL_FILE"
else
  log "Downloading IBM Granite 3.2 1B A400M Instruct (Q2_K, ~400MB)..."
  info "This may take 5-15 minutes depending on your connection."

  MODEL_URL="${HF_BASE}/${MODEL_REPO}/resolve/main/${MODEL_FILENAME}"

  if wget -q --show-progress -O "$MODEL_FILE.tmp" "$MODEL_URL" 2>&1; then
    mv "$MODEL_FILE.tmp" "$MODEL_FILE"
    log "Model downloaded: $MODEL_FILE"
  elif curl -L --progress-bar -o "$MODEL_FILE.tmp" "$MODEL_URL"; then
    mv "$MODEL_FILE.tmp" "$MODEL_FILE"
    log "Model downloaded: $MODEL_FILE"
  else
    rm -f "$MODEL_FILE.tmp"
    err "Model download failed. Check internet connection and try again."
  fi
fi

# ── Smoke test ────────────────────────────────────────────────────────────────
log "Running smoke test (may take ~60 seconds on first load)..."
TEST_OUT=$("$LLAMA_BIN" \
  --model "$MODEL_FILE" \
  --threads 4 \
  --ctx-size 64 \
  --n-predict 20 \
  --temp 0.1 \
  --no-display-prompt \
  --log-disable \
  --prompt "<|user|>
Say hello.
<|assistant|>" 2>/dev/null || echo "")

if [ -z "$TEST_OUT" ]; then
  warn "Smoke test produced no output — model may still work for longer prompts."
  warn "Run manually: $LLAMA_BIN --model $MODEL_FILE --prompt \"hello\" --n-predict 20"
else
  log "Smoke test OK: \"${TEST_OUT:0:80}\""
fi

# ── Update radioman.conf with AI paths ────────────────────────────────────────
CONF="$RADIOMAN_DIR/radioman.conf"
if [ -f "$CONF" ]; then
  if ! grep -q '^\[ai\]' "$CONF"; then
    cat >> "$CONF" <<'EOF'

[ai]
# Path to llama-cli binary (set by install_ai.sh)
llama_cli = /opt/radioman/llama/llama-cli
# Path to GGUF model file
model = /opt/radioman/models/granite.gguf
EOF
    log "Added [ai] section to radioman.conf"
  else
    info "[ai] section already present in radioman.conf"
  fi
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " AI installation complete!"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Model:   $MODEL_FILE"
info "Binary:  $LLAMA_BIN"
echo ""
info "Restart radioman to activate the AI assistant:"
info "  systemctl restart radioman"
echo ""
warn "Inference on Pi Zero 2W takes 1-3 minutes per response."
warn "Use the AI chat tab in the dashboard at http://radioman.local:8080"
echo ""
