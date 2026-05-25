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

  # Construct the ARM64 asset URL directly from the tag.
  # llama.cpp names the asset: llama-{tag}-bin-ubuntu-arm64.zip
  ASSET_URL="https://github.com/ggerganov/llama.cpp/releases/download/${RELEASE_TAG}/llama-${RELEASE_TAG}-bin-ubuntu-arm64.zip"

  # Verify the asset exists before downloading
  HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" -L "$ASSET_URL" 2>/dev/null || echo "000")
  if [ "$HTTP_STATUS" != "200" ]; then
    # Try linux-arm64 variant (used in some release series)
    ASSET_URL="https://github.com/ggerganov/llama.cpp/releases/download/${RELEASE_TAG}/llama-${RELEASE_TAG}-bin-linux-arm64.zip"
    HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" -L "$ASSET_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_STATUS" != "200" ]; then
      err "No ARM64 binary found for release $RELEASE_TAG — check https://github.com/ggerganov/llama.cpp/releases"
    fi
  fi

  info "Downloading: $(basename "$ASSET_URL")"
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT

  if ! wget -q --show-progress -O "$TMP/llama.zip" "$ASSET_URL" 2>&1; then
    if ! curl -L --progress-bar -o "$TMP/llama.zip" "$ASSET_URL"; then
      err "Download failed. Check internet connection and try again."
    fi
  fi

  log "Extracting llama-cli..."
  unzip -q "$TMP/llama.zip" -d "$TMP/llama_extracted"

  # Binary may be at the root or inside a subdirectory
  LLAMA_BIN_FOUND=$(find "$TMP/llama_extracted" -name "llama-cli" -type f 2>/dev/null | head -1)
  if [ -z "$LLAMA_BIN_FOUND" ]; then
    # Some releases ship it as 'llama-cli' without extension, try any executable named main
    LLAMA_BIN_FOUND=$(find "$TMP/llama_extracted" -name "main" -type f 2>/dev/null | head -1)
  fi

  if [ -z "$LLAMA_BIN_FOUND" ]; then
    warn "Contents of extracted zip:"
    find "$TMP/llama_extracted" -type f | head -20
    err "Could not find llama-cli binary in the downloaded zip"
  fi

  cp "$LLAMA_BIN_FOUND" "$LLAMA_BIN"
  chmod +x "$LLAMA_BIN"
  log "llama-cli installed to $LLAMA_BIN"

  # Quick sanity check — just run --version, not a full inference
  if "$LLAMA_BIN" --version 2>/dev/null | grep -q "version\|llama"; then
    info "Binary verified: $("$LLAMA_BIN" --version 2>/dev/null | head -1)"
  else
    warn "Binary did not respond to --version — may still work, continuing"
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
