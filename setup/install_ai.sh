#!/bin/bash
# radioman AI installer
# Builds llama.cpp for ARM64 and downloads IBM Granite 1B Q2_K GGUF.
# Run as root AFTER install.sh: sudo bash setup/install_ai.sh
#
# Disk: ~1.5GB for build deps + model (~400MB) + llama.cpp binary
# RAM during build: ~200MB  (build takes 15-30 min on Pi Zero 2W)
# RAM during inference: model is loaded per-request by llama-cli subprocess

set -e

RADIOMAN_DIR="/opt/radioman"
LLAMA_DIR="$RADIOMAN_DIR/llama"
MODEL_DIR="$RADIOMAN_DIR/models"
MODEL_FILE="$MODEL_DIR/granite.gguf"
LLAMA_BIN="$LLAMA_DIR/llama-cli"

# Model: IBM Granite 3.2 1B A400M Instruct at Q2_K quantization (~400MB)
# Hosted by community GGUF provider on HuggingFace
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
warn "This will take 15-30 minutes on a Pi Zero 2W."
warn "The model download is ~400MB. Ensure you have a stable internet connection."
echo ""

mkdir -p "$LLAMA_DIR" "$MODEL_DIR"

# ── Build llama.cpp ────────────────────────────────────────────────────────────
if [ -f "$LLAMA_BIN" ]; then
  info "llama-cli already present at $LLAMA_BIN"
else
  log "Installing llama.cpp build dependencies..."
  apt-get update -qq
  apt-get install -y -qq build-essential cmake pkg-config

  log "Cloning llama.cpp (latest release)..."
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT

  git clone --depth=1 -q https://github.com/ggerganov/llama.cpp "$TMP/llama.cpp"

  log "Building llama-cli for ARM64 (this takes ~20 minutes on Pi Zero 2W)..."
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

  # Build all default targets — target name varies across llama.cpp versions
  cmake --build "$TMP/llama.cpp/build" \
    --config Release \
    -j2 \
    2>&1 | grep -v "^\[" | tail -30

  # Find the binary wherever cmake put it (location varies by version)
  LLAMA_BIN_BUILT=$(find "$TMP/llama.cpp/build" -name "llama-cli" -type f 2>/dev/null | head -1)

  if [ -z "$LLAMA_BIN_BUILT" ]; then
    # Older llama.cpp used 'main' as the CLI binary name
    LLAMA_BIN_BUILT=$(find "$TMP/llama.cpp/build" -name "main" -type f 2>/dev/null | head -1)
  fi

  if [ -z "$LLAMA_BIN_BUILT" ]; then
    err "Build failed — could not find llama-cli or main binary under build/"
  fi

  cp "$LLAMA_BIN_BUILT" "$LLAMA_BIN"
  chmod +x "$LLAMA_BIN"
  log "llama-cli installed to $LLAMA_BIN (from $LLAMA_BIN_BUILT)"
fi

# ── Download IBM Granite GGUF model ───────────────────────────────────────────
if [ -f "$MODEL_FILE" ] && [ "$(stat -c%s "$MODEL_FILE" 2>/dev/null || echo 0)" -gt 100000000 ]; then
  info "Granite model already present at $MODEL_FILE"
else
  log "Downloading IBM Granite 3.2 1B A400M Instruct (Q2_K, ~400MB)..."

  MODEL_URL="${HF_BASE}/${MODEL_REPO}/resolve/main/${MODEL_FILENAME}"

  # Try wget with progress, fall back to curl
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
log "Running smoke test (may take ~60 seconds)..."
TEST_OUT=$("$LLAMA_BIN" \
  --model "$MODEL_FILE" \
  --threads 2 \
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
  log "Smoke test OK: \"${TEST_OUT:0:80}...\""
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
