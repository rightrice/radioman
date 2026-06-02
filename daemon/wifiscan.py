"""
wifiscan.py — WiFiMan-style AP discovery on the internal radio.

Runs `iw dev <iface> scan` periodically in MANAGED mode (no monitor mode,
no adapter required) and feeds discovered access points into the same
on_network callback bettercap uses, so the Networks / Stats / channel
congestion views populate from the Pi's built-in WiFi.

This complements capture.py: while bettercap holds the interface in monitor
mode for handshake capture, this scanner pauses (via should_pause) to avoid
fighting over the radio.
"""

import logging
import re
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("wifiscan")


class WifiScanner:
    def __init__(self, iface: str = "wlan0", on_network: Callable = None,
                 interval: int = 60, should_pause: Callable = None,
                 vendor_lookup: Callable = None):
        self._iface         = iface
        self._on_network    = on_network
        self._interval      = max(15, interval)
        self._should_pause  = should_pause or (lambda: False)
        self._vendor_lookup = vendor_lookup or (lambda mac: "")
        self._running       = False

    # ── Scan ───────────────────────────────────────────────────────────────────
    def scan_once(self) -> list:
        """Run a single `iw scan` and return parsed APs. Emits each via on_network."""
        try:
            out = subprocess.check_output(
                ["iw", "dev", self._iface, "scan"],
                text=True, timeout=30, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.warning("iw not found — install with: sudo apt install iw")
            return []
        except subprocess.CalledProcessError as e:
            # Busy (monitor mode), down, or transient — normal, skip quietly.
            log.debug("iw scan on %s failed (rc=%s)", self._iface, e.returncode)
            return []
        except subprocess.TimeoutExpired:
            log.debug("iw scan timed out")
            return []

        aps = parse_iw_scan(out)
        for ap in aps:
            ap["vendor"] = self._vendor_lookup(ap["bssid"]) or ""
            if self._on_network:
                self._on_network(ap["bssid"], ap["ssid"], ap["channel"],
                                 ap["rssi"], ap["security"], ap["vendor"])
        log.info("iw scan: %d APs on %s", len(aps), self._iface)
        return aps

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def _loop(self):
        # Small initial delay so the interface settles after boot.
        time.sleep(5)
        while self._running:
            if not self._should_pause():
                try:
                    self.scan_once()
                except Exception as e:
                    log.error("wifi scan loop error: %s", e)
            else:
                log.debug("wifiscan paused (capture active)")
            time.sleep(self._interval)

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="wifiscan").start()
        log.info("WiFi scanner started (managed-mode iw scan, interface: %s, every %ds)",
                 self._iface, self._interval)

    def stop(self):
        self._running = False


# ── Parser (pure, testable) ────────────────────────────────────────────────────
def _freq_to_channel(freq: int) -> int:
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:                 # 6 GHz (channel 1 = 5955)
        return (freq - 5950) // 5
    return 0


def parse_iw_scan(text: str) -> list:
    """Parse `iw dev X scan` output into a list of AP dicts."""
    aps = []
    cur = None

    def finish(ap):
        if ap and ap.get("bssid"):
            ap["security"] = _classify_security(ap)
            ap.pop("_rsn", None); ap.pop("_wpa", None); ap.pop("_sae", None)
            ap.pop("_privacy", None)
            aps.append(ap)

    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^BSS ([0-9a-fA-F:]{17})", line)
        if m:
            finish(cur)
            cur = {"bssid": m.group(1).upper(), "ssid": "", "channel": 0,
                   "rssi": -100, "security": "OPEN",
                   "_rsn": False, "_wpa": False, "_sae": False, "_privacy": False}
            continue
        if cur is None:
            continue

        if line.startswith("freq:"):
            try:
                cur["channel"] = _freq_to_channel(int(float(line.split(":", 1)[1])))
            except ValueError:
                pass
        elif line.startswith("signal:"):
            try:
                cur["rssi"] = round(float(line.split(":", 1)[1].split()[0]))
            except (ValueError, IndexError):
                pass
        elif line.startswith("SSID:"):
            cur["ssid"] = line[5:].strip()
        elif line.startswith("RSN:"):
            cur["_rsn"] = True
        elif line.startswith("WPA:"):
            cur["_wpa"] = True
        elif "Authentication suites:" in line and "SAE" in line:
            cur["_sae"] = True
        elif line.startswith("capability:") and "Privacy" in line:
            cur["_privacy"] = True

    finish(cur)
    return aps


def _classify_security(ap: dict) -> str:
    if ap.get("_sae"):
        return "WPA3"
    if ap.get("_rsn"):
        return "WPA2"
    if ap.get("_wpa"):
        return "WPA"
    if ap.get("_privacy"):
        return "WEP"
    return "OPEN"
