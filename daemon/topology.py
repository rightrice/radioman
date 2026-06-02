"""
topology.py — L3 / VLAN topology discovery for the network graph.

Two complementary methods:
  • traceroute — maps the routed path out (gateway → internal L3 hops →
    internet edge); reveals routers / L3 switches between subnets.
  • SNMP (read-only) — walks a target (gateway / L3 switch) for its
    interfaces/SVIs (→ subnets), the VLAN database (IDs + names) and the
    cross-VLAN ARP table, building a real multi-VLAN map.

Both degrade gracefully: if `traceroute`/`snmp` tools aren't installed, or
SNMP isn't configured, that part is skipped and a note is surfaced.

CLI tools used (apt: traceroute, snmp):
  traceroute, snmpbulkwalk/snmpwalk, snmpget
"""

import ipaddress
import logging
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger("topology")

# ── SNMP OIDs ──────────────────────────────────────────────────────────────────
OID_SYSNAME   = ".1.3.6.1.2.1.1.5.0"
OID_SYSDESCR  = ".1.3.6.1.2.1.1.1.0"
OID_IP_MASK   = ".1.3.6.1.2.1.4.20.1.3"          # ipAdEntNetMask  (suffix = IP)
OID_IP_IFIDX  = ".1.3.6.1.2.1.4.20.1.2"          # ipAdEntIfIndex  (suffix = IP)
OID_IFDESCR   = ".1.3.6.1.2.1.2.2.1.2"           # ifDescr         (suffix = ifIndex)
OID_VLAN_NAME = ".1.3.6.1.2.1.17.7.1.4.3.1.1"    # dot1qVlanStaticName (suffix = VLAN id)
OID_ARP_MAC   = ".1.3.6.1.2.1.4.22.1.2"          # ipNetToMediaPhysAddress (suffix = ifIdx.IP)


# ── traceroute ──────────────────────────────────────────────────────────────────
def traceroute(target: str, max_hops: int = 12, timeout: int = 2) -> list:
    """Return [{hop, ip}] for the routed path to target. [] if unavailable."""
    if not shutil.which("traceroute"):
        return []
    try:
        out = subprocess.check_output(
            ["traceroute", "-n", "-w", str(timeout), "-q", "1", "-m", str(max_hops), target],
            text=True, timeout=max_hops * timeout + 10, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.debug("traceroute %s: %s", target, e)
        return []
    return parse_traceroute(out)


def parse_traceroute(text: str) -> list:
    hops = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if not m:
            continue
        hop = int(m.group(1))
        ipm = re.search(r"(\d+\.\d+\.\d+\.\d+)", m.group(2))
        hops.append({"hop": hop, "ip": ipm.group(1) if ipm else ""})
    return hops


# ── SNMP ────────────────────────────────────────────────────────────────────────
def snmp_available() -> bool:
    return bool(shutil.which("snmpbulkwalk") or shutil.which("snmpwalk"))


def _snmp_walk(community: str, target: str, base_oid: str,
               version: str = "2c", timeout: int = 3) -> list:
    """Walk base_oid; return [(suffix, value)] where suffix follows base_oid."""
    tool = shutil.which("snmpbulkwalk") or shutil.which("snmpwalk")
    if not tool:
        return []
    cmd = [tool, "-v", version, "-c", community, "-Oqn",
           "-t", str(timeout), "-r", "1", target, base_oid]
    try:
        out = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout * 4 + 5)
    except Exception as e:
        log.debug("snmp walk %s: %s", base_oid, e)
        return []
    if out.returncode != 0:
        return []
    return _parse_snmp(out.stdout, base_oid)


def _snmp_get(community: str, target: str, oid: str,
              version: str = "2c", timeout: int = 3) -> str:
    if not shutil.which("snmpget"):
        return ""
    try:
        out = subprocess.run(
            ["snmpget", "-v", version, "-c", community, "-Oqv",
             "-t", str(timeout), "-r", "1", target, oid],
            text=True, capture_output=True, timeout=timeout * 2 + 5)
    except Exception:
        return ""
    return out.stdout.strip().strip('"') if out.returncode == 0 else ""


