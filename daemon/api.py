import logging
import os
import re
import threading
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

log = logging.getLogger("api")

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
        log.info("Capture download: %s", os.path.basename(real_path))
        return send_file(
            real_path,
            as_attachment=True,
            download_name=os.path.basename(real_path),
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
            cracked  = [c["password"] for c in captures if c.get("password")]
            log.info("AI analyze: passwords (%d cracked)", len(cracked))
            result = state["ai"].analyze_passwords(cracked)
        else:
            return jsonify({"error": "type must be 'networks' or 'passwords'"}), 400
        if result.get("busy"):
            return jsonify(result), 429
        return jsonify(result)

    return app
