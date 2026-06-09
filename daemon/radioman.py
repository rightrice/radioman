#!/usr/bin/env python3
"""
radioman — main orchestrator
Starts all subsystems and runs the main loop.
"""

import configparser
import logging
import os
import signal
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import db
import fingerprint
import battery as bat
from personality import PersonalityEngine
from display import Display
from capture import CaptureEngine
from cracker import CrackQueue, CrackJob
from scanner import NetworkScanner
from wifiscan import WifiScanner
from topology import TopologyMapper
from xplt import XpltSync
from ai import AIEngine
from api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("radioman")

DEFAULT_CONFIG = os.environ.get(
    "RADIOMAN_CONFIG",
    os.path.join(BASE_DIR, "radioman.conf"),
)


def load_config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg


def flat(cfg: configparser.ConfigParser, section: str) -> dict:
    try:
        return dict(cfg[section])
    except KeyError:
        return {}


class Radioman:
    def __init__(self, config_path: str):
        self._conf_path = config_path
        cfg = load_config(config_path)

        main_cfg    = flat(cfg, "radioman")
        capture_cfg = flat(cfg, "capture")
        crack_cfg   = flat(cfg, "cracker")
        db_cfg      = flat(cfg, "database")
        pisugar_cfg = flat(cfg, "pisugar")
        display_cfg = flat(cfg, "display")
        xplt_cfg    = flat(cfg, "xplt")
        wifiscan_cfg = flat(cfg, "wifiscan")
        scan_cfg    = flat(cfg, "scan")
        snmp_cfg    = flat(cfg, "snmp")

        self._scan_target = scan_cfg.get("target", "").strip()
        self._my_bssid    = scan_cfg.get("my_bssid", "").strip().upper()
        self._my_ssid     = scan_cfg.get("my_ssid", "").strip()
        self._snmp_community = snmp_cfg.get("community", "").strip()
        self._snmp_target    = snmp_cfg.get("target", "").strip()
        self._snmp_version   = snmp_cfg.get("version", "2c").strip() or "2c"

        self._iface       = main_cfg.get("interface", "wlan0")
        self._web_port    = int(main_cfg.get("web_port", 8080))
        self._disp_period = int(main_cfg.get("display_refresh", 5))
        self._pers_period = int(main_cfg.get("personality_interval", 30))

        self._db_path     = db_cfg.get("path", "/opt/radioman/radioman.db")
        self._rssi_hours  = int(db_cfg.get("rssi_history_hours", 24))

        log.info("Config: interface=%s  web_port=%d  db=%s",
                 self._iface, self._web_port, self._db_path)
        log.info("Config: display=%s rotate=%s  refresh=%ds",
                 display_cfg.get("model", "epd2in13_V4"),
                 display_cfg.get("rotate", 180),
                 self._disp_period)
        log.info("Config: wordlist=%s  max_jobs=%s",
                 crack_cfg.get("wordlist", "/opt/radioman/wordlists/rockyou.txt"),
                 crack_cfg.get("max_jobs", 1))
        log.info("Config: XPLT token %s",
                 "present" if xplt_cfg.get("device_token") else "not set (sync disabled)")

        db.init(self._db_path)

        capture_cfg["interface"]   = self._iface
        capture_cfg["caplet"]      = os.path.join(BASE_DIR, "radioman.cap")

        self.xplt_sync   = XpltSync(xplt_cfg, self._db_path, conf_path=config_path)
        self.ai          = AIEngine(db_path=self._db_path)
        self.personality = PersonalityEngine()
        self.display     = Display(
            model=display_cfg.get("model", "epd2in13_V4"),
            rotate=int(display_cfg.get("rotate", 180)),
        )
        self.scanner     = NetworkScanner(iface=self._iface, on_host=self._on_host,
                                          target=self._scan_target)
        self.crack_queue = CrackQueue(crack_cfg, on_cracked=self._on_cracked)
        self.capture     = CaptureEngine(
            config=capture_cfg,
            on_network=self._on_network,
            on_client=self._on_client,
            on_capture=self._on_capture,
        )

        # Managed-mode AP scanner — populates Networks/Stats from the internal
        # radio (no monitor mode needed). Pauses while bettercap holds the
        # interface in monitor mode for capture.
        self._wifiscan_enabled = wifiscan_cfg.get("enabled", "true").lower() != "false"
        self.wifiscan = WifiScanner(
            iface=self._iface,
            on_network=self._on_network,
            interval=int(wifiscan_cfg.get("interval", 60)),
            should_pause=lambda: self.capture.scanning,
            vendor_lookup=self.scanner._vendor_for,
        )

        # L3 / VLAN topology mapper (traceroute + optional SNMP).
        self.topology = TopologyMapper(
            iface=self._iface, db_path=self._db_path,
            snmp_community=self._snmp_community,
            snmp_target=self._snmp_target,
            snmp_version=self._snmp_version,
        )

        self._state = {
            "db_path":      self._db_path,
            "conf_path":    self._conf_path,
            "captures_dir": capture_cfg.get("captures_dir", "/opt/radioman/captures"),
            "iface":        self._iface,
            "scan_target":  self._scan_target,
            "my_bssid":     self._my_bssid,
            "my_ssid":      self._my_ssid,
            "snmp_community": self._snmp_community,
            "snmp_target":  self._snmp_target,
            "snmp_version": self._snmp_version,
            "personality": self.personality,
            "battery":    bat.read,
            "scanner":    self.scanner,
            "capture":    self.capture,
            "crack_queue": self.crack_queue,
            "xplt_sync":  self.xplt_sync,
            "ai":         self.ai,
            "topology":   self.topology,
        }

        self._running = False

    # ── Event callbacks ───────────────────────────────────────────────────────

    def _on_network(self, bssid, ssid, channel, rssi, security, vendor):
        if db.is_ignored(self._db_path, bssid):
            log.debug("Ignored AP: %s (%s)", bssid, ssid)
            return
        device_type = fingerprint.device_type_for(bssid, vendor, ssid, is_ap=True)
        is_new = db.upsert_network(self._db_path, bssid, ssid, channel, rssi,
                                   security, vendor, device_type)
        log.debug("AP: %-17s  ch%-3d  %4ddBm  %-6s  %s  [%s]",
                  bssid, channel, rssi, security, ssid or "(hidden)", vendor or "?")
        # Only announce genuinely new APs — re-sightings every scan would spam.
        if is_new:
            db.log_event(self._db_path, "info", f"AP: {ssid or bssid} ch{channel} {rssi}dBm {security}")
            self.personality.on_new_network()

    def _on_client(self, mac, bssid, rssi, vendor):
        if bssid and db.is_ignored(self._db_path, bssid):
            return
        device_type = fingerprint.device_type_for(mac, vendor, "", is_ap=False)
        db.upsert_client(self._db_path, mac, bssid, rssi, vendor, device_type)
        log.debug("Client: %s → %s  %ddBm  [%s/%s]", mac, bssid or "probe", rssi,
                  vendor or "?", device_type)

    def _on_host(self, host):
        ip = host.get("ip", "")
        if not ip:
            return
        device_type = fingerprint.device_type_for(
            host.get("mac", ""), host.get("vendor", ""), host.get("hostname", ""))
        is_new = db.upsert_host(
            self._db_path, ip,
            host.get("mac", ""), host.get("vendor", ""),
            host.get("hostname", ""), host.get("method", "arp"), device_type,
        )
        if is_new:
            label = host.get("hostname") or host.get("vendor") or host.get("mac") or ip
            db.log_event(self._db_path, "info", f"LAN host: {ip} ({label})")

    def _on_capture(self, filepath, bssid, ssid, cap_type):
        if bssid and db.is_ignored(self._db_path, bssid):
            log.info("Skipping capture for ignored BSSID %s", bssid)
            return
        cap_id = db.insert_capture(self._db_path, filepath, bssid, ssid, cap_type)
        log.info("Capture #%d: %s [%s] bssid=%s  file=%s",
                 cap_id, ssid or "(hidden)", cap_type, bssid, filepath)
        db.log_event(self._db_path, "capture",
                     f"Captured {cap_type}: {ssid or bssid}")
        self.personality.on_capture(ssid)
        job = CrackJob(capture_id=cap_id, filepath=filepath,
                       bssid=bssid, ssid=ssid, cap_type=cap_type)
        self.crack_queue.enqueue(job)

    def _on_cracked(self, capture_id, password):
        db.mark_cracked(self._db_path, capture_id, password)
        masked = password[:2] + "*" * max(0, len(password) - 2)
        log.info("CRACKED capture #%d → %s (%d chars)", capture_id, masked, len(password))
        db.log_event(self._db_path, "cracked", f"Cracked capture #{capture_id}: {password}")
        self.personality.on_crack(password=password)
        self.xplt_sync.sync_now()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _display_loop(self):
        self.display.init()
        while self._running:
            try:
                batt  = bat.read()
                pers  = self.personality.snapshot()
                pers["scanning"] = self.capture.scanning
                stats = db.get_stats(self._db_path)
                self.display.update(pers, stats, batt)
            except Exception as e:
                log.error("Display loop error: %s", e)
            time.sleep(self._disp_period)

    _cleanup_counter = 0

    def _personality_loop(self):
        while self._running:
            try:
                batt    = bat.read()
                stats   = db.get_stats(self._db_path)
                self.personality.tick(
                    battery_pct=batt.get("percent", -1),
                    networks_seen=stats.get("networks", 0),
                )
                batt_pct = batt.get("percent", -1)
                if 0 <= batt_pct < 15:
                    self.personality.on_low_battery(batt_pct)
                log.debug("Tick: mood=%s  batt=%s%%(%s)  nets=%d  caps=%d  cracked=%d",
                          self.personality.snapshot().get("mood"),
                          batt_pct, "chg" if batt.get("charging") else "dis",
                          stats.get("networks", 0), stats.get("captures", 0),
                          stats.get("cracked", 0))
                # Clean up old RSSI history every ~10 minutes
                Radioman._cleanup_counter += 1
                if Radioman._cleanup_counter % max(1, 600 // self._pers_period) == 0:
                    db.clean_rssi_history(self._db_path, hours=self._rssi_hours)
                    log.debug("RSSI history cleaned (retention=%dh)", self._rssi_hours)
            except Exception as e:
                log.error("Personality loop error: %s", e)
            time.sleep(self._pers_period)

    def _web_loop(self):
        app = create_app(self._state)
        log.info("Web dashboard at http://0.0.0.0:%d", self._web_port)
        app.run(host="0.0.0.0", port=self._web_port,
                debug=False, use_reloader=False, threaded=True)

    def start(self):
        self._running = True
        log.info("radioman starting up...")

        self.scanner.start()
        self.crack_queue.start()
        self.capture.start()
        self.xplt_sync.start()
        if self._wifiscan_enabled:
            self.wifiscan.start()
        else:
            log.info("Internal WiFi scanner disabled (wifiscan.enabled=false)")

        threading.Thread(target=self._display_loop,    daemon=True, name="display").start()
        threading.Thread(target=self._personality_loop, daemon=True, name="personality").start()
        threading.Thread(target=self._web_loop,         daemon=True, name="web").start()

        log.info("All systems go. Press Ctrl+C to stop.")
        db.log_event(self._db_path, "info", "radioman started")

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        log.info("Shutting down...")
        self._running = False
        self.capture.stop()
        self.crack_queue.stop()
        self.scanner.stop()
        self.wifiscan.stop()
        self.xplt_sync.stop()
        self.display.sleep()
        db.log_event(self._db_path, "info", "radioman stopped")
        log.info("Goodbye.")


_instance: "Radioman | None" = None


def _handle_signal(signum, frame):
    log.info("Signal %d received", signum)
    if _instance:
        _instance.stop()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not os.path.exists(config_path):
        log.warning("Config not found at %s — using defaults", config_path)

    _instance = Radioman(config_path)
    _instance.start()
