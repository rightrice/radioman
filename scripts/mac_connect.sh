#!/usr/bin/env bash
# mac_connect.sh — run on your Mac to connect to radioman over USB
#
# Usage:
#   bash scripts/mac_connect.sh            — just SSH-ready (no internet sharing)
#   bash scripts/mac_connect.sh share      — SSH + share your Mac's internet to the Pi
#   bash scripts/mac_connect.sh share en1  — specify upstream interface (auto-detected)
#
# Pi IP:  10.55.0.1  (set by radioman install)
# Mac IP: 10.55.0.2  (set by this script)
#
# Adapted from jayofelony/pwnagotchi macos_connection_share.sh

set -e

MODE=${1:-}
# Auto-detect upstream: interface that holds the default route (usually en1 on
# Mac Mini/MacBook when on WiFi, en0 on machines with wired-only internet)
DEFAULT_UPSTREAM=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}' || echo "en0")
UPSTREAM=${2:-$DEFAULT_UPSTREAM}
PI_IP="10.55.0.1"
MAC_IP="10.55.0.2"
USB_NET="10.55.0.0/24"

# ── Find the USB gadget interface ──────────────────────────────────────────────
USB_IFACE=""

# Look for the USB gadget interface.
# On macOS, RNDIS/CDC-ECM gadgets appear as anpi* (Apple Network Proxy Interface).
# Prefer an anpi* with status:active; fall back to any anpi* if none are active.
# Skip known system interfaces.
FALLBACK_IFACE=""
for iface in $(ifconfig -lu 2>/dev/null); do
  case "$iface" in lo*|en0|en1|en2|en3|utun*|bridge*|gif*|stf*|ap*|awdl*|llw*) continue ;; esac
  if ifconfig "$iface" 2>/dev/null | grep -qiE "ether|ethernet"; then
    STATUS=$(ifconfig "$iface" 2>/dev/null | awk '/status:/{print $2}')
    if [ "$STATUS" = "active" ]; then
      USB_IFACE="$iface"
      break
    fi
    [ -z "$FALLBACK_IFACE" ] && FALLBACK_IFACE="$iface"
  fi
done
[ -z "$USB_IFACE" ] && USB_IFACE="$FALLBACK_IFACE"

if [ -z "$USB_IFACE" ]; then
  echo "ERROR: No USB gadget interface found."
  echo "       Make sure the Pi is booted and connected with a data USB cable."
  echo "       Check System Settings → Network for a new interface after plugging in."
  exit 1
fi

echo "Found USB interface: $USB_IFACE"

# ── Assign static IP to the Mac's USB interface ────────────────────────────────
CURRENT_IP=$(ifconfig "$USB_IFACE" 2>/dev/null | awk '/inet /{print $2}')
if [ "$CURRENT_IP" = "$MAC_IP" ]; then
  echo "Mac USB IP already set to $MAC_IP"
else
  echo "Setting $USB_IFACE to $MAC_IP ..."
  sudo ifconfig "$USB_IFACE" "$MAC_IP" netmask 255.255.255.0 up
  echo "Mac USB IP: $MAC_IP"
fi

# ── Verify Pi is reachable ─────────────────────────────────────────────────────
echo "Pinging Pi at $PI_IP ..."
if ping -c 2 -W 1000 "$PI_IP" &>/dev/null; then
  echo "Pi is reachable at $PI_IP"
else
  echo "WARNING: Pi not responding to ping yet."
  echo "         It may still be booting, or the Pi-side USB gadget isn't configured."
  echo "         Run on the Pi:  ip addr show usb0"
fi

# ── Internet sharing via pfctl (optional) ─────────────────────────────────────
if [ "$MODE" = "share" ]; then
  echo ""
  echo "Setting up internet sharing: $UPSTREAM → $USB_IFACE ..."

  # Enable IP forwarding
  sudo sysctl -w net.inet.ip.forwarding=1 >/dev/null

  # Set up NAT — Pi will use Mac's upstream connection
  sudo pfctl -e 2>/dev/null || true
  echo "nat on ${UPSTREAM} from ${USB_NET} to any -> (${UPSTREAM})" | sudo pfctl -f - 2>/dev/null

  echo "Internet sharing active: Pi can now reach the internet via your Mac's $UPSTREAM"
  echo "To stop sharing: sudo pfctl -d"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " SSH:       ssh kali@${PI_IP}"
echo " Dashboard: http://${PI_IP}:8080"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
