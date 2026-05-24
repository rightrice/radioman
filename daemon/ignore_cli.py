#!/usr/bin/env python3
"""
radioman-ignore — manage the BSSID ignore list directly via the database.

Usage:
  ignore_cli.py list
  ignore_cli.py add <BSSID> [note...]
  ignore_cli.py remove <BSSID>

Examples:
  python3 ignore_cli.py list
  python3 ignore_cli.py add AA:BB:CC:DD:EE:FF my home router
  python3 ignore_cli.py remove AA:BB:CC:DD:EE:FF

The daemon does not need to be running. Changes take effect immediately
on the next bettercap poll cycle (~5 seconds).

Set RADIOMAN_DB to override the default database path.
"""

import os
import sys

DB_PATH = os.environ.get("RADIOMAN_DB", "/opt/radioman/radioman.db")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db


def _validate_bssid(s: str) -> str:
    s = s.strip().upper()
    if len(s) == 17 and s.count(":") == 5:
        return s
    print(f"Invalid BSSID format: '{s}' (expected XX:XX:XX:XX:XX:XX)")
    sys.exit(1)


def cmd_list():
    rows = db.get_ignored(DB_PATH)
    if not rows:
        print("Ignore list is empty.")
        return
    print(f"{'BSSID':<20}  {'Added (UTC)':<22}  Note")
    print("─" * 66)
    for r in rows:
        added = (r.get("added") or "")[:19].replace("T", " ")
        print(f"{r['bssid']:<20}  {added:<22}  {r.get('note', '')}")


def cmd_add(args):
    if not args:
        print("Usage: ignore_cli.py add <BSSID> [note...]")
        sys.exit(1)
    bssid = _validate_bssid(args[0])
    note  = " ".join(args[1:])
    db.init(DB_PATH)
    db.add_ignored(DB_PATH, bssid, note)
    print(f"Added {bssid} to ignore list." + (f" Note: {note}" if note else ""))


def cmd_remove(args):
    if not args:
        print("Usage: ignore_cli.py remove <BSSID>")
        sys.exit(1)
    bssid   = _validate_bssid(args[0])
    removed = db.remove_ignored(DB_PATH, bssid)
    print(f"{'Removed' if removed else 'Not found'}: {bssid}")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return

    cmd  = argv[0].lower()
    rest = argv[1:]

    if cmd == "list":
        cmd_list()
    elif cmd == "add":
        cmd_add(rest)
    elif cmd in ("remove", "rm", "del", "delete"):
        cmd_remove(rest)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
