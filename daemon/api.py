import logging
import os
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

log = logging.getLogger("api")


def _ttl_to_expires(ttl_hours) -> str:
    """Convert an hours value to an ISO-8601 UTC expiry, or '' for no expiry."""
    try:
        h = float(ttl_hours)
    except (TypeError, ValueError):
        return ""
    if h <= 0:
        return ""
    return (datetime.utcnow() + timedelta(hours=h)).isoformat()

_here = os.path.dirname(os.path.abspath(__file__))
_web_flat = os.path.join(_here, "web")        # deployed flat: /opt/radioman/web
_web_repo = os.path.join(_here, "..", "web")  # dev repo: daemon/../web
WEB_DIR = _web_flat if os.path.isdir(_web_flat) else _web_repo


def create_app(state: dict) -> Flask:
    """
    state keys (all mutable dicts/objects shared with the main daemon):
      db_path      str
      personality  PersonalityEngine
      battery      callable → dict
      scanner      NetworkScanner
      capture      CaptureEngine
      crack_queue  CrackQueue
    """
    import db as _db
    import netcfg

    app = Flask(__name__, static_folder=None)
    CORS(app)

    # Tracks an in-flight WiFi-join attempt (background thread).
    _wifi = {"connecting": False, "message": ""}

    def _db_path() -> str:
        return state["db_path"]

    def _save_conf(section: str, values: dict):
        """Persist settings to radioman.conf (survives restarts)."""
        import configparser as _cp
        conf_path = state.get("conf_path", "")
        if not conf_path or not os.path.exists(conf_path):
            log.warning("No conf_path — settings not persisted")
            return
        cfg = _cp.ConfigParser()
        cfg.read(conf_path)
        if section not in cfg:
            cfg.add_section(section)
        for k, v in values.items():
            cfg.set(section, k, str(v))
        with open(conf_path, "w") as fh:
            cfg.write(fh)

    # ── Static web dashboard ──────────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory(os.path.abspath(WEB_DIR), "index.html")

    @app.route("/assets/<path:path>")
    def assets(path):
        return send_from_directory(os.path.join(os.path.abspath(WEB_DIR), "assets"), path)

    @app.after_request
    def _log_request(resp):
        log.debug("%s %s → %d", request.method, request.path, resp.status_code)
        return resp

    def _capabilities() -> dict:
        """What the attached hardware can actually do, so the UI can explain/disable
        features instead of showing empty tables. Monitor mode + injection + AP
        mode all require a USB Wi-Fi adapter on this board (internal Synaptics
        43436s can't) — detect one by a USB-backed wlanN interface."""
        import glob
        usb_wifi = False
        for net in glob.glob("/sys/class/net/wlan*"):
            try:
                if "usb" in os.path.realpath(os.path.join(net, "device")).lower():
                    usb_wifi = True
                    break
            except Exception:
                pass
        gps = state.get("gps")
        return {
            "wifi_monitor": usb_wifi,   # handshake capture, client discovery, deauth
            "rogue_ap":     usb_wifi,   # evil-twin AP
            "gps":          bool(gps and getattr(gps, "enabled", False)),
        }

    # ── Status ────────────────────────────────────────────────────────────────
    @app.route("/api/status")
    def status():
        batt = state["battery"]()
        pers = state["personality"].snapshot()
        stats = _db.get_stats(_db_path())
        cq = state["crack_queue"]
        return jsonify({
            "personality": pers,
            "battery":     batt,
            "stats":       stats,
            "scanning":    state["capture"].scanning,
            "my_bssid":    state.get("my_bssid", ""),
            "capabilities": _capabilities(),
            "crack_queue": {
                "queued": cq.queue_size,
                "active": cq.active_jobs,
            },
        })

    # ── Networks ──────────────────────────────────────────────────────────────
    @app.route("/api/networks")
    def networks():
        return jsonify(_db.get_networks(_db_path()))

    @app.route("/api/networks/purge", methods=["POST"])
    def purge_networks():
        data = request.json or {}
        days = max(1, int(data.get("days", 7)))
        count = _db.purge_stale_networks(_db_path(), days)
        _db.log_event(_db_path(), "info", f"Purged {count} stale networks (>{days}d)")
        return jsonify({"purged": count})

    @app.route("/api/networks/<bssid>", methods=["DELETE"])
    def delete_network(bssid: str):
        removed = _db.delete_network(_db_path(), bssid.upper())
        if removed:
            _db.log_event(_db_path(), "info", f"Deleted network: {bssid.upper()}")
        return jsonify({"removed": removed, "bssid": bssid.upper()})

    # ── Bluetooth / BLE ────────────────────────────────────────────────────────
    @app.route("/api/bluetooth")
    def bluetooth():
        return jsonify(_db.get_bluetooth(_db_path()))

    @app.route("/api/bluetooth/purge", methods=["POST"])
    def purge_bluetooth():
        data = request.json or {}
        days = max(1, int(data.get("days", 7)))
        count = _db.purge_stale_bluetooth(_db_path(), days)
        _db.log_event(_db_path(), "info", f"Purged {count} stale BT devices (>{days}d)")
        return jsonify({"purged": count})

    @app.route("/api/bluetooth/<mac>", methods=["DELETE"])
    def delete_bluetooth(mac: str):
        removed = _db.delete_bluetooth(_db_path(), mac.upper())
        if removed:
            _db.log_event(_db_path(), "info", f"Deleted BT device: {mac.upper()}")
        return jsonify({"removed": removed, "mac": mac.upper()})

    # ── Active / offensive testing (authorized engagements only) ────────────────
    def _offensive_on() -> bool:
        return bool(state.get("offensive_enabled"))

    _MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    _SCOPE_KINDS = ("bssid", "client", "ssid", "ip")

    @app.route("/api/offensive/status")
    def offensive_status():
        return jsonify({
            "enabled":     _offensive_on(),
            "scope_count": len(_db.get_scope(_db_path())),
            "scanning":    state["capture"].scanning,
        })

    @app.route("/api/scope", methods=["GET"])
    def scope_list():
        return jsonify(_db.get_scope(_db_path()))

    @app.route("/api/scope", methods=["POST"])
    def scope_add():
        data    = request.json or {}
        kind    = (data.get("kind", "bssid") or "bssid").strip().lower()
        target  = (data.get("target", "") or "").strip()
        note    = (data.get("note", "") or "").strip()
        authref = (data.get("authref", "") or "").strip()
        if kind not in _SCOPE_KINDS:
            return jsonify({"error": f"kind must be one of {', '.join(_SCOPE_KINDS)}"}), 400
        if not target:
            return jsonify({"error": "target required"}), 400
        if kind in ("bssid", "client") and not _MAC_RE.match(target):
            return jsonify({"error": "MAC target must look like AA:BB:CC:DD:EE:FF"}), 400
        if not authref:
            return jsonify({"error": "authref (authorization reference) is required for scope entries"}), 400
        engagement = (data.get("engagement", "") or "").strip()[:80]
        expires    = _ttl_to_expires(data.get("ttl_hours"))
        _db.add_scope(_db_path(), target, kind, note, authref, engagement, expires)
        _db.log_event(_db_path(), "info",
                      f"Scope + {kind}:{target.upper() if kind in ('bssid','client') else target} (auth: {authref}"
                      + (f", engagement: {engagement}" if engagement else "")
                      + (", expires " + expires if expires else "") + ")")
        return jsonify({"added": True, "target": target, "kind": kind,
                        "engagement": engagement, "expires": expires})

    @app.route("/api/scope/bulk", methods=["POST"])
    def scope_bulk():
        """Paste a scope list from an RoE doc. One target per line; the kind is
        auto-detected (MAC→bssid, IP or CIDR→ip, anything else→ssid), or forced
        with a `kind:` / `kind ` prefix. A shared authref applies to the batch."""
        data       = request.json or {}
        text       = data.get("text", "") or ""
        authref    = (data.get("authref", "") or "").strip()
        note       = (data.get("note", "") or "").strip()
        engagement = (data.get("engagement", "") or "").strip()[:80]
        expires    = _ttl_to_expires(data.get("ttl_hours"))
        if not authref:
            return jsonify({"error": "authref (authorization reference) is required"}), 400
        added, skipped = 0, []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            kind = ""
            m = re.match(r"^(bssid|client|ssid|ip)[:\s]+(.+)$", line, re.I)
            if m:
                kind, line = m.group(1).lower(), m.group(2).strip()
            if not kind:
                if _MAC_RE.match(line):
                    kind = "bssid"
                elif re.match(r"^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$", line):
                    kind = "ip"
                else:
                    kind = "ssid"
            if kind in ("bssid", "client") and not _MAC_RE.match(line):
                skipped.append(line)
                continue
            _db.add_scope(_db_path(), line, kind, note, authref, engagement, expires)
            added += 1
        _db.log_event(_db_path(), "info", f"Scope bulk import: +{added} (auth: {authref}"
                      + (f", engagement: {engagement}" if engagement else "") + ")")
        return jsonify({"added": added, "skipped": skipped,
                        "engagement": engagement, "expires": expires})

    @app.route("/api/scope", methods=["DELETE"])
    def scope_remove():
        kind   = (request.args.get("kind", "") or "").strip().lower()
        target = (request.args.get("target", "") or "").strip()
        if kind not in _SCOPE_KINDS or not target:
            return jsonify({"error": "kind and target query params required"}), 400
        removed = _db.remove_scope(_db_path(), target, kind)
        if removed:
            _db.log_event(_db_path(), "info", f"Scope − {kind}:{target}")
        return jsonify({"removed": removed})

    # ── Engagement profiles (group + bulk-clear scope) ──────────────────────
    @app.route("/api/scope/engagements")
    def scope_engagements():
        return jsonify(_db.get_engagements(_db_path()))

    @app.route("/api/scope/engagement", methods=["DELETE"])
    def scope_engagement_clear():
        name = (request.args.get("engagement", "") or "").strip()
        if not name:
            return jsonify({"error": "engagement query param required"}), 400
        n = _db.clear_engagement(_db_path(), name)
        if n:
            _db.log_event(_db_path(), "info", f"Engagement ended: '{name}' — {n} scope entr{'y' if n == 1 else 'ies'} cleared")
        return jsonify({"cleared": n, "engagement": name})

    # ── "My lab" — saved owned networks for one-click scoping ────────────────
    @app.route("/api/lab", methods=["GET"])
    def lab_list():
        return jsonify(_db.get_lab_targets(_db_path()))

    @app.route("/api/lab", methods=["POST"])
    def lab_add():
        data   = request.json or {}
        kind   = (data.get("kind", "bssid") or "bssid").strip().lower()
        target = (data.get("target", "") or "").strip()
        note   = (data.get("note", "") or "").strip()
        if kind not in _SCOPE_KINDS:
            return jsonify({"error": f"kind must be one of {', '.join(_SCOPE_KINDS)}"}), 400
        if not target:
            return jsonify({"error": "target required"}), 400
        if kind in ("bssid", "client") and not _MAC_RE.match(target):
            return jsonify({"error": "MAC target must look like AA:BB:CC:DD:EE:FF"}), 400
        _db.add_lab_target(_db_path(), target, kind, note)
        return jsonify({"added": True, "target": target, "kind": kind})

    @app.route("/api/lab", methods=["DELETE"])
    def lab_remove():
        kind   = (request.args.get("kind", "") or "").strip().lower()
        target = (request.args.get("target", "") or "").strip()
        if kind not in _SCOPE_KINDS or not target:
            return jsonify({"error": "kind and target query params required"}), 400
        return jsonify({"removed": _db.remove_lab_target(_db_path(), target, kind)})

    @app.route("/api/lab/apply", methods=["POST"])
    def lab_apply():
        """Add all saved lab targets to scope in one click. These are the
        operator's own networks; the authorization reference records that."""
        lab = _db.get_lab_targets(_db_path())
        if not lab:
            return jsonify({"error": "No lab targets saved yet"}), 400
        data       = request.json or {}
        authref    = (data.get("authref", "") or "").strip() or "owned-lab"
        engagement = (data.get("engagement", "") or "").strip()[:80] or "lab"
        expires    = _ttl_to_expires(data.get("ttl_hours"))
        for t in lab:
            _db.add_scope(_db_path(), t["target"], t["kind"],
                          t.get("note", ""), authref, engagement, expires)
        _db.log_event(_db_path(), "info",
                      f"Lab applied to scope: +{len(lab)} (auth: {authref}, engagement: {engagement})")
        return jsonify({"applied": len(lab), "engagement": engagement, "expires": expires})

    @app.route("/api/attack/deauth", methods=["POST"])
    def attack_deauth():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled — set [offensive] enabled=true on the device"}), 403
        data = request.json or {}
        res = state["attack"].deauth(
            data.get("bssid", ""), data.get("client", ""), data.get("reason", ""))
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.route("/api/audit")
    def audit_feed():
        return jsonify(_db.get_audit(_db_path(), 100))

    # ── Rogue AP / evil-twin (authorized engagements only) ──────────────────────
    @app.route("/api/rogueap/status")
    def rogueap_status():
        st = state["rogueap"].status()
        st["clients"] = len(_db.get_rogue_clients(_db_path()))
        st["captures"] = len(_db.get_rogue_captures(_db_path()))
        return jsonify(st)

    @app.route("/api/rogueap/arm", methods=["POST"])
    def rogueap_arm():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled"}), 403
        data = request.json or {}
        res = state["rogueap"].arm(
            data.get("ssid", ""), data.get("authref", ""),
            bool(data.get("acknowledge")), bool(data.get("capture_creds")))
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.route("/api/rogueap/disarm", methods=["POST"])
    def rogueap_disarm():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled"}), 403
        return jsonify(state["rogueap"].disarm())

    @app.route("/api/rogueap/start", methods=["POST"])
    def rogueap_start():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled"}), 403
        data = request.json or {}
        res = state["rogueap"].start(int(data.get("channel", 6)))
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.route("/api/rogueap/stop", methods=["POST"])
    def rogueap_stop():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled"}), 403
        return jsonify(state["rogueap"].stop())

    @app.route("/api/rogueap/loot")
    def rogueap_loot():
        # Captured credentials are masked in transit; full values stay in the DB
        # for the engagement report (mirrors the crack-result masking).
        caps = _db.get_rogue_captures(_db_path(), 200)
        for c in caps:
            pw = c.get("password") or ""
            c["password"] = (pw[:2] + "*" * max(0, len(pw) - 2)) if pw else ""
        return jsonify({
            "clients":  _db.get_rogue_clients(_db_path(), 200),
            "captures": caps,
        })

    @app.route("/api/rogueap/loot", methods=["DELETE"])
    def rogueap_loot_clear():
        if not _offensive_on():
            return jsonify({"error": "offensive mode disabled"}), 403
        n = _db.clear_rogue(_db_path())
        _db.log_event(_db_path(), "info", f"Cleared rogue-AP loot ({n} rows)")
        return jsonify({"cleared": n})

    # ── GPS / wardrive ─────────────────────────────────────────────────────────
    @app.route("/api/wardrive")
    def wardrive():
        gps = state.get("gps")
        fix = gps.current_fix() if gps else {"fix": 0}
        return jsonify({
            "networks": _db.get_geo_networks(_db_path()),
            "track":    _db.get_wardrive_track(_db_path()),
            "fix":      fix,
            "enabled":  bool(gps and gps.enabled),
        })

    @app.route("/api/wardrive/track", methods=["DELETE"])
    def clear_track():
        count = _db.clear_wardrive_track(_db_path())
        _db.log_event(_db_path(), "info", f"Cleared wardrive track ({count} points)")
        return jsonify({"cleared": count})

    # ── Clients ───────────────────────────────────────────────────────────────
    @app.route("/api/clients")
    def clients():
        return jsonify(_db.get_clients(_db_path()))

    @app.route("/api/clients/<mac>", methods=["DELETE"])
    def delete_client(mac: str):
        removed = _db.delete_client(_db_path(), mac.upper())
        if removed:
            _db.log_event(_db_path(), "info", f"Deleted client: {mac.upper()}")
        return jsonify({"removed": removed, "mac": mac.upper()})

    # ── Captures ──────────────────────────────────────────────────────────────
    @app.route("/api/captures")
    def captures():
        return jsonify(_db.get_captures(_db_path()))

    # ── Password intelligence (offline analysis of cracked keys) ──────────────
    @app.route("/api/passwords")
    def passwords_analysis():
        import passwords as _pw
        cracked = [c for c in _db.get_captures(_db_path()) if c.get("password")]
        return jsonify(_pw.analyze(cracked))

    # ── Vault (at-rest capture encryption) ────────────────────────────────────
    @app.route("/api/vault")
    def vault_status():
        vault = state.get("vault")
        if not vault:
            return jsonify({"enabled": False})
        return jsonify(vault.status())

    @app.route("/api/vault/unlock", methods=["POST"])
    def vault_unlock():
        vault = state.get("vault")
        if not vault or not vault.enabled:
            return jsonify({"error": "vault is disabled"}), 400
        data = request.json or {}
        pin  = str(data.get("passphrase", data.get("pin", "")))
        res  = vault.unlock(pin)
        if not res.get("ok"):
            return jsonify(res), 400
        # Reconcile DB to disk, then re-enqueue any uncracked captures so they
        # crack now that the key is available.
        _db.sync_capture_encryption(_db_path(), state.get("captures_dir", "/opt/radioman/captures"))
        from cracker import CrackJob
        requeued = 0
        for c in _db.get_captures(_db_path()):
            if not c.get("cracked") and c.get("filename"):
                state["crack_queue"].enqueue(CrackJob(
                    capture_id=c["id"], filepath=c["filename"],
                    bssid=c.get("bssid") or "", ssid=c.get("ssid") or "",
                    cap_type=c.get("type", "EAPOL")))
                requeued += 1
        _db.log_event(_db_path(), "info",
                      f"Vault unlocked (fp {res.get('fingerprint')}) — {res.get('migrated',0)} migrated, {requeued} re-queued")
        return jsonify({**res, "requeued": requeued})

    @app.route("/api/vault/lock", methods=["POST"])
    def vault_lock():
        vault = state.get("vault")
        if not vault:
            return jsonify({"error": "vault is disabled"}), 400
        res = vault.lock()
        if res.get("ok"):
            _db.log_event(_db_path(), "info", "Vault locked — key cleared from memory")
        return jsonify(res)

    # ── Capture file download ─────────────────────────────────────────────────
    @app.route("/api/captures/<int:capture_id>/download")
    def capture_download(capture_id: int):
        rows = [r for r in _db.get_captures(_db_path()) if r["id"] == capture_id]
        if not rows:
            return jsonify({"error": "not found"}), 404
        filepath = rows[0].get("filename", "")
        if not filepath or not os.path.isfile(filepath):
            return jsonify({"error": "file not on disk"}), 404
        # Restrict to the captures directory — no path traversal
        captures_dir = os.path.realpath(
            state.get("captures_dir", "/opt/radioman/captures")
        )
        real_path = os.path.realpath(filepath)
        if not real_path.startswith(captures_dir + os.sep) and real_path != captures_dir:
            log.warning("Download blocked outside captures dir: %s", filepath)
            return jsonify({"error": "access denied"}), 403

        vault = state.get("vault")
        dl_name = os.path.basename(real_path)
        # Encrypted capture → decrypt in memory and serve the plaintext .pcapng.
        if vault and vault.is_encrypted(real_path):
            if vault.locked:
                return jsonify({"error": "vault is locked — unlock to download captures"}), 423
            try:
                with vault.plaintext(real_path) as ptxt:
                    with open(ptxt, "rb") as fh:
                        data = fh.read()
            except Exception as e:
                log.error("decrypt-for-download failed: %s", e)
                return jsonify({"error": "decryption failed"}), 500
            from flask import Response
            dl_name = dl_name[:-len(".enc")] if dl_name.endswith(".enc") else dl_name
            log.info("Capture download (decrypted): %s", dl_name)
            return Response(
                data,
                headers={
                    "Content-Disposition": f'attachment; filename="{dl_name}"',
                    "Content-Type": "application/vnd.tcpdump.pcap",
                },
            )
        log.info("Capture download: %s", dl_name)
        return send_file(
            real_path,
            as_attachment=True,
            download_name=dl_name,
            mimetype="application/vnd.tcpdump.pcap",
        )

    # ── Crack on demand ───────────────────────────────────────────────────────
    @app.route("/api/crack/<int:capture_id>", methods=["POST"])
    def crack(capture_id: int):
        from cracker import CrackJob
        rows = [r for r in _db.get_captures(_db_path()) if r["id"] == capture_id]
        if not rows:
            return jsonify({"error": "not found"}), 404
        row = rows[0]
        job = CrackJob(
            capture_id=row["id"],
            filepath=row["filename"],
            bssid=row["bssid"] or "",
            ssid=row["ssid"] or "",
            cap_type=row.get("type", "EAPOL"),
        )
        state["crack_queue"].enqueue(job)
        return jsonify({"queued": True, "capture_id": capture_id})

    # ── Network graph ─────────────────────────────────────────────────────────
    @app.route("/api/graph")
    def graph():
        return jsonify(_db.get_graph(_db_path()))

    # ── LAN hosts (persisted; scanner enriches + stores via on_host) ──────────
    @app.route("/api/hosts")
    def hosts():
        return jsonify(_db.get_hosts(_db_path()))

    @app.route("/api/hosts/scan", methods=["POST"])
    def hosts_scan():
        target = None
        if request.is_json:
            raw = request.json.get("target", "")
            if raw:
                # Accept only IP, CIDR, or hostname-looking strings — no nmap flags
                if re.fullmatch(r"[\w.\-/]+", str(raw)):
                    target = str(raw)
                else:
                    return jsonify({"error": "invalid target format"}), 400
        started = state["scanner"].start_nmap_scan(target)   # background; persists via on_host
        return jsonify({"started": started, **state["scanner"].scan_status()})

    @app.route("/api/hosts/scanstatus")
    def hosts_scanstatus():
        return jsonify(state["scanner"].scan_status())

    # ── L3 / VLAN topology (traceroute + SNMP) ────────────────────────────────
    @app.route("/api/topology")
    def topology():
        return jsonify(state["topology"].get())

    @app.route("/api/topology/scan", methods=["POST"])
    def topology_scan():
        started = state["topology"].start_scan()
        return jsonify({"started": started, **state["topology"].scan_status()})

    # ── Events / log ─────────────────────────────────────────────────────────
    @app.route("/api/events")
    def events():
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
        except (ValueError, TypeError):
            limit = 50
        return jsonify(_db.get_events(_db_path(), limit))

    # ── Ignore list ───────────────────────────────────────────────────────────
    @app.route("/api/ignore", methods=["GET"])
    def get_ignore():
        return jsonify(_db.get_ignored(_db_path()))

    @app.route("/api/ignore", methods=["POST"])
    def add_ignore():
        data = request.json or {}
        bssid = data.get("bssid", "").strip().upper()
        if not bssid:
            return jsonify({"error": "bssid required"}), 400
        note = data.get("note", "").strip()
        _db.add_ignored(_db_path(), bssid, note)
        _db.log_event(_db_path(), "info", f"Ignored BSSID: {bssid}")
        return jsonify({"ignored": True, "bssid": bssid})

    @app.route("/api/ignore/<bssid>", methods=["DELETE"])
    def remove_ignore(bssid: str):
        bssid = bssid.upper()
        removed = _db.remove_ignored(_db_path(), bssid)
        if removed:
            _db.log_event(_db_path(), "info", f"Unignored BSSID: {bssid}")
        return jsonify({"removed": removed, "bssid": bssid})

    # ── XPLT / Supabase sync ─────────────────────────────────────────────────
    @app.route("/api/xplt/status")
    def xplt_status():
        return jsonify(state["xplt_sync"].snapshot())

    @app.route("/api/xplt/sync", methods=["POST"])
    def xplt_sync_now():
        state["xplt_sync"].sync_now()
        return jsonify({"triggered": True})

    @app.route("/api/xplt/pair", methods=["POST"])
    def xplt_pair():
        import configparser as _cp
        data = request.json or {}
        code = str(data.get("code", "")).strip().upper().replace(" ", "").replace("-", "")
        device_name = str(data.get("device_name", "")).strip()[:80] or "radioman"

        if len(code) != 8 or not code.isalnum():
            return jsonify({"error": "Code must be exactly 8 alphanumeric characters"}), 400

        try:
            token = state["xplt_sync"].pair(code, device_name)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        # Persist token to radioman.conf so it survives restarts
        conf_path = state.get("conf_path", "")
        if conf_path and os.path.exists(conf_path):
            try:
                cfg = _cp.ConfigParser()
                cfg.read(conf_path)
                if "xplt" not in cfg:
                    cfg.add_section("xplt")
                cfg.set("xplt", "device_token", token)
                with open(conf_path, "w") as fh:
                    cfg.write(fh)
            except Exception as e:
                log.error("Failed to write token to conf: %s", e)

        _db.log_event(_db_path(), "info", f"Device paired with XPLT as '{device_name}'")
        return jsonify({"paired": True})

    # ── Scan control ──────────────────────────────────────────────────────────
    @app.route("/api/scan/start", methods=["POST"])
    def scan_start():
        state["capture"].start_scan()
        _db.log_event(_db_path(), "info", "Scan started")
        return jsonify({"scanning": True})

    @app.route("/api/scan/stop", methods=["POST"])
    def scan_stop():
        state["capture"].stop_scan()
        _db.log_event(_db_path(), "info", "Scan stopped")
        return jsonify({"scanning": False})

    # ── Stats (channel / security / vendor / RSSI history) ───────────────────
    @app.route("/api/stats")
    def stats_overview():
        return jsonify({
            "channels": _db.get_channel_stats(_db_path()),
            "security": _db.get_security_stats(_db_path()),
            "vendors":  _db.get_vendor_stats(_db_path()),
        })

    @app.route("/api/stats/rssi_history/<bssid>")
    def stats_rssi_history(bssid: str):
        try:
            minutes = min(int(request.args.get("minutes", 60)), 1440)
        except (ValueError, TypeError):
            minutes = 60
        return jsonify(_db.get_rssi_history(_db_path(), bssid, minutes))

    # ── bettercap pass-through commands ───────────────────────────────────────
    @app.route("/api/cmd", methods=["POST"])
    def cmd():
        if not request.is_json:
            return jsonify({"error": "expected JSON"}), 400
        command = request.json.get("cmd", "").strip()
        if not command:
            return jsonify({"error": "empty command"}), 400
        ok = state["capture"].send_cmd(command)
        return jsonify({"sent": ok, "cmd": command})

    # ── AI (local llama.cpp inference) ───────────────────────────────────────
    @app.route("/api/ai/status")
    def ai_status():
        return jsonify(state["ai"].status())

    @app.route("/api/ai/chat", methods=["POST"])
    def ai_chat():
        if not request.is_json:
            return jsonify({"error": "expected JSON"}), 400
        data     = request.json or {}
        messages = data.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages array required"}), 400
        # Sanitize: only allow role/content keys, truncate content
        clean = []
        for m in messages[-12:]:
            role    = str(m.get("role", "user"))[:16]
            content = str(m.get("content", ""))[:2000]
            if role in ("user", "assistant") and content:
                clean.append({"role": role, "content": content})
        if not clean:
            return jsonify({"error": "no valid messages"}), 400
        log.info("AI chat: %d messages", len(clean))
        result = state["ai"].chat(clean)
        if result.get("busy"):
            return jsonify(result), 429
        return jsonify(result)

    # ── Settings (scan target + "my network") ────────────────────────────────
    @app.route("/api/settings")
    def get_settings():
        return jsonify({
            "scan_target":    state.get("scan_target", ""),
            "my_bssid":       state.get("my_bssid", ""),
            "my_ssid":        state.get("my_ssid", ""),
            "iface":          state.get("iface", "wlan0"),
            "snmp_community": state.get("snmp_community", ""),
            "snmp_target":    state.get("snmp_target", ""),
            "snmp_version":   state.get("snmp_version", "2c"),
        })

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        data        = request.json or {}
        scan_target = str(data.get("scan_target", "")).strip()
        my_bssid    = str(data.get("my_bssid", "")).strip().upper()
        my_ssid     = str(data.get("my_ssid", "")).strip()[:64]
        snmp_comm   = str(data.get("snmp_community", "")).strip()[:64]
        snmp_target = str(data.get("snmp_target", "")).strip()
        snmp_ver    = str(data.get("snmp_version", "2c")).strip() or "2c"

        if scan_target and not re.fullmatch(r"[\w.\-/]+", scan_target):
            return jsonify({"error": "Invalid scan target (use IP or CIDR, e.g. 192.168.1.0/24)"}), 400
        if my_bssid and not re.fullmatch(r"[0-9A-F:]{17}", my_bssid):
            return jsonify({"error": "Invalid BSSID (use AA:BB:CC:DD:EE:FF)"}), 400
        if snmp_target and not re.fullmatch(r"[\w.\-]+", snmp_target):
            return jsonify({"error": "Invalid SNMP target (use an IP or hostname)"}), 400
        if snmp_ver not in ("1", "2c"):
            snmp_ver = "2c"

        state["scan_target"]    = scan_target
        state["my_bssid"]       = my_bssid
        state["my_ssid"]        = my_ssid
        state["snmp_community"] = snmp_comm
        state["snmp_target"]    = snmp_target
        state["snmp_version"]   = snmp_ver
        state["scanner"].set_target(scan_target)
        state["topology"].set_snmp(snmp_comm, snmp_target, snmp_ver)
        _save_conf("scan", {"target": scan_target, "my_bssid": my_bssid, "my_ssid": my_ssid})
        _save_conf("snmp", {"community": snmp_comm, "target": snmp_target, "version": snmp_ver})
        return jsonify({"saved": True, "scan_target": scan_target,
                        "my_bssid": my_bssid, "my_ssid": my_ssid})

    # ── WiFi connection ───────────────────────────────────────────────────────
    @app.route("/api/wifi/status")
    def wifi_status():
        st = netcfg.wifi_status(state.get("iface", "wlan0"))
        st.update(_wifi)
        return jsonify(st)

    @app.route("/api/wifi/connect", methods=["POST"])
    def wifi_connect():
        data     = request.json or {}
        ssid     = str(data.get("ssid", "")).strip()
        password = str(data.get("password", ""))
        if not ssid:
            return jsonify({"error": "SSID required"}), 400
        if _wifi["connecting"]:
            return jsonify({"error": "A connection attempt is already in progress"}), 409

        def worker():
            _wifi["connecting"] = True
            _wifi["message"]    = f"Connecting to {ssid}…"
            ok, msg = netcfg.wifi_connect(state.get("iface", "wlan0"), ssid, password)
            _wifi["message"]    = msg
            _wifi["connecting"] = False
            _db.log_event(_db_path(), "info", f"WiFi join '{ssid}': {msg}")

        threading.Thread(target=worker, daemon=True, name="wifi-connect").start()
        return jsonify({"started": True})

    @app.route("/api/ai/analyze", methods=["POST"])
    def ai_analyze():
        if not request.is_json:
            return jsonify({"error": "expected JSON"}), 400
        kind = str(request.json.get("type", "")).strip()
        if kind == "networks":
            networks = _db.get_networks(_db_path())
            captures = _db.get_captures(_db_path())
            log.info("AI analyze: networks (%d) captures (%d)", len(networks), len(captures))
            result = state["ai"].analyze_networks(networks, captures)
        elif kind == "passwords":
            captures = _db.get_captures(_db_path())
            cracked  = [c for c in captures if c.get("password")]
            log.info("AI analyze: passwords (%d cracked)", len(cracked))
            result = state["ai"].analyze_passwords(cracked)
        else:
            return jsonify({"error": "type must be 'networks' or 'passwords'"}), 400
        if result.get("busy"):
            return jsonify(result), 429
        return jsonify(result)

    return app
