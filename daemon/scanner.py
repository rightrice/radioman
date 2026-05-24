import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable

log = logging.getLogger("scanner")


class NetworkScanner:
    """
    Passive ARP + optional nmap scan for host discovery on the
    management interface (e.g. wlan0 in AP/client mode).
    Active scanning is only triggered on demand from the web UI.
    """

    def __init__(self, iface: str = "wlan0", on_host: Callable = None):
        self._iface   = iface
        self._on_host = on_host
        self._running = False
        self._results = {}
        self._lock    = threading.Lock()

    def arp_scan(self) -> list:
        """Read ARP table — zero noise, zero packets."""
        hosts = []
        try:
            out = subprocess.check_output(["arp", "-a", "-n"],
                                          text=True, timeout=5)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[1].startswith("("):
                    ip  = parts[1].strip("()")
                    mac = parts[3].upper()
                    hosts.append({"ip": ip, "mac": mac, "method": "arp"})
        except Exception as e:
            log.debug("arp scan: %s", e)
        return hosts

    def nmap_scan(self, target: str = None) -> list:
        """Active nmap scan — call only on user request."""
        target = target or _iface_subnet(self._iface)
        if not target:
            log.warning("Could not determine subnet for nmap")
            return []

        log.info("nmap scan: %s", target)
        hosts = []
        try:
            out = subprocess.check_output(
                ["nmap", "-sn", "-oX", "-", target],
                text=True, timeout=60,
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
                    hosts.append({"ip": ip, "mac": mac.upper(),
                                  "vendor": vendor, "method": "nmap"})
        except FileNotFoundError:
            log.warning("nmap not found — install with: sudo apt install nmap")
        except subprocess.TimeoutExpired:
            log.warning("nmap timed out")
        except Exception as e:
            log.debug("nmap: %s", e)

        with self._lock:
            for h in hosts:
                self._results[h["ip"]] = h
                if self._on_host:
                    self._on_host(h)

        return hosts

    def get_hosts(self) -> list:
        with self._lock:
            return list(self._results.values())

    def _passive_loop(self):
        while self._running:
            hosts = self.arp_scan()
            with self._lock:
                for h in hosts:
                    self._results[h["ip"]] = h
            time.sleep(30)

    def start(self):
        self._running = True
        t = threading.Thread(target=self._passive_loop,
                             daemon=True, name="scanner")
        t.start()
        log.info("Network scanner started (passive ARP, interface: %s)", self._iface)

    def stop(self):
        self._running = False


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
