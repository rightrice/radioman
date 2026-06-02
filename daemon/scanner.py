import logging
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable

log = logging.getLogger("scanner")

# OUI vendor databases shipped by common packages, in priority order.
# nmap is a radioman dependency, so nmap-mac-prefixes is normally present.
_OUI_FILES = [
    "/usr/share/nmap/nmap-mac-prefixes",   # nmap
    "/usr/share/ieee-data/oui.txt",        # ieee-data package
    "/var/lib/ieee-data/oui.txt",
]


class NetworkScanner:
    """
    Passive neighbour-table (ARP) discovery + optional nmap scan for host
    discovery on the management interface (e.g. wlan0 in client mode).
    Active scanning is only triggered on demand from the web UI.

    Discovered hosts are enriched with OUI vendor and best-effort reverse-DNS
    hostname, then handed to the on_host callback for persistence.
    """

    def __init__(self, iface: str = "wlan0", on_host: Callable = None,
                 interval: int = 30, target: str = ""):
        self._iface     = iface
        self._on_host   = on_host
        self._interval  = interval
        self._target    = (target or "").strip()   # configured nmap target override
        self._running   = False
        self._results   = {}      # ip -> host dict (in-memory snapshot)
        self._hostnames = {}      # ip -> hostname (cached, incl. negatives)
        self._oui       = None    # lazy-loaded {prefix: vendor}
        self._lock      = threading.Lock()
        self._scan      = {"scanning": False, "last_scan": 0,
                           "last_count": 0, "last_error": ""}

    def set_target(self, target: str):
        self._target = (target or "").strip()

    # ── Neighbour table (passive, zero packets) ────────────────────────────────
    def arp_scan(self) -> list:
        """
        Read the kernel neighbour (ARP) table. Prefers `ip neigh` (always
        present via iproute2); falls back to `arp -a -n` (net-tools, often
        absent on Ubuntu Server). Emits zero packets.
        """
        hosts = self._ip_neigh()
        if not hosts:
            hosts = self._arp_legacy()
        for h in hosts:
            self._enrich(h)
        return hosts

    def _ip_neigh(self) -> list:
        hosts = []
        try:
            out = subprocess.check_output(
                ["ip", "-4", "neigh", "show"], text=True, timeout=5,
            )
        except Exception as e:
            log.debug("ip neigh: %s", e)
            return hosts
        for line in out.splitlines():
            # e.g. "192.168.1.5 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"
            parts = line.split()
            if "lladdr" not in parts:
                continue
            state = parts[-1].upper()
            if state in ("FAILED", "INCOMPLETE"):
                continue
            mac = parts[parts.index("lladdr") + 1].upper()
            if not _is_unicast(mac):
                continue
            hosts.append({"ip": parts[0], "mac": mac, "method": "arp"})
        return hosts

    def _arp_legacy(self) -> list:
        hosts = []
        try:
            out = subprocess.check_output(["arp", "-a", "-n"],
                                          text=True, timeout=5)
        except Exception as e:
            log.debug("arp -a: %s", e)
            return hosts
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1].startswith("("):
                mac = parts[3].upper()
                if not _is_unicast(mac):
                    continue
                hosts.append({"ip": parts[1].strip("()"),
                              "mac": mac, "method": "arp"})
        return hosts

    # ── nmap (active, on demand) ───────────────────────────────────────────────
    def start_nmap_scan(self, target: str = None) -> bool:
        """Kick off an nmap sweep in the background. Returns False if one is
        already running (so the UI can stay responsive instead of blocking)."""
        with self._lock:
            if self._scan["scanning"]:
                return False
            self._scan["scanning"] = True
            self._scan["last_error"] = ""
        threading.Thread(target=self._run_nmap, args=(target,),
                         daemon=True, name="nmap").start()
        return True

    def _run_nmap(self, target):
        try:
            hosts = self.nmap_scan(target)
            with self._lock:
                self._scan["last_count"] = len(hosts)
                self._scan["last_scan"]  = time.time()
        finally:
            with self._lock:
                self._scan["scanning"] = False

    def scan_status(self) -> dict:
        with self._lock:
            return dict(self._scan)

    def _set_error(self, msg: str):
        log.warning("nmap: %s", msg)
        with self._lock:
            self._scan["last_error"] = msg

    def nmap_scan(self, target: str = None) -> list:
        """Active nmap ping-sweep. Prefers an explicit target, then the
        configured target, then the wlan0 subnet."""
        target = (target or self._target or _iface_subnet(self._iface)).strip()
        if not target:
            self._set_error("Could not determine the network to scan — "
                            "set a scan target in Settings.")
            return []

        log.info("nmap scan: %s", target)
        hosts = []
        try:
            out = subprocess.check_output(
                ["nmap", "-sn", "-oX", "-", target],
                text=True, timeout=120, stderr=subprocess.DEVNULL,
            )
            root = ET.fromstring(out)
            for host in root.findall("host"):
                ip  = _nmap_addr(host, "ipv4")
                mac = _nmap_addr(host, "mac")
                vendor = ""
                mac_el = host.find(".//address[@addrtype='mac']")
                if mac_el is not None:
                    vendor = mac_el.get("vendor", "")
                if ip:
                    h = {"ip": ip, "mac": mac.upper(),
                         "vendor": vendor, "method": "nmap"}
                    self._enrich(h)
                    hosts.append(h)
        except FileNotFoundError:
            self._set_error("nmap is not installed (sudo apt install nmap).")
        except subprocess.TimeoutExpired:
            self._set_error("nmap timed out.")
        except Exception as e:
            self._set_error(f"nmap failed: {e}")

        self._store(hosts)
        return hosts

    # ── Enrichment ─────────────────────────────────────────────────────────────
    def _enrich(self, host: dict):
        """Add OUI vendor (if missing) and reverse-DNS hostname in place."""
        if not host.get("vendor"):
            host["vendor"] = self._vendor_for(host.get("mac", ""))
        host["hostname"] = self._hostname_for(host.get("ip", ""))

    def _vendor_for(self, mac: str) -> str:
        if not mac:
            return ""
        with self._lock:
            if self._oui is None:
                self._oui = _load_oui()
            return self._oui.get(
                mac.upper().replace(":", "").replace("-", "")[:6], "")

    def _hostname_for(self, ip: str) -> str:
        if not ip:
            return ""
        with self._lock:
            if ip in self._hostnames:        # cached (incl. negative results)
                return self._hostnames[ip]
        name = ""
        old = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(0.5)
            name = socket.gethostbyaddr(ip)[0].rstrip(".")
        except Exception:
            name = ""
        finally:
            socket.setdefaulttimeout(old)
        with self._lock:
            self._hostnames[ip] = name
        return name

    # ── Storage / lifecycle ────────────────────────────────────────────────────
    def _store(self, hosts: list):
        with self._lock:
            for h in hosts:
                self._results[h["ip"]] = h
        if self._on_host:
            for h in hosts:
                self._on_host(h)

    def get_hosts(self) -> list:
        with self._lock:
            return list(self._results.values())

    def _passive_loop(self):
        while self._running:
            self._store(self.arp_scan())
            time.sleep(self._interval)

    def start(self):
        self._running = True
        threading.Thread(target=self._passive_loop,
                         daemon=True, name="scanner").start()
        log.info("Network scanner started (passive ARP/neigh, interface: %s)",
                 self._iface)

    def stop(self):
        self._running = False


