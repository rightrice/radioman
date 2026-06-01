#!/usr/bin/env bash
# mac_connect.sh — connect to radioman over USB on macOS
#
# Usage:
#   bash scripts/mac_connect.sh            — USB connection only
#   bash scripts/mac_connect.sh share      — USB + share Mac's internet to Pi
#   bash scripts/mac_connect.sh diagnose   — full diagnostic, no changes
#
# Pi IP:  10.55.0.1  (set by radioman install)
# Mac IP: 10.55.0.2  (set by this script)

set -e

MODE=${1:-}
PI_IP="10.55.0.1"
MAC_IP="10.55.0.2"
USB_NET="10.55.0.0/24"

# MACs set by /etc/modprobe.d/g_ncm.conf on the Pi
HOST_MAC="72:48:4f:52:4d:01"   # Mac-side MAC (host_addr)
PI_MAC="72:48:4f:52:4d:02"     # Pi-side MAC  (dev_addr)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[radioman]${NC} $1"; }
warn() { echo -e "${YELLOW}[warning]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; }
info() { echo -e "${BLUE}[info]${NC} $1"; }

# ── Diagnose mode ─────────────────────────────────────────────────────────────
if [ "$MODE" = "diagnose" ]; then
  echo ""
  log "USB gadget diagnostic"
  echo ""
  info "All network interfaces:"
  for iface in $(ifconfig -l 2>/dev/null); do
    MAC=$(ifconfig "$iface" 2>/dev/null | awk '/ether/{print $2}')
    STATUS=$(ifconfig "$iface" 2>/dev/null | awk '/status:/{print $2}')
    IP=$(ifconfig "$iface" 2>/dev/null | awk '/inet /{print $2}')
    [ -n "$MAC" ] && echo "  $iface  MAC=$MAC  status=${STATUS:-?}  IP=${IP:--}"
  done
  echo ""
  info "Routing table (10.55.x.x):"
  netstat -rn 2>/dev/null | grep "10\.55" || echo "  (none)"
  echo ""
  info "ARP entry for $PI_IP:"
  arp -n "$PI_IP" 2>/dev/null || echo "  (no entry)"
  echo ""
  exit 0
fi

# ── Find USB gadget interface ─────────────────────────────────────────────────
echo ""
log "Searching for USB gadget interface..."

USB_IFACE=""

# Primary: find by our known persistent host MAC
for iface in $(ifconfig -l 2>/dev/null); do
  MAC=$(ifconfig "$iface" 2>/dev/null | awk '/ether/{print $2}')
  if [ "$MAC" = "$HOST_MAC" ]; then
    USB_IFACE="$iface"
    info "Found by MAC ($HOST_MAC): $iface"
    break
  fi
done

# Fallback: any active non-system ethernet
if [ -z "$USB_IFACE" ]; then
  warn "Persistent MAC $HOST_MAC not found — Pi may still be booting or g_ncm MACs not set."
  warn "Falling back to interface scan..."
  FALLBACK=""
  for iface in $(ifconfig -l 2>/dev/null); do
    case "$iface" in lo*|en0|en1|en2|en3|utun*|bridge*|gif*|stf*|ap*|awdl*|llw*|anpi*) continue ;; esac
    if ifconfig "$iface" 2>/dev/null | grep -qiE "ether"; then
      STATUS=$(ifconfig "$iface" 2>/dev/null | awk '/status:/{print $2}')
      if [ "$STATUS" = "active" ]; then
        USB_IFACE="$iface"
        warn "Using active interface by fallback: $iface"
        break
      fi
      [ -z "$FALLBACK" ] && FALLBACK="$iface"
    fi
  done
  [ -z "$USB_IFACE" ] && USB_IFACE="$FALLBACK"
fi

if [ -z "$USB_IFACE" ]; then
  err "No USB gadget interface found."
  echo ""
  echo "  Checklist:"
  echo "  1. Is the Pi powered on and fully booted? (wait ~30s)"
  echo "  2. Cable in Pi DATA port (middle), not PWR?"
  echo "  3. Data cable, not charge-only?"
  echo "  4. Run:  bash scripts/mac_connect.sh diagnose  for full interface list"
  echo ""
  exit 1
fi

log "USB interface: $USB_IFACE"

# ── Clear conflicting 10.55.0.x IPs from other interfaces ────────────────────
for iface in $(ifconfig -l 2>/dev/null); do
  [ "$iface" = "$USB_IFACE" ] && continue
  EXISTING=$(ifconfig "$iface" 2>/dev/null | awk '/inet 10\.55\./{print $2}')
  if [ -n "$EXISTING" ]; then
    warn "Removing conflicting 10.55.x IP from $iface ($EXISTING)..."
    sudo ifconfig "$iface" delete "$EXISTING" 2>/dev/null || true
  fi
done

# ── Set static IP on USB interface ────────────────────────────────────────────
CURRENT_IP=$(ifconfig "$USB_IFACE" 2>/dev/null | awk '/inet /{print $2}')
if [ "$CURRENT_IP" = "$MAC_IP" ]; then
  info "Mac USB IP already $MAC_IP on $USB_IFACE"
else
  log "Setting $USB_IFACE → $MAC_IP ..."
  sudo ifconfig "$USB_IFACE" "$MAC_IP" netmask 255.255.255.0 up
fi

# ── Set static ARP entry for Pi ───────────────────────────────────────────────
sudo arp -d "$PI_IP" 2>/dev/null || true
sudo arp -s "$PI_IP" "$PI_MAC" 2>/dev/null && \
  info "ARP: $PI_IP → $PI_MAC (static)" || \
  warn "Could not set static ARP — dynamic ARP will be attempted"

# ── Verify Pi is reachable ────────────────────────────────────────────────────
log "Pinging Pi at $PI_IP ..."
PING_OK=false
for i in 1 2 3 4 5; do
  if ping -c 1 -W 1000 -t 3 "$PI_IP" &>/dev/null; then
    PING_OK=true
    break
  fi
  [ $i -lt 5 ] && sleep 1
done

if $PING_OK; then
  log "Pi is reachable at $PI_IP"
else
  warn "Pi not responding to ping."
  echo ""
  echo "  Possible causes:"
  echo "  • Pi still booting — wait 30s and retry"
  echo "  • usb0 not up on Pi — SSH via WiFi and run:"
  echo "      sudo netplan apply && ip addr show usb0"
  echo "  • Run:  bash scripts/mac_connect.sh diagnose"
fi

# ── Internet sharing via pfctl ────────────────────────────────────────────────
if [ "$MODE" = "share" ]; then
  UPSTREAM=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}' || echo "en0")
  echo ""
  log "Sharing internet: $UPSTREAM → $USB_IFACE ..."
  sudo sysctl -w net.inet.ip.forwarding=1 >/dev/null
  sudo pfctl -e 2>/dev/null || true
  echo "nat on ${UPSTREAM} from ${USB_NET} to any -> (${UPSTREAM})" | sudo pfctl -f - 2>/dev/null
  log "Internet sharing active (via $UPSTREAM)"
  info "Re-run after Mac sleep/reboot — pfctl rules don't persist"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if $PING_OK; then
  echo -e " ${GREEN}SSH:${NC}       ssh rightrice@${PI_IP}"
  echo -e " ${GREEN}Dashboard:${NC} http://${PI_IP}:8080"
else
  echo -e " ${YELLOW}SSH:${NC}       ssh rightrice@${PI_IP}  (not reachable yet)"
  echo -e " ${YELLOW}Retry:${NC}     bash scripts/mac_connect.sh"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
