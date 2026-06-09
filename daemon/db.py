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

        CREATE TABLE IF NOT EXISTS rssi_history (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            bssid TEXT NOT NULL,
            rssi  INTEGER,
            ts    TEXT
        );

        CREATE TABLE IF NOT EXISTS hosts (
            ip          TEXT PRIMARY KEY,
            mac         TEXT,
            vendor      TEXT,
            hostname    TEXT,
            method      TEXT,
            first_seen  TEXT,
            last_seen   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_clients_bssid ON clients(bssid);
        CREATE INDEX IF NOT EXISTS idx_hosts_last ON hosts(last_seen);
        CREATE INDEX IF NOT EXISTS idx_captures_bssid ON captures(bssid);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_rssi_bssid_ts ON rssi_history(bssid, ts);
    """)
    # Add columns to existing tables (safe on re-run — fails silently if present)
    for col_sql in [
        "ALTER TABLE networks ADD COLUMN xplt_synced INTEGER DEFAULT 0",
        "ALTER TABLE clients  ADD COLUMN xplt_synced INTEGER DEFAULT 0",
        "ALTER TABLE captures ADD COLUMN xplt_synced INTEGER DEFAULT 0",
        "ALTER TABLE networks ADD COLUMN device_type TEXT DEFAULT ''",
        "ALTER TABLE clients  ADD COLUMN device_type TEXT DEFAULT ''",
        "ALTER TABLE hosts    ADD COLUMN device_type TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass  # column already exists
    conn.commit()


def upsert_network(path: str, bssid: str, ssid: str, channel: int,
                   rssi: int, security: str, vendor: str = "",
                   device_type: str = "") -> bool:
    """Insert or update an AP. Returns True if this BSSID was newly seen.
    Non-empty vendor/device_type never overwrite an existing value with a blank."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    is_new = conn.execute("SELECT 1 FROM networks WHERE bssid=?", (bssid,)).fetchone() is None
    conn.execute("""
        INSERT INTO networks (bssid, ssid, channel, rssi, security, vendor, device_type, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bssid) DO UPDATE SET
            ssid=excluded.ssid,
            channel=excluded.channel,
            rssi=excluded.rssi,
            security=excluded.security,
            vendor=CASE WHEN excluded.vendor != '' THEN excluded.vendor ELSE networks.vendor END,
            device_type=CASE WHEN excluded.device_type != '' THEN excluded.device_type ELSE networks.device_type END,
            last_seen=excluded.last_seen
    """, (bssid, ssid, channel, rssi, security, vendor, device_type, now, now))
    conn.execute(
        "INSERT INTO rssi_history (bssid, rssi, ts) VALUES (?, ?, ?)",
        (bssid, rssi, now),
    )
    conn.commit()
    return is_new


def upsert_client(path: str, mac: str, bssid: str, rssi: int, vendor: str = "",
                  device_type: str = ""):
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    conn.execute("""
        INSERT INTO clients (mac, bssid, rssi, vendor, device_type, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mac) DO UPDATE SET
            bssid=excluded.bssid,
            rssi=excluded.rssi,
            vendor=CASE WHEN excluded.vendor != '' THEN excluded.vendor ELSE clients.vendor END,
            device_type=CASE WHEN excluded.device_type != '' THEN excluded.device_type ELSE clients.device_type END,
            last_seen=excluded.last_seen
    """, (mac, bssid, rssi, vendor, device_type, now, now))
    conn.execute("""
        UPDATE networks SET clients = (
            SELECT COUNT(*) FROM clients WHERE bssid = ?
        ) WHERE bssid = ?
    """, (bssid, bssid))
    conn.commit()


def upsert_host(path: str, ip: str, mac: str = "", vendor: str = "",
                hostname: str = "", method: str = "arp", device_type: str = "") -> bool:
    """Insert or update a LAN host. Returns True if this IP was newly seen.
    Non-empty fields never overwrite an existing value with a blank."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(path)
    is_new = conn.execute("SELECT 1 FROM hosts WHERE ip=?", (ip,)).fetchone() is None
    conn.execute("""
        INSERT INTO hosts (ip, mac, vendor, hostname, method, device_type, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ip) DO UPDATE SET
            mac         = CASE WHEN excluded.mac         != '' THEN excluded.mac         ELSE hosts.mac         END,
            vendor      = CASE WHEN excluded.vendor      != '' THEN excluded.vendor      ELSE hosts.vendor      END,
            hostname    = CASE WHEN excluded.hostname    != '' THEN excluded.hostname    ELSE hosts.hostname    END,
            device_type = CASE WHEN excluded.device_type != '' THEN excluded.device_type ELSE hosts.device_type END,
            method   = excluded.method,
            last_seen = excluded.last_seen
    """, (ip, mac, vendor, hostname, method, device_type, now, now))
    conn.commit()
    return is_new


def get_hosts(path: str, limit: int = 500) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM hosts ORDER BY last_seen DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


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
    # Reset xplt_synced so the cracked result gets pushed on the next sync
    conn.execute("""
        UPDATE captures SET cracked=1, password=?, xplt_synced=0 WHERE id=?
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


def get_unsynced_networks(path: str, limit: int = 200) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM networks WHERE xplt_synced=0 LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_synced_networks(path: str, bssids: list) -> None:
    if not bssids:
        return
    conn = get_conn(path)
    conn.execute(
        f"UPDATE networks SET xplt_synced=1 WHERE bssid IN ({','.join('?' * len(bssids))})",
        bssids,
    )
    conn.commit()


def get_unsynced_clients(path: str, limit: int = 200) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM clients WHERE xplt_synced=0 LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_synced_clients(path: str, macs: list) -> None:
    if not macs:
        return
    conn = get_conn(path)
    conn.execute(
        f"UPDATE clients SET xplt_synced=1 WHERE mac IN ({','.join('?' * len(macs))})",
        macs,
    )
    conn.commit()


def get_unsynced_captures(path: str, limit: int = 200) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT * FROM captures WHERE xplt_synced=0 LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_synced_captures(path: str, ids: list) -> None:
    if not ids:
        return
    conn = get_conn(path)
    conn.execute(
        f"UPDATE captures SET xplt_synced=1 WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    )
    conn.commit()


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


def delete_network(path: str, bssid: str) -> bool:
    bssid = bssid.upper().strip()
    conn = get_conn(path)
    conn.execute("DELETE FROM rssi_history WHERE bssid=?", (bssid,))
    conn.execute("DELETE FROM clients WHERE bssid=?", (bssid,))
    cur = conn.execute("DELETE FROM networks WHERE bssid=?", (bssid,))
    conn.commit()
    return cur.rowcount > 0


def delete_client(path: str, mac: str) -> bool:
    mac = mac.upper().strip()
    conn = get_conn(path)
    cur = conn.execute("DELETE FROM clients WHERE mac=?", (mac,))
    conn.commit()
    return cur.rowcount > 0


def purge_stale_networks(path: str, days: int = 7) -> int:
    conn = get_conn(path)
    bssids = [r[0] for r in conn.execute(
        "SELECT bssid FROM networks WHERE last_seen < datetime('now', ?)",
        (f"-{days} days",)
    ).fetchall()]
    for bssid in bssids:
        conn.execute("DELETE FROM rssi_history WHERE bssid=?", (bssid,))
        conn.execute("DELETE FROM clients WHERE bssid=?", (bssid,))
    cur = conn.execute(
        "DELETE FROM networks WHERE last_seen < datetime('now', ?)",
        (f"-{days} days",)
    )
    conn.commit()
    return cur.rowcount


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


def get_channel_stats(path: str) -> dict:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT channel, COUNT(*) AS cnt FROM networks WHERE channel IS NOT NULL GROUP BY channel ORDER BY channel"
    ).fetchall()
    return {row["channel"]: row["cnt"] for row in rows}


def get_security_stats(path: str) -> dict:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT security, COUNT(*) AS cnt FROM networks GROUP BY security ORDER BY cnt DESC"
    ).fetchall()
    return {row["security"]: row["cnt"] for row in rows}


