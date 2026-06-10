"""
ble.py — passive Bluetooth / BLE discovery on the internal radio.

The Pi's combo chip exposes a Bluetooth controller (hci0) that sits idle while
we audit Wi-Fi, so this is free signal. We stream device sightings straight from
BlueZ's `bluetoothctl` in interactive mode: power the adapter on, issue `scan on`,
and parse the `[NEW]`/`[CHG]` Device lines it emits (MAC, name, RSSI).

This mirrors wifiscan.py's shape — a background thread feeding an `on_device`
callback — and degrades gracefully: no bluetoothctl / no controller just logs a
warning and disables, nothing else breaks.

Why bluetoothctl over bettercap's ble.recon: bettercap only runs during a Wi-Fi
capture session and is bound to the monitor interface, whereas the BT controller
is independent and we want always-on discovery.
"""

import logging
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("ble")

# Don't re-emit the same device to the DB more often than this (RSSI churns a
# lot); a NEW sighting always emits immediately.
EMIT_INTERVAL = 15

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DEV = re.compile(r"\[(NEW|CHG|DEL)\]\s+Device\s+([0-9A-Fa-f:]{17})\s*(.*)")


def parse_btctl_line(line: str):
    """Parse one line of bluetoothctl output into an update dict, or None.

    Returns {"action": NEW|CHG|DEL, "mac": ..., optional "name", optional "rssi"}.
    Pure + testable — no I/O.
    """
    line = _ANSI.sub("", line).strip()
    m = _DEV.search(line)
    if not m:
        return None
    action, mac, rest = m.group(1), m.group(2).upper(), m.group(3).strip()
    upd = {"action": action, "mac": mac}
    if ":" in rest:
        key, _, val = rest.partition(":")
        key, val = key.strip(), val.strip()
        if key == "RSSI":
            try:
                upd["rssi"] = int(val)
            except ValueError:
                pass
        elif key == "Name":
            upd["name"] = val
        # TxPower / Connected / Paired / ManufacturerData etc. are ignored
    elif rest and action in ("NEW", "DEL"):
        # A bare trailing token is the display name — unless it's just the MAC
        # rendered with dashes, which BlueZ uses as a placeholder for "no name".
        if rest.replace("-", "").upper() != mac.replace(":", ""):
            upd["name"] = rest
    return upd


class BLEScanner:
    def __init__(self, on_device: Callable, mode: str = "auto",
                 vendor_lookup: Callable = None):
        self.mode = (mode or "auto").lower().strip()
        self.enabled = self.mode in ("auto", "bluetoothctl")
        self._on_device = on_device
        self._vendor_lookup = vendor_lookup or (lambda m: "")
        self._running = False
        self._proc = None
        self._devices = {}    # mac -> {"name", "rssi", "last_emit"}

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def start(self):
        if not self.enabled:
            log.info("Bluetooth scanning disabled (mode=%s)", self.mode)
            return
        if not shutil.which("bluetoothctl"):
            log.warning("bluetoothctl not found — Bluetooth scan disabled "
                        "(sudo apt install bluez)")
            self.enabled = False
            return
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="ble").start()
        log.info("Bluetooth scanner started (bluetoothctl stream)")

    def stop(self):
        self._running = False
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ── Stream ─────────────────────────────────────────────────────────────────
    def _run(self):
        try:
            subprocess.run(["bluetoothctl", "power", "on"],
                           capture_output=True, timeout=10)
        except Exception as e:
            log.debug("bluetoothctl power on: %s", e)

        backoff = 5
        while self._running:
            try:
                self._stream()
                backoff = 5
            except Exception as e:
                if self._running:
                    log.warning("BT scan stream error: %s — restarting in %ds",
                                e, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120)

    def _stream(self):
        # Interactive mode keeps the process alive and streaming events for as
        # long as stdin stays open — the most version-portable way to scan.
        self._proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        try:
            self._proc.stdin.write("scan on\n")
            self._proc.stdin.flush()
        except Exception:
            pass

        for line in self._proc.stdout:
            if not self._running:
                break
            if "No default controller" in line:
                log.warning("No Bluetooth controller available — is hci0 up? "
                            "(rfkill unblock bluetooth)")
                time.sleep(10)
                break
            upd = parse_btctl_line(line)
            if upd:
                self._handle(upd)

        try:
            self._proc.terminate()
        except Exception:
            pass

    def _handle(self, upd: dict):
        if upd["action"] == "DEL":
            return
        mac = upd["mac"]
        dev = self._devices.setdefault(mac, {"name": "", "rssi": None, "last_emit": 0.0})
        changed = upd["action"] == "NEW"
        if upd.get("name") and upd["name"] != dev["name"]:
            dev["name"] = upd["name"]
            changed = True
        if "rssi" in upd and upd["rssi"] != dev["rssi"]:
            dev["rssi"] = upd["rssi"]
            changed = True

        now = time.time()
        if changed and (upd["action"] == "NEW" or now - dev["last_emit"] >= EMIT_INTERVAL):
            dev["last_emit"] = now
            vendor = self._vendor_lookup(mac) or ""
            rssi = dev["rssi"] if dev["rssi"] is not None else 0
            try:
                self._on_device(mac, dev["name"], rssi, vendor)
            except Exception as e:
                log.error("on_device callback error: %s", e)
