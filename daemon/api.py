import logging
import os
from flask import Flask, jsonify, request, send_from_directory
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
        target = request.json.get("target") if request.is_json else None
        results = state["scanner"].nmap_scan(target)
        return jsonify(results)

    # ── Events / log ─────────────────────────────────────────────────────────
    @app.route("/api/events")
    def events():
        limit = min(int(request.args.get("limit", 50)), 200)
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

    return app
