#!/usr/bin/env bash
# linux_connect.sh — run on Ubuntu/Linux to connect to radioman over USB
#
# Usage:
#   bash scripts/linux_connect.sh             — SSH-ready (static IP only)
#   bash scripts/linux_connect.sh share       — SSH + iptables NAT internet sharing
#   bash scripts/linux_connect.sh share wlan0 — specify upstream interface
#
# Pi IP:    10.55.0.1  (set by radioman install)
# Linux IP: 10.55.0.2  (set by this script)
#
# Note: unlike Windows ICS, Linux iptables NAT does NOT change the adapter IP,
# so the Pi stays reachable at 10.55.0.1 in both modes. No Pi-side changes needed.

set -e

MODE=${1:-}
PI_IP="10.55.0.1"
LINUX_IP="10.55.0.2"
USB_NET="10.55.0.0/24"

# Auto-detect upstream: interface holding the default route
DEFAULT_UPSTREAM=$(ip route get 8.8.8.8 2>/dev/null \
  | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1 || true)
DEFAULT_UPSTREAM=${DEFAULT_UPSTREAM:-$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')}
UPSTREAM=${2:-$DEFAULT_UPSTREAM}

# ── Find the USB gadget interface ──────────────────────────────────────────────
# The Pi Zero 2W USB gadget (g_ether) appears on Linux as a CDC ECM device.
# Driver: cdc_ether (Linux) or rndis_host (Windows-emulation mode).
# Interface names vary: usb0, enxXXXXXX, etc.

USB_IFACE=""
FALLBACK_IFACE=""

for iface in $(ls /sys/class/net/ 2>/dev/null | sort); do
  [[ "$iface" == "lo" ]] && continue

  # Must be a USB-backed device (device symlink path contains /usb/)
  dev_path=$(readlink -f "/sys/class/net/$iface/device" 2>/dev/null || echo "")
  [[ "$dev_path" != *"/usb/"* ]] && continue

  # Check driver name
  driver=$(basename "$(readlink "/sys/class/net/$iface/device/driver" 2>/dev/null || echo "")" 2>/dev/null || echo "")
  if [[ "$driver" == "cdc_ether" || "$driver" == "rndis_host" ]]; then
    state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo "unknown")
    if [[ "$state" == "up" ]]; then
      USB_IFACE="$iface"
      break
    fi
    [[ -z "$FALLBACK_IFACE" ]] && FALLBACK_IFACE="$iface"
  fi
done

[[ -z "$USB_IFACE" ]] && USB_IFACE="$FALLBACK_IFACE"

if [[ -z "$USB_IFACE" ]]; then
  echo "ERROR: No USB gadget interface found."
  echo "       Make sure the Pi is booted and connected with a data USB cable."
  echo "       Check: ip link show   or   nmcli device status"
  echo "       You may need to load the cdc_ether driver: sudo modprobe cdc_ether"
  exit 1
fi

echo "Found USB interface: $USB_IFACE  (driver: $(basename "$(readlink "/sys/class/net/$USB_IFACE/device/driver" 2>/dev/null || echo "unknown")"))"

# ── Assign static IP ───────────────────────────────────────────────────────────
CURRENT_IP=$(ip addr show "$USB_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)

if [[ "$CURRENT_IP" == "$LINUX_IP" ]]; then
  echo "USB adapter already has $LINUX_IP"
else
  echo "Setting $USB_IFACE to $LINUX_IP ..."

  if command -v nmcli &>/dev/null; then
    # nmcli creates a persistent profile and handles reconnects gracefully.
    # ipv4.never-default prevents this adapter from replacing your internet route.
    nmcli connection delete "radioman-usb" 2>/dev/null || true
    nmcli connection add \
      type ethernet \
      ifname "$USB_IFACE" \
      con-name "radioman-usb" \
      ipv4.method manual \
      ipv4.addresses "$LINUX_IP/24" \
      ipv4.never-default yes \
      ipv6.method disabled \
      connection.autoconnect no 2>/dev/null
    nmcli connection up "radioman-usb" 2>/dev/null
    echo "IP set via NetworkManager"
  else
    # Fallback: raw ip commands (not persistent across reconnects)
    sudo ip addr flush dev "$USB_IFACE" 2>/dev/null || true
    sudo ip addr add "$LINUX_IP/24" dev "$USB_IFACE"
    sudo ip link set "$USB_IFACE" up
    echo "IP set via ip command (NetworkManager not found)"
  fi
fi

# Remove any default route on the USB adapter — prevents it from hijacking
# your internet traffic (same issue as Windows/macOS).
sudo ip route del default dev "$USB_IFACE" 2>/dev/null || true

echo "Linux USB IP: $LINUX_IP"

# ── Verify Pi is reachable ─────────────────────────────────────────────────────
echo "Pinging Pi at $PI_IP ..."
if ping -c 2 -W 3 "$PI_IP" &>/dev/null; then
  echo "Pi is reachable at $PI_IP"
else
  echo "WARNING: Pi not responding to ping yet."
  echo "         It may still be booting, or usb0 isn't configured on the Pi."
  echo "         Run on the Pi:  ip addr show usb0"
fi

# ── Internet sharing via iptables NAT (optional) ───────────────────────────────
if [[ "$MODE" == "share" ]]; then
  echo ""

  if [[ -z "$UPSTREAM" ]]; then
    echo "ERROR: Could not detect upstream internet interface."
    echo "       Specify it manually: bash scripts/linux_connect.sh share wlan0"
    exit 1
  fi

  echo "Setting up internet sharing: $UPSTREAM → $USB_IFACE ..."

  # Enable IP forwarding for this session
  sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null

  # NAT: masquerade traffic from the Pi's subnet out through the upstream interface.
  # Check for existing rules first to avoid duplicates on re-run.
  if ! sudo iptables -t nat -C POSTROUTING -s "$USB_NET" -o "$UPSTREAM" -j MASQUERADE 2>/dev/null; then
    sudo iptables -t nat -A POSTROUTING -s "$USB_NET" -o "$UPSTREAM" -j MASQUERADE
  fi
  if ! sudo iptables -C FORWARD -i "$USB_IFACE" -o "$UPSTREAM" -j ACCEPT 2>/dev/null; then
    sudo iptables -A FORWARD -i "$USB_IFACE" -o "$UPSTREAM" -j ACCEPT
  fi
  if ! sudo iptables -C FORWARD -i "$UPSTREAM" -o "$USB_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
    sudo iptables -A FORWARD -i "$UPSTREAM" -o "$USB_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
  fi

  echo "Internet sharing active: Pi routes internet via your $UPSTREAM"
  echo "Rules are session-only — re-run this script after reboot."
  echo "To stop sharing: sudo iptables -t nat -F POSTROUTING && sudo iptables -F FORWARD"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " SSH:       ssh kali@${PI_IP}"
echo " Dashboard: http://${PI_IP}:8080"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
