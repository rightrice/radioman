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
import battery as bat
from personality import PersonalityEngine
from display import Display
from capture import CaptureEngine
from cracker import CrackQueue, CrackJob
from scanner import NetworkScanner
from api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("radioman")

DEFAULT_CONFIG = os.environ.get(
    "RADIOMAN_CONFIG",
    os.path.join(os.path.dirname(BASE_DIR), "config", "radioman.conf"),
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
        cfg = load_config(config_path)

        main_cfg    = flat(cfg, "radioman")
        capture_cfg = flat(cfg, "capture")
        crack_cfg   = flat(cfg, "cracker")
        db_cfg      = flat(cfg, "database")
        pisugar_cfg = flat(cfg, "pisugar")
        display_cfg = flat(cfg, "display")

        self._iface       = main_cfg.get("interface", "wlan0")
        self._web_port    = int(main_cfg.get("web_port", 8080))
        self._disp_period = int(main_cfg.get("display_refresh", 5))
        self._pers_period = int(main_cfg.get("personality_interval", 30))

        self._db_path = db_cfg.get("path", "/opt/radioman/radioman.db")
        db.init(self._db_path)

        capture_cfg["interface"]   = self._iface
        capture_cfg["caplet"]      = os.path.join(BASE_DIR, "radioman.cap")

        self.personality = PersonalityEngine()
        self.display     = Display(
            model=display_cfg.get("model", "epd2in13_V3"),
            rotate=int(display_cfg.get("rotate", 180)),
        )
        self.scanner     = NetworkScanner(iface=self._iface)
        self.crack_queue = CrackQueue(crack_cfg, on_cracked=self._on_cracked)
        self.capture     = CaptureEngine(
            config=capture_cfg,
            on_network=self._on_network,
            on_client=self._on_client,
            on_capture=self._on_capture,
        )

        self._state = {
            "db_path":    self._db_path,
            "personality": self.personality,
            "battery":    bat.read,
            "scanner":    self.scanner,
            "capture":    self.capture,
            "crack_queue": self.crack_queue,
        }

        self._running = False

    # ── Event callbacks ───────────────────────────────────────────────────────

    def _on_network(self, bssid, ssid, channel, rssi, security, vendor):
        db.upsert_network(self._db_path, bssid, ssid, channel, rssi, security, vendor)
        db.log_event(self._db_path, "info", f"AP: {ssid or bssid} ch{channel} {security}")
        self.personality.on_new_network()

    def _on_client(self, mac, bssid, rssi, vendor):
        db.upsert_client(self._db_path, mac, bssid, rssi, vendor)

    def _on_capture(self, filepath, bssid, ssid, cap_type):
        cap_id = db.insert_capture(self._db_path, filepath, bssid, ssid, cap_type)
        db.log_event(self._db_path, "capture",
                     f"Captured {cap_type}: {ssid or bssid}")
        self.personality.on_capture(ssid)
        job = CrackJob(capture_id=cap_id, filepath=filepath,
                       bssid=bssid, ssid=ssid)
        self.crack_queue.enqueue(job)

    def _on_cracked(self, capture_id, password):
        db.mark_cracked(self._db_path, capture_id, password)
        db.log_event(self._db_path, "cracked", f"Cracked capture #{capture_id}: {password}")
        self.personality.on_crack(password=password)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _display_loop(self):
        self.display.init()
        while self._running:
            try:
                batt  = bat.read()
                pers  = self.personality.snapshot()
                stats = db.get_stats(self._db_path)
                self.display.update(pers, stats, batt)
            except Exception as e:
                log.error("Display loop error: %s", e)
            time.sleep(self._disp_period)

    def _personality_loop(self):
        while self._running:
            try:
                batt    = bat.read()
                stats   = db.get_stats(self._db_path)
                self.personality.tick(
                    battery_pct=batt.get("percent", -1),
                    networks_seen=stats.get("networks", 0),
                )
                if batt.get("percent", 100) < 15:
                    self.personality.on_low_battery(batt["percent"])
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
        self.display.sleep()
        db.log_event(self._db_path, "info", "radioman stopped")
        log.info("Goodbye.")


def _handle_signal(signum, frame):
    log.info("Signal %d received", signum)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not os.path.exists(config_path):
        log.warning("Config not found at %s — using defaults", config_path)

    radioman = Radioman(config_path)
    radioman.start()
