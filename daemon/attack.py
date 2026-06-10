"""
attack.py — active (offensive) actions, every one gated by authz.

Currently implements targeted deauthentication: bounce the clients of a single
authorized AP (or one authorized client) so they reconnect and we can capture
the PMKID/EAPOL handshake. This is standard authorized-pentest methodology for
handshake acquisition — NOT a broadcast/continuous denial-of-service tool:

  * Single named target only. Broadcast (ff:ff:ff:ff:ff:ff) is explicitly refused.
  * Target must pass authz.is_authorized() — i.e. on the RoE allowlist AND
    offensive mode enabled. Denials are audited.
  * Rate-limited per target so it can't be turned into a sustained flood.
  * Requires bettercap/monitor mode to be active (reuses CaptureEngine.send_cmd).

The actual frame TX is delegated to bettercap's `wifi.deauth <mac>`; this module
is the policy + safety wrapper around it.
"""

import logging
import time

log = logging.getLogger("attack")

BROADCAST = {"FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00", "*"}


class AttackEngine:
    def __init__(self, capture, authz, db_path: str,
                 enabled: bool = False, min_interval: int = 5):
        self._capture = capture
        self._authz = authz
        self._db_path = db_path
        self.enabled = enabled
        self._min_interval = max(2, int(min_interval))
        self._last = {}   # target -> last-fired epoch

    def deauth(self, bssid: str, client: str = "", reason: str = "") -> dict:
        """Deauth a single authorized target. Returns a result dict; never raises."""
        bssid = (bssid or "").strip().upper()
        client = (client or "").strip().upper()

        if not self.enabled:
            return {"ok": False, "error": "offensive mode disabled — set [offensive] enabled=true on the device"}

        # Refuse broadcast / indiscriminate targets outright (no mass deauth).
        if not bssid or bssid in BROADCAST or client in BROADCAST:
            self._authz.audit("deauth", bssid or "?", False, "broadcast/empty target refused")
            return {"ok": False, "error": "a specific in-scope BSSID is required (broadcast is not allowed)"}

        # The AP must be authorized.
        ok, why = self._authz.is_authorized(bssid, "bssid")
        if not ok:
            self._authz.audit("deauth", bssid, False, why)
            return {"ok": False, "error": why}

        # If a specific client is named, it must be separately authorized too.
        if client:
            cok, cwhy = self._authz.is_authorized(client, "client")
            if not cok:
                self._authz.audit("deauth", client, False, cwhy)
                return {"ok": False, "error": cwhy}

        # Monitor mode / bettercap must be live to transmit.
        if not getattr(self._capture, "scanning", False):
            return {"ok": False, "error": "start a scan first — bettercap/monitor mode must be active to transmit"}

        target = client or bssid
        now = time.time()
        wait = self._min_interval - (now - self._last.get(target, 0.0))
        if wait > 0:
            return {"ok": False, "error": f"rate limited — wait {wait:.0f}s before deauthing {target} again"}

        sent = self._capture.send_cmd(f"wifi.deauth {target}")
        self._last[target] = now
        self._authz.audit("deauth", target, True, reason or "handshake capture")
        if not sent:
            return {"ok": False, "error": "command not delivered to bettercap (is it running?)", "target": target}
        return {"ok": True, "target": target, "action": "deauth"}
