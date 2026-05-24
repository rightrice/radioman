import socket
import logging

log = logging.getLogger("battery")

_PISUGAR_SOCK = "/tmp/pisugar-server.sock"
_I2C_ADDR = 0x75
_I2C_BUS = 1


def _pisugar_cmd(cmd: str) -> str:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(_PISUGAR_SOCK)
            s.sendall((cmd + "\n").encode())
            return s.recv(256).decode().strip()
    except Exception:
        return ""


def _read_i2c_direct() -> dict:
    try:
        import smbus2
        bus = smbus2.SMBus(_I2C_BUS)
        pct_raw = bus.read_byte_data(_I2C_ADDR, 0x2A)
        status  = bus.read_byte_data(_I2C_ADDR, 0x02)
        bus.close()
        return {
            "percent":  min(100, max(0, pct_raw)),
            "charging": bool(status & 0x40),
            "source":   "i2c",
        }
    except Exception as e:
        log.debug("I2C battery read failed: %s", e)
        return {"percent": -1, "charging": False, "source": "unavailable"}


def read() -> dict:
    """Return battery percent, charging state, and data source."""
    resp = _pisugar_cmd("get battery")
    if resp:
        try:
            pct = float(resp.split(":")[-1].strip().replace("%", ""))
            charging_resp = _pisugar_cmd("get battery_charging")
            charging = "true" in charging_resp.lower()
            return {"percent": int(pct), "charging": charging, "source": "pisugar"}
        except Exception:
            pass

    return _read_i2c_direct()


def heart_string(percent: int, filled: str = "♥", empty: str = "♡", total: int = 5) -> str:
    filled_count = round((percent / 100) * total)
    return filled * filled_count + empty * (total - filled_count)
