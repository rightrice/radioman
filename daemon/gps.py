#!/usr/bin/env python3
"""
gps.py — position source for wardrive mode.

Two backends, selected by config ([gps] mode = gpsd|serial|off):
  * gpsd   — reads from a running gpsd (python3-gps module if present, else a
             raw JSON socket to 127.0.0.1:2947). Works with most USB receivers.
  * serial — parses raw NMEA ($GxGGA / $GxRMC) straight off a serial/USB dongle
             (/dev/ttyACM0, /dev/ttyUSB0, ...) via pyserial.

Everything degrades gracefully: no gpsd, no pyserial, or no device just leaves
the fix at {"fix": 0} forever — nothing else in the daemon breaks.

`current_fix()` is thread-safe and returns a snapshot:
    {fix, lat, lon, alt, accuracy, speed, ts}
where fix is 0 (none), 2 (2D) or 3 (3D).
"""

import logging
import threading
import time
from datetime import datetime

log = logging.getLogger("gps")


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _nmea_to_decimal(raw, hemi):
    """Convert an NMEA ddmm.mmmm / dddmm.mmmm field to signed decimal degrees."""
    if not raw or "." not in raw:
        return None
    dot = raw.index(".")
    deg_len = dot - 2  # the two digits before the dot are whole minutes
    if deg_len < 1:
        return None
    deg = _f(raw[:deg_len])
    minutes = _f(raw[deg_len:])
    if deg is None or minutes is None:
        return None
    val = deg + minutes / 60.0
    if hemi in ("S", "W"):
        val = -val
    return round(val, 6)


def _epc(*vals):
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(max(nums), 1) if nums else None


class GPSReader:
    def __init__(self, mode="off", device="/dev/ttyACM0", baud=9600):
        self.mode = (mode or "off").lower().strip()
        self.device = device
        self.baud = int(baud or 9600)
        self.enabled = self.mode in ("gpsd", "serial")
        self._fix = {"fix": 0, "lat": None, "lon": None, "alt": None,
                     "accuracy": None, "speed": None, "ts": None}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        if not self.enabled:
            log.info("GPS disabled (mode=%s)", self.mode)
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="gps")
        self._thread.start()
        log.info("GPS reader started (mode=%s device=%s)", self.mode, self.device)

    def stop(self):
        self._running = False

    def current_fix(self):
        with self._lock:
            return dict(self._fix)

    # ── Internals ─────────────────────────────────────────────────────────────
    def _set(self, **kw):
        with self._lock:
            self._fix.update(kw)
            self._fix["ts"] = datetime.utcnow().isoformat()

    def _run(self):
        while self._running:
            try:
                if self.mode == "gpsd":
                    self._run_gpsd()
                elif self.mode == "serial":
                    self._run_serial()
                else:
                    return
            except Exception as e:
                log.warning("GPS %s error: %s — retrying in 5s", self.mode, e)
                self._set(fix=0)
                time.sleep(5)

    # ── gpsd backend ──────────────────────────────────────────────────────────
    def _run_gpsd(self):
        try:
            import gps as gpslib
        except Exception:
            return self._run_gpsd_socket()
        session = gpslib.gps(mode=gpslib.WATCH_ENABLE | gpslib.WATCH_NEWSTYLE)
        while self._running:
            try:
                report = session.next()
            except StopIteration:
                time.sleep(1)
                continue
            if getattr(report, "class", "") != "TPV":
                continue
            mode = getattr(report, "mode", 0)
            if mode >= 2:
                self._set(
                    fix=mode,
                    lat=getattr(report, "lat", None),
                    lon=getattr(report, "lon", None),
                    alt=getattr(report, "alt", None) if mode >= 3 else None,
                    accuracy=_epc(getattr(report, "epx", None),
                                  getattr(report, "epy", None)),
                    speed=getattr(report, "speed", None),
                )
            else:
                self._set(fix=0)

    def _run_gpsd_socket(self):
        import json
        import socket
        s = socket.create_connection(("127.0.0.1", 2947), timeout=5)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        s.settimeout(5)
        buf = b""
        try:
            while self._running:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        obj = json.loads(line.decode("utf-8", "ignore"))
                    except Exception:
                        continue
                    if obj.get("class") != "TPV":
                        continue
                    mode = obj.get("mode", 0)
                    if mode >= 2:
                        self._set(
                            fix=mode,
                            lat=obj.get("lat"), lon=obj.get("lon"),
                            alt=obj.get("alt") if mode >= 3 else None,
                            accuracy=_epc(obj.get("epx"), obj.get("epy")),
                            speed=obj.get("speed"),
                        )
                    else:
                        self._set(fix=0)
        finally:
            try:
                s.close()
            except Exception:
                pass

    # ── Serial / raw NMEA backend ─────────────────────────────────────────────
    def _run_serial(self):
        try:
            import serial
        except Exception:
            log.warning("pyserial not installed — serial GPS unavailable "
                        "(sudo apt install python3-serial)")
            time.sleep(30)
            return
        ser = serial.Serial(self.device, self.baud, timeout=2)
        try:
            while self._running:
                raw = ser.readline().decode("ascii", "ignore").strip()
                if raw.startswith("$"):
                    self._parse_nmea(raw)
        finally:
            try:
                ser.close()
            except Exception:
                pass

    def _parse_nmea(self, line):
        body = line.split("*")[0]
        parts = body.split(",")
        typ = parts[0][3:] if len(parts[0]) >= 6 else parts[0].lstrip("$")
        if typ == "GGA" and len(parts) >= 10:
            if parts[6] in ("", "0"):          # fix quality 0 = no fix
                self._set(fix=0)
                return
            lat = _nmea_to_decimal(parts[2], parts[3])
            lon = _nmea_to_decimal(parts[4], parts[5])
            hdop = _f(parts[8])
            alt = _f(parts[9])
            # rough accuracy: HDOP × nominal 5 m UERE
            acc = round(hdop * 5.0, 1) if hdop else None
            self._set(fix=3 if alt is not None else 2,
                      lat=lat, lon=lon, alt=alt, accuracy=acc)
        elif typ == "RMC" and len(parts) >= 8:
            if parts[2] != "A":                # status V = void
                self._set(fix=0)
                return
            lat = _nmea_to_decimal(parts[3], parts[4])
            lon = _nmea_to_decimal(parts[5], parts[6])
            spd_kn = _f(parts[7])
            spd = round(spd_kn * 0.514444, 2) if spd_kn is not None else None
            self._set(fix=2, lat=lat, lon=lon, speed=spd)
