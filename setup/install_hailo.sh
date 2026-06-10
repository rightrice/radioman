#!/bin/bash
# install_hailo.sh — set up the Raspberry Pi AI HAT+ 2 (Hailo-10H) so radioman's
# AI assistant runs ON THE NPU instead of the CPU.
#
# The Hailo-10H runs LLMs fully on-device via "hailo-ollama", a local
# Ollama-compatible HTTP server (loopback only — NO internet at runtime). radioman
# then points [ai] backend=hailo at it. US-origin model only: Llama 3.2 1B (Meta).
#
# Requires: Raspberry Pi 5 + AI HAT+ 2 (Hailo-10H). Run after install.sh:
#   sudo bash setup/install_hailo.sh
#
# NOTE: the Hailo GenAI packaging evolves — the package/model steps below follow
# Hailo's current docs (Hailo Model Zoo GenAI / hailo-ollama). If a step's name
# has changed, this script tells you exactly what to check rather than guessing.

set -e

CONF="/opt/radioman/radioman.conf"
HAILO_MODEL="${HAILO_MODEL:-llama3.2:1b}"     # US-origin (Meta). Override via env.
HAILO_URL="${HAILO_URL:-http://127.0.0.1:11434}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[hailo]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/install_hailo.sh"

# ── Board check ───────────────────────────────────────────────────────────────
BOARD=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "")
case "$BOARD" in
  *"Raspberry Pi 5"*) : ;;
  *) warn "This is for the Pi 5 + AI HAT+ 2. Detected: ${BOARD:-unknown}. Continuing anyway." ;;
esac
log "Board: ${BOARD:-unknown}"

# ── PCIe Gen3 (recommended for the HAT+ throughput) ──────────────────────────
CONFIG_TXT="/boot/firmware/config.txt"
[ ! -f "$CONFIG_TXT" ] && CONFIG_TXT="/boot/config.txt"
if [ -f "$CONFIG_TXT" ]; then
  grep -q "^dtparam=pciex1_gen=3" "$CONFIG_TXT" || echo "dtparam=pciex1_gen=3" >> "$CONFIG_TXT"
  log "PCIe Gen3 enabled in $CONFIG_TXT (reboot required to take effect)"
else
  warn "No config.txt found — enable PCIe Gen3 manually for best throughput"
fi

# ── Hailo runtime + firmware ─────────────────────────────────────────────────
# 'hailo-all' is the Raspberry Pi metapackage (HailoRT + PCIe driver + firmware).
log "Installing Hailo runtime (hailo-all)..."
apt-get update -qq
apt-get install -y hailo-all 2>/dev/null && log "hailo-all installed" || \
  warn "Could not install hailo-all via apt — see https://www.raspberrypi.com/documentation/computers/ai.html"

# ── Hailo-Ollama GenAI server (runs the LLM on the NPU) ──────────────────────
# Shipped as the Hailo Model Zoo GenAI package. Package name/source can change
# between releases, so detect it and otherwise print exact next steps.
if command -v hailo-ollama >/dev/null 2>&1 || systemctl list-unit-files 2>/dev/null | grep -q hailo-ollama; then
  log "hailo-ollama already present"
else
  apt-get install -y hailo-ollama 2>/dev/null && log "hailo-ollama installed" || {
    warn "hailo-ollama not available via apt on this image."
    warn "Install the 'Hailo Model Zoo GenAI' package per Hailo's docs, then re-run:"
    warn "  https://github.com/hailo-ai  (search: hailo-ollama / Model Zoo GenAI for RPi5)"
  }
fi

# Enable + start the server if the unit exists.
if systemctl list-unit-files 2>/dev/null | grep -q "hailo-ollama"; then
  systemctl enable --now hailo-ollama 2>/dev/null && log "hailo-ollama service started" || \
    warn "Could not start hailo-ollama service — check: systemctl status hailo-ollama"
fi

# ── Pull the model (Llama 3.2 1B — Meta/US) ──────────────────────────────────
# First chat auto-downloads (~2GB) if not pre-pulled. Try an explicit pull if the
# Ollama-style CLI/endpoint is available.
log "Ensuring model '$HAILO_MODEL' is available (one-time ~2GB download)..."
if command -v hailo-ollama >/dev/null 2>&1; then
  hailo-ollama pull "$HAILO_MODEL" 2>/dev/null || \
    info "Could not pre-pull; it will download on first use."
else
  curl -sf -X POST "$HAILO_URL/api/pull" -d "{\"name\":\"$HAILO_MODEL\"}" >/dev/null 2>&1 || \
    info "Pre-pull skipped; the model downloads on first chat."
fi

# ── Point radioman at the NPU backend ────────────────────────────────────────
if [ -f "$CONF" ]; then
  python3 - "$CONF" "$HAILO_URL" "$HAILO_MODEL" <<'PY'
import configparser, sys
conf, url, model = sys.argv[1:4]
cp = configparser.ConfigParser(); cp.read(conf)
if "ai" not in cp: cp.add_section("ai")
cp.set("ai", "backend", "hailo")
cp.set("ai", "hailo_url", url)
cp.set("ai", "hailo_model", model)
with open(conf, "w") as f: cp.write(f)
PY
  log "radioman.conf [ai] backend=hailo, model=$HAILO_MODEL"
else
  warn "$CONF not found — set [ai] backend=hailo manually after install.sh"
fi

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
if curl -sf "$HAILO_URL/api/tags" >/dev/null 2>&1; then
  log "Hailo-Ollama endpoint is UP at $HAILO_URL"
else
  warn "Hailo-Ollama endpoint not responding at $HAILO_URL yet."
  warn "A reboot is usually needed after enabling PCIe Gen3 + installing the driver."
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " AI HAT+ 2 (Hailo-10H) setup complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Model:    $HAILO_MODEL  (Llama 3.2 1B — Meta, US-origin; on-device, no internet)"
info "Endpoint: $HAILO_URL  (loopback only)"
info "Reboot, then check the dashboard AI tab — it should show '⚡ Hailo-10H NPU'."
echo ""
warn "Runtime is fully on-device. The only network use was this one-time install"
warn "(Hailo packages + model download). After this it runs airgapped."
echo ""
