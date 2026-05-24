import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger("capture")

BETTERCAP_TIMEOUT = 5


class CaptureEngine:
    def __init__(self, config: dict,
                 on_network: Callable,
                 on_client: Callable,
                 on_capture: Callable):
        self._cfg        = config
        self._on_network = on_network
        self._on_client  = on_client
        self._on_capture = on_capture
        self._proc: Optional[subprocess.Popen] = None
        self._running    = False
        self._lock       = threading.Lock()
        self._seen_caps  = set()

        self._host  = config.get("bettercap_host", "127.0.0.1")
        self._port  = int(config.get("bettercap_port", 8081))
        self._auth  = HTTPBasicAuth(
            config.get("bettercap_user", "user"),
            config.get("bettercap_pass", "pass"),
        )
        self._iface       = config.get("interface", "wlan0")
        self._captures_dir = config.get("captures_dir", "/opt/radioman/captures")
        self._caplet      = config.get("caplet", "/opt/radioman/radioman.cap")

    @property
    def _api(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _bc(self, method: str, path: str, **kwargs):
        try:
            resp = requests.request(
                method, f"{self._api}{path}",
                auth=self._auth, timeout=BETTERCAP_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug("bettercap API %s %s: %s", method, path, e)
            return None

    def _start_bettercap(self):
        bc = shutil.which("bettercap")
        if not bc:
            log.warning("bettercap not found — capture disabled. Install with: sudo apt install bettercap")
            return
        eval_cmds = (
            f"set api.rest.address {self._host}:{self._port}; "
            f"set api.rest.username {self._auth.username}; "
            f"set api.rest.password {self._auth.password}; "
            "api.rest on"
        )
        cmd = [
            bc,
            "-iface", self._iface,
            "-caplet", self._caplet,
            "-eval", eval_cmds,
            "-no-colors",
        ]
        log.info("Starting bettercap: %s", " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(4)
        except Exception as e:
            log.error("Failed to start bettercap: %s", e)
            self._proc = None

    def _stop_bettercap(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def _poll(self):
        data = self._bc("GET", "/api/session/wifi")
        if not data:
            return

        aps     = data.get("aps", [])
        clients = data.get("stations", [])

        for ap in aps:
            bssid    = ap.get("mac", "").upper()
            ssid     = ap.get("essid", "") or ""
            channel  = ap.get("channel", 0)
            rssi     = ap.get("rssi", 0)
            security = _parse_security(ap.get("authentication", ""))
            vendor   = ap.get("vendor", "")
            if bssid:
                self._on_network(bssid, ssid, channel, rssi, security, vendor)

        for sta in clients:
            mac   = sta.get("mac", "").upper()
            bssid = sta.get("ap_mac", "").upper()
            rssi  = sta.get("rssi", 0)
            vendor = sta.get("vendor", "")
            if mac:
                self._on_client(mac, bssid, rssi, vendor)

        self._scan_new_captures()

    def _scan_new_captures(self):
        try:
            files = os.listdir(self._captures_dir)
        except FileNotFoundError:
            return

        for fname in files:
            if fname in self._seen_caps:
                continue
            if not fname.endswith((".pcap", ".pcapng", ".cap")):
                continue
            self._seen_caps.add(fname)
            path = os.path.join(self._captures_dir, fname)
            bssid, ssid, cap_type = _parse_capture_filename(fname)
            log.info("New capture: %s (%s / %s)", fname, ssid, cap_type)
            self._on_capture(path, bssid, ssid, cap_type)

    def _monitor_loop(self):
        while self._running:
            if self._proc is None or self._proc.poll() is not None:
                if shutil.which("bettercap"):
                    log.warning("bettercap not running — restarting...")
                    self._start_bettercap()
                else:
                    time.sleep(30)
                    continue
            self._poll()
            time.sleep(5)

    def start(self):
        os.makedirs(self._captures_dir, exist_ok=True)
        self._running = True
        self._start_bettercap()
        t = threading.Thread(target=self._monitor_loop, daemon=True, name="capture")
        t.start()
        log.info("Capture engine started on %s", self._iface)

    def stop(self):
        self._running = False
        self._stop_bettercap()
        log.info("Capture engine stopped")

    def send_cmd(self, cmd: str) -> bool:
        result = self._bc("POST", "/api/session",
                          json={"cmd": cmd})
        return result is not None


def _parse_security(auth: str) -> str:
    auth = (auth or "").upper()
    if "WPA3" in auth:   return "WPA3"
    if "WPA2" in auth:   return "WPA2"
    if "WPA"  in auth:   return "WPA"
    if "WEP"  in auth:   return "WEP"
    if "OPEN" in auth:   return "OPEN"
    return auth or "UNKNOWN"


def _parse_capture_filename(fname: str):
    """
    bettercap names captures like: <BSSID>_<ESSID>.pcap
    or handshake_<BSSID>_<ESSID>.pcap
    Best-effort extraction.
    """
    base = fname.replace(".pcapng", "").replace(".pcap", "").replace(".cap", "")
    parts = base.split("_")
    bssid = ""
    ssid  = ""
    if len(parts) >= 2:
        candidate = parts[0] if ":" in parts[0] else (parts[1] if len(parts) > 1 else "")
        if len(candidate) == 17 and candidate.count(":") == 5:
            bssid = candidate.upper()
            ssid  = "_".join(parts[1:]) if parts[0] == bssid else "_".join(parts[2:])
        else:
            ssid = base
    cap_type = "PMKID" if "pmkid" in fname.lower() else "EAPOL"
    return bssid, ssid, cap_type
