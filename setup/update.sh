#!/bin/bash
# radioman update script
# Run from the cloned git repo after git pull:
#   cd /path/to/radioman && git pull && sudo bash setup/update.sh
#
# Updates deployed app files, web assets, caplet, and service.
# Never touches: radioman.conf, captures/, wordlists/

set -e

RADIOMAN_DIR="/opt/radioman"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()    { echo -e "${GREEN}[update]${NC} $1"; }
warn()   { echo -e "${YELLOW}[warning]${NC} $1"; }
err()    { echo -e "${RED}[error]${NC} $1"; exit 1; }
info()   { echo -e "${BLUE}[info]${NC} $1"; }
change() { echo -e "  ${GREEN}↑${NC} $1"; CHANGED=true; }
same()   { echo -e "  ${BLUE}·${NC} $1 (unchanged)"; }

[ "$EUID" -ne 0 ] && err "Please run as root: sudo bash setup/update.sh"
[ ! -d "$RADIOMAN_DIR" ] && err "radioman not installed at $RADIOMAN_DIR — run setup/install.sh first"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGED=false

echo ""
log "Checking for updates..."
echo ""

# ── Git status ─────────────────────────────────────────────────────────────────
if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
  CURRENT=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  BRANCH=$(git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null || echo "unknown")
  info "Repo: $SCRIPT_DIR  branch=$BRANCH  commit=$CURRENT"
else
  warn "Not a git repo — skipping version info"
fi
echo ""

# ── Helper: copy file if changed ───────────────────────────────────────────────
update_file() {
  local src="$1" dst="$2" label="$3"
  if [ ! -f "$dst" ]; then
    cp "$src" "$dst"
    change "$label (new)"
  elif ! cmp -s "$src" "$dst"; then
    cp "$src" "$dst"
    change "$label"
  else
    same "$label"
  fi
}

# ── Helper: copy dir if any file inside changed ────────────────────────────────
update_dir() {
  local src="$1" dst="$2" label="$3"
  local dir_changed=false

  # Find files in src that differ from dst
  while IFS= read -r -d '' f; do
    rel="${f#$src/}"
    dst_file="$dst/$rel"
    if [ ! -f "$dst_file" ] || ! cmp -s "$f" "$dst_file"; then
      dir_changed=true
      break
    fi
  done < <(find "$src" -type f -print0)

  if $dir_changed; then
    rm -rf "$dst"
    cp -r "$src" "$dst"
    change "$label"
  else
    same "$label"
  fi
}

# ── Daemon Python files ────────────────────────────────────────────────────────
log "Daemon files..."
for f in "$SCRIPT_DIR"/daemon/*.py; do
  fname="$(basename "$f")"
  update_file "$f" "$RADIOMAN_DIR/$fname" "daemon/$fname"
done
echo ""

# ── Web assets ────────────────────────────────────────────────────────────────
log "Web assets..."
update_dir "$SCRIPT_DIR/web" "$RADIOMAN_DIR/web" "web/"
echo ""

# ── Caplet ────────────────────────────────────────────────────────────────────
log "Bettercap caplet..."
update_file "$SCRIPT_DIR/setup/radioman.cap" "$RADIOMAN_DIR/radioman.cap" "radioman.cap"
echo ""

# ── Systemd service ────────────────────────────────────────────────────────────
log "Systemd service..."
SERVICE_SRC="$SCRIPT_DIR/setup/radioman.service"
SERVICE_DST="/etc/systemd/system/radioman.service"
if [ ! -f "$SERVICE_DST" ] || ! cmp -s "$SERVICE_SRC" "$SERVICE_DST"; then
  cp "$SERVICE_SRC" "$SERVICE_DST"
  systemctl daemon-reload
  change "radioman.service (daemon-reload done)"
else
  same "radioman.service"
fi
echo ""

# ── Waveshare library (re-sync if e-Paper repo is present) ────────────────────
log "Waveshare library..."
WAVESHARE_SRC="/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_PKG="$RADIOMAN_DIR/venv/lib/python${PY_VER}/site-packages"

if [ -d "$WAVESHARE_SRC" ]; then
  update_dir "$WAVESHARE_SRC" "$SITE_PKG/waveshare_epd" "waveshare_epd (venv)"
else
  same "waveshare_epd (source not found — skipping)"
fi
echo ""

# ── Python packages ────────────────────────────────────────────────────────────
log "Python packages..."
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
VENV_PIP="$RADIOMAN_DIR/venv/bin/pip"

if [ -f "$REQUIREMENTS" ]; then
  # Check if installed packages match requirements
  MISSING=$("$VENV_PIP" install --dry-run -r "$REQUIREMENTS" --quiet 2>&1 | grep "Would install" || true)
  if [ -n "$MISSING" ]; then
    "$VENV_PIP" install --quiet -r "$REQUIREMENTS"
    change "Python packages: $MISSING"
  else
    same "Python packages"
  fi
else
  same "Python packages (no requirements.txt — skipping)"
fi
echo ""

# ── Permissions ────────────────────────────────────────────────────────────────
chmod +x "$RADIOMAN_DIR/radioman.py" 2>/dev/null || true
chmod +x "$RADIOMAN_DIR/ignore_cli.py" 2>/dev/null || true

# ── Restart if anything changed ────────────────────────────────────────────────
if $CHANGED; then
  log "Changes detected — restarting radioman..."
  systemctl restart radioman
  sleep 2
  STATUS=$(systemctl is-active radioman 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "active" ]; then
    log "radioman restarted successfully"
  else
    warn "radioman may not have started cleanly — check: journalctl -u radioman -n 30"
  fi
else
  log "Nothing changed — radioman not restarted"
fi

echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Update complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Logs: journalctl -u radioman -f"
info "Dashboard: http://radioman.local:8080"
echo ""
