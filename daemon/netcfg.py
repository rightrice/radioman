"""
netcfg.py — read WiFi connection status and join a network from the dashboard.

Supports both NetworkManager (nmcli) and netplan/wpa_supplicant. The daemon
runs as root (systemd), so it can apply system network config. Joining is
best-effort and may briefly drop the wlan0 connection — manage the Pi over the
USB link (10.55.0.1) when changing WiFi.
"""

import logging
import os
import re
import shutil
import subprocess

log = logging.getLogger("netcfg")


def wifi_status(iface: str = "wlan0") -> dict:
    """Current association + IP for the WiFi interface."""
    ssid, signal = "", None
    try:
        out = subprocess.check_output(["iw", "dev", iface, "link"],
                                      text=True, timeout=5, stderr=subprocess.DEVNULL)
        if "Not connected" not in out:
            m = re.search(r"SSID:\s*(.+)", out)
            if m:
                ssid = m.group(1).strip()
            m = re.search(r"signal:\s*(-?\d+)", out)
            if m:
                signal = int(m.group(1))
    except Exception as e:
        log.debug("wifi_status link: %s", e)

    ip = ""
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show", iface],
                                      text=True, timeout=5)
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            ip = m.group(1)
    except Exception as e:
        log.debug("wifi_status ip: %s", e)

    return {
        "iface":     iface,
        "connected": bool(ssid),
        "ssid":      ssid,
        "signal":    signal,
        "ip":        ip,
        "manager":   "networkmanager" if shutil.which("nmcli") else "netplan",
    }


def wifi_connect(iface: str, ssid: str, password: str) -> tuple:
    """Join a WiFi network. Returns (ok, message). Blocking (can take ~30s)."""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "SSID is required"

    if shutil.which("nmcli"):
        return _connect_nmcli(iface, ssid, password)
    return _connect_netplan(iface, ssid, password)


def _connect_nmcli(iface: str, ssid: str, password: str) -> tuple:
    cmd = ["nmcli", "device", "wifi", "connect", ssid, "ifname", iface]
    if password:
        cmd += ["password", password]
    try:
        # nmcli rescans + connects; blocks until associated or failed.
        out = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
        if out.returncode == 0:
            log.info("nmcli connected to %s", ssid)
            return True, f"Connected to {ssid}"
        msg = (out.stderr or out.stdout or "connection failed").strip().splitlines()[-1]
        log.warning("nmcli connect failed: %s", msg)
        return False, msg
    except subprocess.TimeoutExpired:
        return False, "Connection attempt timed out"
    except Exception as e:
        return False, str(e)


def _connect_netplan(iface: str, ssid: str, password: str) -> tuple:
    netplan_dir = "/etc/netplan"
    if not os.path.isdir(netplan_dir):
        return False, "Neither nmcli nor netplan found — cannot configure WiFi"

    # Escape double quotes in SSID/password for the YAML string literals.
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    ap_block = f'          "{esc(ssid)}": {{}}' if not password \
        else f'          "{esc(ssid)}":\n            password: "{esc(password)}"'
    content = (
        "network:\n"
        "  version: 2\n"
        "  wifis:\n"
        f"    {iface}:\n"
        "      dhcp4: true\n"
        "      optional: true\n"
        "      access-points:\n"
        f"{ap_block}\n"
    )
    path = os.path.join(netplan_dir, "90-radioman-wifi.yaml")
    try:
        with open(path, "w") as fh:
            fh.write(content)
        os.chmod(path, 0o600)   # contains the WiFi password
        subprocess.run(["netplan", "generate"], check=True, timeout=15,
                       capture_output=True, text=True)
        subprocess.run(["netplan", "apply"], check=True, timeout=20,
                       capture_output=True, text=True)
        log.info("netplan applied WiFi config for %s", ssid)
        return True, f"Applied netplan config for {ssid}"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or str(e)).strip()
    except Exception as e:
        return False, str(e)
