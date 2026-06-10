"""
authz.py — the authorization chokepoint for ACTIVE (offensive) actions.

Every active capability (deauth, and later rogue AP, etc.) must call
`is_authorized()` before doing anything, and `audit()` after. This is the single
place that decides whether an action is permitted, so the policy can't drift
between features.

Policy (fail-closed / deny-by-default):
  1. Offensive mode must be enabled at deploy time ([offensive] enabled=true).
     Off by default — a freshly flashed device cannot launch active actions.
  2. The exact target must be on the Rules-of-Engagement allowlist (the `scope`
     table). Nothing is in scope unless an operator explicitly added it.
  3. Every decision — allow OR deny — is written to the audit trail.

This intentionally does NOT decide *how* an action runs (rate limits, broadcast
refusal, monitor-mode checks live in attack.py); it only answers "is this target
authorized right now, and record that we asked."
"""

import ipaddress
import logging

import db

log = logging.getLogger("authz")


class AuthzEngine:
    def __init__(self, db_path: str, enabled: bool = False):
        self.db_path = db_path
        self.enabled = enabled
        log.info("Authorization engine ready (offensive mode %s)",
                 "ENABLED" if enabled else "disabled")

    def is_authorized(self, target: str, kind: str) -> tuple:
        """Return (allowed: bool, reason: str). Never raises.

        Beyond an exact allowlist hit, scope can match ergonomically so a whole
        engagement is authorized at once:
          * a BSSID is authorized if its SSID (from the networks table) is in
            scope as an `ssid` entry — authorize a client network by name;
          * an IP is authorized if it falls inside any `ip` scope CIDR.
        """
        if not self.enabled:
            return False, "offensive mode disabled ([offensive] enabled=false)"
        target = (target or "").strip()
        if not target:
            return False, "no target specified"

        if db.is_in_scope(self.db_path, target, kind):
            return True, "authorized (exact)"

        if kind == "bssid":
            ssid = db.ssid_for_bssid(self.db_path, target)
            if ssid and db.is_in_scope(self.db_path, ssid, "ssid"):
                return True, f"authorized (SSID '{ssid}' in scope)"

        if kind == "ip" and self._ip_in_scope(target):
            return True, "authorized (within scoped CIDR)"

        return False, f"{target} is not in the authorized scope ({kind})"

    def is_ssid_authorized(self, ssid: str) -> tuple:
        """Whether an SSID may be used for a rogue AP (must be in scope by name)."""
        if not self.enabled:
            return False, "offensive mode disabled ([offensive] enabled=false)"
        ssid = (ssid or "").strip()
        if not ssid:
            return False, "no SSID specified"
        if db.is_in_scope(self.db_path, ssid, "ssid"):
            return True, "authorized"
        return False, f"SSID '{ssid}' is not in the authorized scope — add it as an 'ssid' scope entry first"

    def _ip_in_scope(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for entry in db.get_scope_targets(self.db_path, "ip"):
            try:
                if "/" in entry:
                    if addr in ipaddress.ip_network(entry, strict=False):
                        return True
                elif ipaddress.ip_address(entry) == addr:
                    return True
            except ValueError:
                continue
        return False

    def audit(self, action: str, target: str, allowed: bool, detail: str = "") -> None:
        """Record an active-action decision to the audit trail (events table).
        level 'active' = performed, 'denied' = blocked."""
        level = "active" if allowed else "denied"
        verb = "ALLOW" if allowed else "DENY"
        msg = f"{verb} {action} → {target or '?'}"
        if detail:
            msg += f"  ({detail})"
        try:
            db.log_event(self.db_path, level, msg)
        except Exception as e:
            log.error("audit write failed: %s", e)
        log.info("%s", msg)