def _is_unicast(mac: str) -> bool:
    """Reject broadcast (ff:ff:..), IPv4/IPv6 multicast (low bit of first
    octet set), and unparseable/empty MACs — keep only real unicast hosts."""
    try:
        first = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return (first & 1) == 0


def _load_oui() -> dict:
    """Parse an OUI prefix→vendor map from the first available database file."""
    for path in _OUI_FILES:
        oui = {}
        try:
            with open(path, "r", errors="ignore") as fh:
                if path.endswith("nmap-mac-prefixes"):
                    for line in fh:                       # "2CCF67 Raspberry Pi"
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        prefix, _, name = line.partition(" ")
                        if len(prefix) == 6 and name:
                            oui[prefix.upper()] = name.strip()
                else:                                     # ieee "AA-BB-CC (hex) Vendor"
                    for line in fh:
                        if "(hex)" in line:
                            pfx, _, name = line.partition("(hex)")
                            pfx = pfx.strip().replace("-", "").upper()
                            if len(pfx) == 6 and name.strip():
                                oui[pfx] = name.strip()
        except FileNotFoundError:
            continue
        except Exception as e:
            log.debug("OUI load %s: %s", path, e)
            continue
        if oui:
            log.info("Loaded %d OUI prefixes from %s", len(oui), path)
            return oui
    log.debug("No OUI database found — vendor lookup disabled")
    return {}


def _iface_subnet(iface: str) -> str:
    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show", iface],
            text=True, timeout=3,
        )
        for part in out.split():
            if "/" in part and not part.startswith("127"):
                ip, prefix = part.split("/")
                octets = ip.split(".")
                return f"{'.'.join(octets[:3])}.0/{prefix}"
    except Exception:
        pass
    return ""


def _nmap_addr(host, addrtype: str) -> str:
    el = host.find(f".//address[@addrtype='{addrtype}']")
    return el.get("addr", "") if el is not None else ""
