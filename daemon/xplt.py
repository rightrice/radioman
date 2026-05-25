"""
xplt.py — XPLT cloud sync for radioman.

Pushes Wi-Fi scan data (networks, clients, captures) to the XPLT platform
via the radioman-ingest Edge Function. The device authenticates with a
pre-provisioned device token — no Supabase keys ever touch the hardware.

The Pi Zero 2W has no internet while wlan0 is in monitor mode. The sync
loop runs every 60 s and silently no-ops until a connection is available.
"""

import logging
import os
import threading
import time
from typing import Optional

import requests

log = logging.getLogger("xplt")

# XPLT ingest endpoint — our server, not a user secret
INGEST_URL    = "https://tvbcncczdorqpoppbpxi.supabase.co/functions/v1/radioman-ingest"
PAIR_URL      = "https://tvbcncczdorqpoppbpxi.supabase.co/functions/v1/radioman-pair"
SYNC_INTERVAL = 60    # seconds between sync attempts
BATCH_SIZE    = 200   # max records per table per sync pass
TIMEOUT       = 20    # seconds for HTTP requests


class XpltSync:
    def __init__(self, config: dict, db_path: str, conf_path: str = ""):
        # Device token can come from env var or config file.
        # The token is provisioned once when the device is registered in XPLT.
        self._token     = (os.environ.get("XPLT_DEVICE_TOKEN") or
                           config.get("device_token", "")).strip()
        self._db_path   = db_path
        self._conf_path = conf_path
        self._running   = False
        self._enabled   = bool(self._token)

        self._last_sync_ts: Optional[float] = None
        self._last_error:   Optional[str]   = None
        self._pending:      int             = 0
        self._total_pushed: int             = 0
        self._lock = threading.Lock()

        if not self._enabled:
            log.info("XPLT sync disabled — add device_token to [xplt] in radioman.conf")

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if not self._enabled:
            return
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="xplt-sync")
        t.start()
        log.info("XPLT sync started")

    def stop(self):
        self._running = False

    def sync_now(self):
        """Trigger an immediate out-of-band sync (e.g. after a crack succeeds)."""
        if self._enabled:
            threading.Thread(target=self._sync, daemon=True, name="xplt-manual").start()

    def pair(self, code: str, device_name: str) -> str:
        """
        Exchange a short-lived pairing code for a device token.
        Calls the radioman-pair edge function and activates sync on success.
        Raises Exception with a human-readable message on any failure.
        """
        try:
            resp = requests.post(
                PAIR_URL,
                json={"code": code, "device_name": device_name},
                timeout=TIMEOUT,
            )
        except requests.exceptions.ConnectionError:
            raise Exception("No internet connection — check your network and try again")
        except requests.exceptions.Timeout:
            raise Exception("Request timed out — check your connection")

        if resp.status_code == 429:
            raise Exception("Too many attempts — try again in 5 minutes")
        if resp.status_code in (400, 401):
            msg = resp.json().get("error", "Invalid or expired pairing code")
            raise Exception(msg)
        if resp.status_code != 200:
            raise Exception(f"Server error (HTTP {resp.status_code})")

        data = resp.json()
        token = data.get("device_token", "")
        if not token:
            raise Exception("No token returned — contact support")

        self.set_token(token)
        return token

    def set_token(self, token: str):
        """Activate sync with a new token (hot-swap, no daemon restart required)."""
        self._token   = token.strip()
        self._enabled = True
        if not self._running:
            self.start()
        log.info("XPLT: device token set, sync activated")

    def unpair(self):
        """
        Disable sync and clear the stored token.
        Removes device_token from radioman.conf so the dashboard shows 'not paired'
        on the next poll. Called automatically when the server returns 401.
        """
        with self._lock:
            self._token   = ""
            self._enabled = False
            self._running = False
            self._last_error = "token revoked"
        log.info("XPLT: token cleared — device unpaired")
        self._remove_token_from_conf()

    def _remove_token_from_conf(self):
        """Strip device_token line from radioman.conf without touching other keys."""
        if not self._conf_path or not os.path.isfile(self._conf_path):
            return
        try:
            with open(self._conf_path, "r") as f:
                lines = f.readlines()
            new_lines = [l for l in lines if not l.strip().startswith("device_token")]
            with open(self._conf_path, "w") as f:
                f.writelines(new_lines)
            log.info("XPLT: device_token removed from %s", self._conf_path)
        except Exception as e:
            log.warning("XPLT: could not update conf: %s", e)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled":       self._enabled,
                "last_sync":     self._last_sync_ts,
                "last_error":    self._last_error,
                "pending":       self._pending,
                "total_pushed":  self._total_pushed,
            }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            self._sync()
            time.sleep(SYNC_INTERVAL)

    def _sync(self):
        import db
        try:
            networks = db.get_unsynced_networks(self._db_path, BATCH_SIZE)
            clients  = db.get_unsynced_clients(self._db_path, BATCH_SIZE)
            captures = db.get_unsynced_captures(self._db_path, BATCH_SIZE)

            total = len(networks) + len(clients) + len(captures)
            with self._lock:
                self._pending = total

            if total == 0:
                return

            payload = {
                "networks": [self._network_row(r) for r in networks],
                "clients":  [self._client_row(r)  for r in clients],
                "captures": [self._capture_row(r) for r in captures],
            }

            try:
                resp = requests.post(
                    INGEST_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type":  "application/json",
                    },
                    timeout=TIMEOUT,
                )
            except requests.exceptions.ConnectionError:
                log.debug("XPLT: offline — %d record(s) queued", total)
                return
            except requests.exceptions.Timeout:
                log.warning("XPLT: ingest request timed out")
                return

            if resp.status_code == 401:
                log.error("XPLT: device token revoked or invalid — clearing pairing")
                self.unpair()
                return

            if resp.status_code == 429:
                log.warning("XPLT: rate limited")
                return

            if resp.status_code != 200:
                log.warning("XPLT: ingest HTTP %d — %s", resp.status_code, resp.text[:200])
                with self._lock:
                    self._last_error = f"HTTP {resp.status_code}"
                return

            # Success — mark records as synced
            result = resp.json().get("ingested", {})
            if networks:
                db.mark_synced_networks(self._db_path, [r["bssid"] for r in networks])
            if clients:
                db.mark_synced_clients(self._db_path, [r["mac"] for r in clients])
            if captures:
                db.mark_synced_captures(self._db_path, [r["id"] for r in captures])

            pushed = (result.get("networks", 0) +
                      result.get("clients",  0) +
                      result.get("captures", 0))

            with self._lock:
                self._total_pushed += pushed
                self._pending       = 0
                self._last_sync_ts  = time.time()
                self._last_error    = None

            log.info("XPLT: pushed %d record(s) (n=%d c=%d cap=%d)",
                     pushed,
                     result.get("networks", 0),
                     result.get("clients",  0),
                     result.get("captures", 0))

        except Exception as e:
            log.error("XPLT sync error: %s", e)
            with self._lock:
                self._last_error = str(e)

    # ── Row builders ──────────────────────────────────────────────────────────

    def _network_row(self, r: dict) -> dict:
        return {
            "bssid":      r.get("bssid"),
            "ssid":       r.get("ssid"),
            "channel":    r.get("channel"),
            "rssi":       r.get("rssi"),
            "security":   r.get("security"),
            "vendor":     r.get("vendor"),
            "first_seen": r.get("first_seen"),
            "last_seen":  r.get("last_seen"),
        }

    def _client_row(self, r: dict) -> dict:
        return {
            "mac":        r.get("mac"),
            "bssid":      r.get("bssid"),
            "rssi":       r.get("rssi"),
            "vendor":     r.get("vendor"),
            "first_seen": r.get("first_seen"),
            "last_seen":  r.get("last_seen"),
        }

    def _capture_row(self, r: dict) -> dict:
        return {
            "local_id":   r.get("id"),
            "bssid":      r.get("bssid"),
            "ssid":       r.get("ssid"),
            "type":       r.get("type"),
            "captured_at": r.get("captured_at"),
            "cracked":    bool(r.get("cracked")),
            "password":   r.get("password"),
        }
