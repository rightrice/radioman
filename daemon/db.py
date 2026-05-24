import sqlite3
import threading
from datetime import datetime

_local = threading.local()


def get_conn(path: str) -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.db_path != path:
        _local.conn = sqlite3.connect(path, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.db_path = path
    return _local.conn


def init(path: str):
    conn = get_conn(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS networks (
            bssid       TEXT PRIMARY KEY,
            ssid        TEXT,
            channel     INTEGER,
            rssi        INTEGER,
            security    TEXT,
            vendor      TEXT,
            clients     INTEGER DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT
        );

        CREATE TABLE IF NOT EXISTS clients (
            mac         TEXT PRIMARY KEY,
            bssid       TEXT,
            rssi        INTEGER,
            vendor      TEXT,
            first_seen  TEXT,
            last_seen   TEXT,
            FOREIGN KEY(bssid) REFERENCES networks(bssid)
        );

        CREATE TABLE IF NOT EXISTS captures (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT UNIQUE,
            bssid       TEXT,
            ssid        TEXT,
            type        TEXT,
            captured_at TEXT,
            cracked     INTEGER DEFAULT 0,
            password    TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT,
            level       TEXT,
            message     TEXT
        );

        CREATE TABLE IF NOT EXISTS ignored_bssids (
            bssid   TEXT PRIMARY KEY,
            note    TEXT DEFAULT "",
            added   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_clients_bssid ON clients(bssid);
        CREATE INDEX IF NOT EXISTS idx_captures_bssid ON captures(bssid);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
    """)
    conn.commit()


def upsert_network(path: str, bssid: str, ssid: str, channel: int,
                   rssi: int, security: str, vendor: str = ""):
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    conn.execute("""
        INSERT INTO networks (bssid, ssid, channel, rssi, security, vendor, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bssid) DO UPDATE SET
            ssid=excluded.ssid,
            channel=excluded.channel,
            rssi=excluded.rssi,
            security=excluded.security,
            last_seen=excluded.last_seen
    """, (bssid, ssid, channel, rssi, security, vendor, now, now))
    conn.commit()


def upsert_client(path: str, mac: str, bssid: str, rssi: int, vendor: str = ""):
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    conn.execute("""
        INSERT INTO clients (mac, bssid, rssi, vendor, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(mac) DO UPDATE SET
            bssid=excluded.bssid,
            rssi=excluded.rssi,
            last_seen=excluded.last_seen
    """, (mac, bssid, rssi, vendor, now, now))
    conn.execute("""
        UPDATE networks SET clients = (
            SELECT COUNT(*) FROM clients WHERE bssid = ?
        ) WHERE bssid = ?
    """, (bssid, bssid))
    conn.commit()


def insert_capture(path: str, filename: str, bssid: str, ssid: str, cap_type: str) -> int:
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    cur = conn.execute("""
        INSERT OR IGNORE INTO captures (filename, bssid, ssid, type, captured_at)
        VALUES (?, ?, ?, ?, ?)
    """, (filename, bssid, ssid, cap_type, now))
    conn.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute("SELECT id FROM captures WHERE filename=?", (filename,)).fetchone()
    return row["id"] if row else 0


def mark_cracked(path: str, capture_id: int, password: str):
    conn = get_conn(path)
    conn.execute("""
        UPDATE captures SET cracked=1, password=? WHERE id=?
    """, (password, capture_id))
    conn.commit()


def log_event(path: str, level: str, message: str):
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    conn.execute("INSERT INTO events (ts, level, message) VALUES (?, ?, ?)",
                 (now, level, message))
    conn.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")
    conn.commit()


def get_stats(path: str) -> dict:
    conn = get_conn(path)
    row = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM networks)  AS networks,
            (SELECT COUNT(*) FROM clients)   AS clients,
            (SELECT COUNT(*) FROM captures)  AS captures,
            (SELECT COUNT(*) FROM captures WHERE cracked=1) AS cracked
    """).fetchone()
    return dict(row) if row else {}


def get_networks(path: str) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM networks ORDER BY last_seen DESC LIMIT 200"
    ).fetchall()
    return [dict(r) for r in rows]


def get_clients(path: str) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM clients ORDER BY last_seen DESC LIMIT 500"
    ).fetchall()
    return [dict(r) for r in rows]


def get_captures(path: str) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM captures ORDER BY captured_at DESC LIMIT 200"
    ).fetchall()
    return [dict(r) for r in rows]


def get_events(path: str, limit: int = 50) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def add_ignored(path: str, bssid: str, note: str = "") -> None:
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    conn.execute(
        "INSERT OR IGNORE INTO ignored_bssids (bssid, note, added) VALUES (?, ?, ?)",
        (bssid.upper().strip(), note.strip(), now),
    )
    conn.commit()


def remove_ignored(path: str, bssid: str) -> bool:
    conn = get_conn(path)
    cur = conn.execute("DELETE FROM ignored_bssids WHERE bssid=?", (bssid.upper().strip(),))
    conn.commit()
    return cur.rowcount > 0


def get_ignored(path: str) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT bssid, note, added FROM ignored_bssids ORDER BY added DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def is_ignored(path: str, bssid: str) -> bool:
    conn = get_conn(path)
    row = conn.execute(
        "SELECT 1 FROM ignored_bssids WHERE bssid=?", (bssid.upper().strip(),)
    ).fetchone()
    return row is not None


def get_graph(path: str) -> dict:
    """Return AP→client relationships for the network graph view."""
    conn = get_conn(path)
    networks = conn.execute("SELECT bssid, ssid, security FROM networks").fetchall()
    clients = conn.execute("SELECT mac, bssid, vendor FROM clients").fetchall()
    nodes = [{"id": r["bssid"], "label": r["ssid"] or r["bssid"],
              "type": "ap", "security": r["security"]} for r in networks]
    nodes += [{"id": r["mac"], "label": r["vendor"] or r["mac"],
               "type": "client"} for r in clients]
    edges = [{"source": r["bssid"], "target": r["mac"]} for r in clients if r["bssid"]]
    return {"nodes": nodes, "edges": edges}