def _parse_snmp(text: str, base_oid: str) -> list:
    base = base_oid.rstrip(".")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("."):
            continue
        parts = line.split(None, 1)
        oid = parts[0]
        val = parts[1].strip().strip('"') if len(parts) > 1 else ""
        if not oid.startswith(base):
            continue
        suffix = oid[len(base):].lstrip(".")
        out.append((suffix, val))
    return out


def snmp_discover(community: str, target: str, version: str = "2c") -> dict:
    """Walk a target's IP/interface/VLAN/ARP tables into a structured dict."""
    sysname  = _snmp_get(community, target, OID_SYSNAME, version)
    sysdescr = _snmp_get(community, target, OID_SYSDESCR, version)

    masks   = dict(_snmp_walk(community, target, OID_IP_MASK, version))   # ip -> mask
    ifidx   = dict(_snmp_walk(community, target, OID_IP_IFIDX, version))  # ip -> ifIndex
    ifnames = dict(_snmp_walk(community, target, OID_IFDESCR, version))   # ifIndex -> name
    vlans_raw = _snmp_walk(community, target, OID_VLAN_NAME, version)     # [(vid, name)]

    subnets = []
    for ip, mask in masks.items():
        cidr = _to_cidr(ip, mask)
        if not cidr:
            continue
        idx = ifidx.get(ip, "")
        ifname = ifnames.get(idx, "")
        subnets.append({"ip": ip, "mask": mask, "cidr": cidr,
                        "ifindex": idx, "ifname": ifname,
                        "vlan": _vlan_from_ifname(ifname)})

    vlans = [{"id": vid, "name": name} for vid, name in vlans_raw]

    arp = []
    for suffix, mac in _snmp_walk(community, target, OID_ARP_MAC, version):
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)$", suffix)
        if m:
            arp.append({"ip": m.group(1), "mac": _norm_mac(mac)})

    reachable = bool(sysname or subnets or vlans)
    return {"sysname": sysname, "sysdescr": sysdescr, "subnets": subnets,
            "vlans": vlans, "arp": arp, "reachable": reachable}


# ── helpers ─────────────────────────────────────────────────────────────────────
def _to_cidr(ip: str, mask: str) -> str:
    try:
        net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
        return str(net)
    except Exception:
        return ""


def _vlan_from_ifname(name: str) -> str:
    m = re.search(r"[Vv]lan\s*0*(\d+)", name or "")
    return m.group(1) if m else ""


def _norm_mac(raw: str) -> str:
    hexes = re.findall(r"[0-9a-fA-F]{1,2}", raw or "")
    if len(hexes) == 6:
        return ":".join(h.zfill(2).upper() for h in hexes)
    return (raw or "").upper()


def default_gateway() -> str:
    try:
        out = subprocess.check_output(["ip", "route", "show", "default"],
                                      text=True, timeout=3)
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except Exception:
        return False