def get_vendor_stats(path: str, limit: int = 12) -> list:
    conn = get_conn(path)
    rows = conn.execute("""
        SELECT vendor, COUNT(*) AS cnt
        FROM (SELECT vendor FROM networks WHERE vendor != ''
              UNION ALL
              SELECT vendor FROM clients  WHERE vendor != '')
        GROUP BY vendor ORDER BY cnt DESC LIMIT ?
    """, (limit,)).fetchall()
    return [{"vendor": row["vendor"], "count": row["cnt"]} for row in rows]


def get_rssi_history(path: str, bssid: str, minutes: int = 60) -> list:
    conn = get_conn(path)
    rows = conn.execute(
        "SELECT ts, rssi FROM rssi_history WHERE bssid=? AND ts > datetime('now', ?) ORDER BY ts",
        (bssid.upper(), f"-{minutes} minutes"),
    ).fetchall()
    return [dict(r) for r in rows]


def clean_rssi_history(path: str, hours: int = 24) -> None:
    conn = get_conn(path)
    conn.execute(
        "DELETE FROM rssi_history WHERE ts < datetime('now', ?)",
        (f"-{hours} hours",),
    )
    conn.commit()


def get_graph(path: str) -> dict:
    """Return AP→client relationships for the network graph view."""
    conn = get_conn(path)
    networks = conn.execute(
        "SELECT bssid, ssid, channel, rssi, security, clients FROM networks").fetchall()
    clients = conn.execute(
        "SELECT mac, bssid, rssi, vendor FROM clients").fetchall()
    nodes = [{"id": r["bssid"], "label": r["ssid"] or r["bssid"], "type": "ap",
              "security": r["security"], "channel": r["channel"],
              "rssi": r["rssi"], "clients": r["clients"]} for r in networks]
    nodes += [{"id": r["mac"], "label": r["vendor"] or r["mac"], "type": "client",
               "rssi": r["rssi"], "vendor": r["vendor"], "bssid": r["bssid"]}
              for r in clients]
    edges = [{"source": r["bssid"], "target": r["mac"]} for r in clients if r["bssid"]]
    return {"nodes": nodes, "edges": edges}
