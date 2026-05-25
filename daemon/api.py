import logging
import os
import re
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

    app = Flask(__name__, static_folder=None)
    CORS(app)

    def _db_path() -> str:
        return state["db_path"]

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
            "crack_queue": {
                "queued": cq.queue_size,
                "active": cq.active_jobs,
            },
        })

    # ── Networks ──────────────────────────────────────────────────────────────
    @app.route("/api/networks")
    def networks():
        return jsonify(_db.get_networks(_db_path()))

    # ── Clients ───────────────────────────────────────────────────────────────
    @app.route("/api/clients")
    def clients():
        return jsonify(_db.get_clients(_db_path()))

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
        )
        state["crack_queue"].enqueue(job)
        return jsonify({"queued": True, "capture_id": capture_id})

    # ── Network graph ─────────────────────────────────────────────────────────
    @app.route("/api/graph")
    def graph():
        return jsonify(_db.get_graph(_db_path()))

    # ── LAN hosts (from scanner) ──────────────────────────────────────────────
    @app.route("/api/hosts")
    def hosts():
        return jsonify(state["scanner"].get_hosts())

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
        results = state["scanner"].nmap_scan(target)
        return jsonify(results)

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
