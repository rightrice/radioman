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
    """
    Read PiSugar 2 (IP5209) directly over I2C.
    Matches jayofelony/pwnagotchi register layout:
      Voltage: 0xa2 (low byte) + 0xa3 (high byte, sign at bit 0x20)
      Charging: register 0x55 bit 4
    """
    try:
        import smbus2
        bus = smbus2.SMBus(_I2C_BUS)
        v_low  = bus.read_byte_data(_I2C_ADDR, 0xa2)
        v_high = bus.read_byte_data(_I2C_ADDR, 0xa3)
        chg_reg = bus.read_byte_data(_I2C_ADDR, 0x55)
        bus.close()

        # Reconstruct voltage per IP5209 datasheet / pwnagotchi reference
        v_raw = (v_high << 8) + v_low
        # Sign bit in 0xa3 (high byte) at 0x20 — negative means below baseline
        if v_high & 0x20:
            voltage = (2600 - (v_raw & 0x1fff) * 0.26855) / 1000
        else:
            voltage = (2600 + v_raw * 0.26855) / 1000

        # Linear approximation of IP5209 discharge curve (3.0V=0%, 4.1V=100%)
        pct = int(min(100, max(0, (voltage - 3.0) / (4.1 - 3.0) * 100)))

        # Register 0x55 bit 4: charging in progress (per pwnagotchi / PiSugar I2C manual)
        charging = bool(chg_reg & 0x10)

        return {
            "percent":  pct,
            "voltage":  round(voltage, 3),
            "charging": charging,
            "source":   "i2c",
        }
    except Exception as e:
        log.debug("I2C battery read failed: %s", e)
        return {"percent": -1, "charging": False, "source": "unavailable"}


def read() -> dict:
    """Return battery percent, charging state, and data source.
    Tries direct I2C first (no daemon required), falls back to pisugar-server.
    """
    result = _read_i2c_direct()
    if result["source"] != "unavailable":
        log.debug("Battery (i2c): %d%%  charging=%s", result["percent"], result["charging"])
        return result

    resp = _pisugar_cmd("get battery")
    if resp:
        try:
            pct = float(resp.split(":")[-1].strip().replace("%", ""))
            charging_resp = _pisugar_cmd("get battery_charging")
            charging = "true" in charging_resp.lower()
            log.debug("Battery (pisugar): %d%%  charging=%s", int(pct), charging)
            return {"percent": int(pct), "charging": charging, "source": "pisugar"}
        except Exception:
            pass

    log.debug("Battery unavailable (no I2C device and no pisugar-server)")
    return result


def heart_string(percent: int, filled: str = "♥", empty: str = "♡", total: int = 5) -> str:
    filled_count = round((percent / 100) * total)
    return filled * filled_count + empty * (total - filled_count)
