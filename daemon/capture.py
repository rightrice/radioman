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
        self._running     = False   # monitor thread alive
        self._scan_active = False   # user has started scanning
        self._lock        = threading.Lock()
        self._seen_caps   = set()

        self._host  = config.get("bettercap_host", "127.0.0.1")
        self._port  = int(config.get("bettercap_port", 8081))
        self._auth  = HTTPBasicAuth(
            config.get("bettercap_user", "user"),
            config.get("bettercap_pass", "pass"),
        )
        self._iface       = config.get("interface", "wlan0")
        self._mon_iface   = config.get("monitor_interface", "mon0")
        self._captures_dir = config.get("captures_dir", "/opt/radioman/captures")
        self._caplet      = config.get("caplet", "/opt/radioman/radioman.cap")
        self._session     = requests.Session()
        self._session.auth = self._auth

    @property
    def _api(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _bc(self, method: str, path: str, **kwargs):
        try:
            resp = self._session.request(
                method, f"{self._api}{path}",
                timeout=BETTERCAP_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug("bettercap API %s %s: %s", method, path, e)
            return None

    def _get_phy(self) -> str:
        try:
            r = subprocess.run(["iw", "dev", self._iface, "info"],
                               capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if "wiphy" in line:
                    return f"phy{line.split()[-1]}"
        except Exception:
            pass
        return "phy0"

    def _setup_monitor(self):
        # Remove stale interface from a previous run
        subprocess.run(["iw", "dev", self._mon_iface, "del"],
                       capture_output=True)
        phy = self._get_phy()
        subprocess.run(
            ["iw", "phy", phy, "interface", "add", self._mon_iface, "type", "monitor"],
            check=True,
        )
        subprocess.run(["ip", "link", "set", self._mon_iface, "up"], check=True)
        log.info("Monitor interface %s created on %s", self._mon_iface, phy)

    def _teardown_monitor(self):
        try:
            subprocess.run(["ip", "link", "set", self._mon_iface, "down"],
                           capture_output=True)
            subprocess.run(["iw", "dev", self._mon_iface, "del"],
                           capture_output=True)
            log.info("Monitor interface %s removed", self._mon_iface)
        except Exception as e:
            log.debug("teardown monitor: %s", e)

    def _nm_release(self):
        """Tell NetworkManager to stop managing the interface so bettercap can take over."""
        if shutil.which("nmcli"):
            try:
                subprocess.run(
                    ["nmcli", "device", "disconnect", self._iface],
                    capture_output=True, timeout=5,
                )
                log.info("NetworkManager released %s", self._iface)
            except Exception as e:
                log.debug("nmcli disconnect: %s", e)

    def _nm_reclaim(self):
        """Hand the interface back to NetworkManager after scanning stops."""
        if shutil.which("nmcli"):
            try:
                subprocess.run(
                    ["nmcli", "device", "connect", self._iface],
                    capture_output=True, timeout=10,
                )
                log.info("NetworkManager reclaimed %s", self._iface)
            except Exception as e:
                log.debug("nmcli connect: %s", e)

    def _wait_for_api(self, timeout: int = 30) -> bool:
        """Poll until bettercap REST API responds or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._bc("GET", "/api/session") is not None:
                log.info("bettercap API ready")
                return True
            time.sleep(1)
        log.error("bettercap API did not become ready within %ds", timeout)
        return False

    def _start_bettercap(self):
        bc = shutil.which("bettercap")
        if not bc:
            log.warning("bettercap not found — capture disabled. Install with: sudo apt install bettercap")
            return
        self._nm_release()
        try:
            self._setup_monitor()
        except Exception as e:
            log.error("Failed to create monitor interface %s: %s", self._mon_iface, e)
            return
        cmd = [
            bc,
            "-iface", self._mon_iface,
            "-caplet", self._caplet,
            "-no-colors",
        ]
        log.info("Starting bettercap: %s", " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if not self._wait_for_api():
                # Log whatever bettercap printed to help diagnose
                try:
                    out, _ = self._proc.communicate(timeout=1)
                    log.error("bettercap output: %s", out.decode(errors="replace"))
                except Exception:
                    pass
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
                self._proc.wait()
            self._proc = None
        self._teardown_monitor()
        self._nm_reclaim()

    def _poll(self):
        data = self._bc("GET", "/api/session/wifi")
        if not data:
            log.debug("bettercap wifi poll returned no data")
            return

        aps     = data.get("aps") or []
        clients = data.get("stations") or []
        log.debug("bettercap poll: %d APs, %d stations (keys: %s)",
                  len(aps), len(clients), list(data.keys()))

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

    @property
    def scanning(self) -> bool:
        return self._scan_active

    def start_scan(self):
        """Start bettercap and begin polling. Call this manually to begin a session."""
        if self._scan_active:
            return
        self._scan_active = True
        log.info("Scan started — launching bettercap on %s (via %s)", self._mon_iface, self._iface)
        self._start_bettercap()

    def stop_scan(self):
        """Stop bettercap and halt polling. Data already in DB is preserved."""
        if not self._scan_active:
            return
        self._scan_active = False
        self._stop_bettercap()
        log.info("Scan stopped")

    def _monitor_loop(self):
        while self._running:
            if not self._scan_active:
                time.sleep(2)
                continue
            # Restart bettercap if it crashed while scanning should be active
            if self._proc is None or self._proc.poll() is not None:
                if shutil.which("bettercap"):
                    log.warning("bettercap exited unexpectedly — restarting...")
                    self._start_bettercap()
                else:
                    time.sleep(30)
                    continue
            self._poll()
            time.sleep(5)

    def start(self):
        """Start the monitor thread. Does NOT start bettercap — call start_scan() for that."""
        os.makedirs(self._captures_dir, exist_ok=True)
        self._running = True
        t = threading.Thread(target=self._monitor_loop, daemon=True, name="capture")
        t.start()
        log.info("Capture engine ready (scanning is off — use the dashboard to start)")

    def stop(self):
        self._scan_active = False
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
    or handshake_<BSSID>_<ESSID>.pcap  (prefix varies)
    Best-effort extraction; degrades gracefully on unexpected names.
    """
    base  = fname.replace(".pcapng", "").replace(".pcap", "").replace(".cap", "")
    parts = base.split("_")
    bssid = ""
    ssid  = ""

    def _is_bssid(s: str) -> bool:
        return len(s) == 17 and s.count(":") == 5

    # Walk parts looking for a MAC-address-shaped token
    for i, part in enumerate(parts):
        if _is_bssid(part):
            bssid = part.upper()
            ssid  = "_".join(parts[i + 1:])
            break
    else:
        ssid = base  # no BSSID found — use whole stem as SSID

    cap_type = "PMKID" if "pmkid" in fname.lower() else "EAPOL"
    return bssid, ssid, cap_type