def _ip_in_cidr(ip: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except Exception:
        return False


# ── Mapper ──────────────────────────────────────────────────────────────────────
class TopologyMapper:
    def __init__(self, iface="wlan0", db_path="", snmp_community="",
                 snmp_target="", snmp_version="2c"):
        self._iface     = iface
        self._db_path   = db_path
        self.community  = snmp_community
        self.target     = snmp_target
        self.version    = snmp_version or "2c"
        self._lock      = threading.Lock()
        self._graph     = {"nodes": [], "edges": [], "meta": {}}
        self._scan      = {"scanning": False, "last_scan": 0, "last_error": "", "note": ""}

    def set_snmp(self, community, target, version):
        self.community = community or ""
        self.target    = target or ""
        self.version   = version or "2c"

    def get(self) -> dict:
        with self._lock:
            return {**self._graph, "scan": dict(self._scan)}

    def scan_status(self) -> dict:
        with self._lock:
            return dict(self._scan)

    def start_scan(self) -> bool:
        with self._lock:
            if self._scan["scanning"]:
                return False
            self._scan["scanning"] = True
            self._scan["last_error"] = ""
        threading.Thread(target=self._run, daemon=True, name="topology").start()
        return True

    def _run(self):
        try:
            graph = self.build()
            with self._lock:
                self._graph = graph
                self._scan["last_scan"] = time.time()
                self._scan["note"] = graph.get("meta", {}).get("note", "")
        except Exception as e:
            log.error("topology build failed: %s", e)
            with self._lock:
                self._scan["last_error"] = str(e)
        finally:
            with self._lock:
                self._scan["scanning"] = False

    def build(self) -> dict:
        import db as _db
        nodes, edges, notes = {}, [], []

        def add(nid, **kw): nodes[nid] = {"id": nid, **kw}
        def link(a, b): edges.append({"source": a, "target": b})

        add("self", label="radioman", type="self")
        gw = default_gateway()
        gw_id = gw or "gateway"
        add(gw_id, label=gw or "Gateway", type="gateway", ip=gw)
        link("self", gw_id)

        # ── traceroute: routed path out ────────────────────────────────────────
        if shutil.which("traceroute"):
            prev = gw_id
            for h in traceroute("1.1.1.1"):
                if not h["ip"] or h["ip"] == gw:
                    continue
                if _is_private(h["ip"]):
                    add(h["ip"], label=h["ip"], type="router", ip=h["ip"])
                    link(prev, h["ip"]); prev = h["ip"]
                else:
                    add("internet", label="Internet", type="internet")
                    link(prev, "internet")
                    break
        else:
            notes.append("traceroute not installed (sudo apt install traceroute)")

        # ── SNMP: subnets / VLANs / ARP ────────────────────────────────────────
        subnet_ids = {}        # cidr -> node id
        vlans = []
        if self.community:
            if not snmp_available():
                notes.append("snmp tools not installed (sudo apt install snmp)")
            else:
                tgt = self.target or gw
                snmp = snmp_discover(self.community, tgt, self.version) if tgt else {}
                if not snmp.get("reachable"):
                    notes.append(f"SNMP target {tgt or '(none)'} did not respond")
                else:
                    if snmp.get("sysname"):
                        nodes[gw_id]["label"] = snmp["sysname"]
                        nodes[gw_id]["sysdescr"] = snmp.get("sysdescr", "")
                    vlans = snmp.get("vlans", [])
                    vlan_names = {v["id"]: v["name"] for v in vlans}
                    for s in snmp.get("subnets", []):
                        if s["cidr"].startswith("127."):
                            continue
                        vid = s.get("vlan", "")
                        vname = vlan_names.get(vid, "")
                        bits = [s["cidr"]]
                        if vid:
                            bits.append(f"VLAN {vid}" + (f" ({vname})" if vname else ""))
                        elif s.get("ifname"):
                            bits.append(s["ifname"])
                        sid = "net:" + s["cidr"]
                        add(sid, label="  ·  ".join(bits), type="subnet",
                            cidr=s["cidr"], ifname=s.get("ifname", ""),
                            vlan=vid, vlan_name=vname)
                        link(gw_id, sid)
                        subnet_ids[s["cidr"]] = sid

        # ── local hosts → attach to matching subnet, else the gateway ──────────
        try:
            hosts = _db.get_hosts(self._db_path) if self._db_path else []
        except Exception:
            hosts = []
        for h in hosts:
            ip = h.get("ip", "")
            if not ip or ip == gw:
                continue
            parent = gw_id
            for cidr, sid in subnet_ids.items():
                if _ip_in_cidr(ip, cidr):
                    parent = sid
                    break
            hid = "host:" + ip
            add(hid, label=h.get("hostname") or h.get("vendor") or ip, type="host",
                ip=ip, mac=h.get("mac", ""), vendor=h.get("vendor", ""),
                hostname=h.get("hostname", ""))
            link(parent, hid)

        meta = {"gateway": gw, "vlans": vlans,
                "subnets": list(subnet_ids.keys()),
                "note": " · ".join(notes)}
        return {"nodes": list(nodes.values()), "edges": edges, "meta": meta}
