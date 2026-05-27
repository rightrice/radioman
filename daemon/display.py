import logging
import threading
from typing import Optional

log = logging.getLogger("display")

WIDTH  = 250
HEIGHT = 122


def _load_epd(model: str):
    try:
        mod = __import__(f"waveshare_epd.{model}", fromlist=[model])
        return mod.EPD()
    except ImportError:
        log.warning("waveshare_epd not found — running in framebuffer simulation mode")
        return None
    except Exception as e:
        log.error("EPD load error: %s", e)
        return None


def _init_epd(epd) -> None:
    # V2/V3 take a mode constant (FULL_UPDATE); V4 takes no arguments.
    if hasattr(epd, "FULL_UPDATE"):
        epd.init(epd.FULL_UPDATE)
    else:
        epd.init()


class Display:
    def __init__(self, model: str = "epd2in13_V2", rotate: int = 180):
        self._model      = model
        self._rotate     = rotate
        self._epd        = None
        self._lock       = threading.Lock()
        self._last       = ""
        self._ready      = False
        self._update_cnt = 0
        self._fonts      = None

    def init(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            log.error("Pillow not installed — display disabled")
            return

        self._epd = _load_epd(self._model)
        if self._epd:
            _init_epd(self._epd)
            self._epd.Clear(0xFF)
            log.info("E-ink display initialised (%s)", self._model)
        else:
            log.info("Display running in simulation mode")
        self._ready = True

    def _load_fonts(self):
        from PIL import ImageFont
        import os
        if self._fonts:
            return self._fonts
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
            "/usr/share/fonts/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        try:
            if path:
                self._fonts = (
                    ImageFont.truetype(path, 10),  # font_sm
                    ImageFont.truetype(path, 9),   # font_xs
                    ImageFont.truetype(path, 13),  # font_lg
                )
            else:
                raise FileNotFoundError
        except Exception:
            default = ImageFont.load_default()
            self._fonts = (default, default, default)
        return self._fonts

    def _make_frame(self, personality: dict, stats: dict, battery: dict) -> "Image":
        from PIL import Image, ImageDraw

        img  = Image.new("1", (WIDTH, HEIGHT), 255)
        draw = ImageDraw.Draw(img)

        font_sm, font_xs, font_lg = self._load_fonts()

        pct      = battery.get("percent", -1)
        chrg     = battery.get("charging", False)
        hearts   = _hearts(pct)
        batt_str = f"{hearts} {pct}%" if pct >= 0 else "— %"
        if chrg:
            batt_str += "+"

        scanning = personality.get("scanning", False)
        msg      = personality.get("message", "")
        up       = personality.get("uptime_seconds", 0)
        h, m     = divmod(up // 60, 60)

        aps  = stats.get("networks",  0)
        clis = stats.get("clients",   0)
        hs   = stats.get("captures",  0)
        crk  = stats.get("cracked",   0)

        # ── Header bar ───────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (WIDTH, 16)], fill=0)
        draw.text((4, 2), "RADIOMAN", font=font_sm, fill=255)
        batt_w = int(draw.textlength(batt_str, font=font_sm))
        draw.text((WIDTH - batt_w - 4, 2), batt_str, font=font_sm, fill=255)

        # ── Scan status + uptime ─────────────────────────────────────────────
        status_str  = "SCANNING" if scanning else "IDLE"
        uptime_str  = f"up {h:02d}h {m:02d}m"
        draw.text((4, 20), status_str, font=font_lg, fill=0)
        up_w = int(draw.textlength(uptime_str, font=font_xs))
        draw.text((WIDTH - up_w - 4, 24), uptime_str, font=font_xs, fill=0)

        # ── Divider ──────────────────────────────────────────────────────────
        draw.line([(4, 38), (WIDTH - 4, 38)], fill=0, width=1)

        # ── Stats grid (two columns) ─────────────────────────────────────────
        col2 = WIDTH // 2 + 4
        draw.text((4,    43), f"APs   {aps}",   font=font_xs, fill=0)
        draw.text((col2, 43), f"CLI   {clis}",  font=font_xs, fill=0)
        draw.text((4,    57), f"HS    {hs}",    font=font_xs, fill=0)
        draw.text((col2, 57), f"PWD   {crk}",   font=font_xs, fill=0)

        # ── Divider ──────────────────────────────────────────────────────────
        draw.line([(4, 71), (WIDTH - 4, 71)], fill=0, width=1)

        # ── Status message ───────────────────────────────────────────────────
        draw.text((4, 76), msg[:36], font=font_xs, fill=0)

        if self._rotate:
            img = img.rotate(self._rotate)

        return img

    def update(self, personality: dict, stats: dict, battery: dict):
        if not self._ready:
            return

        key = (f"{personality.get('scanning')}|{personality.get('mood')}|"
               f"{battery.get('percent')}|{stats.get('networks')}|"
               f"{stats.get('clients')}|{stats.get('captures')}|{stats.get('cracked')}")
        if key == self._last:
            return

        with self._lock:
            try:
                img = self._make_frame(personality, stats, battery)
                if self._epd:
                    if self._update_cnt > 0 and self._update_cnt % 20 == 0:
                        _init_epd(self._epd)
                    buf = self._epd.getbuffer(img)
                    self._epd.display(buf)
                    self._update_cnt += 1
                else:
                    _sim_print(personality, stats, battery)
                self._last = key
            except Exception as e:
                log.error("Display update error: %s", e)

    def sleep(self):
        if self._epd:
            try:
                self._epd.sleep()
            except Exception:
                pass

    def clear(self):
        if self._epd:
            try:
                _init_epd(self._epd)
                self._epd.Clear(0xFF)
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hearts(pct: int, total: int = 5) -> str:
    if pct < 0:
        return "♡" * total
    filled = round((pct / 100) * total)
    return "♥" * filled + "♡" * (total - filled)


def _sim_print(personality: dict, stats: dict, battery: dict):
    pct      = battery.get("percent", -1)
    hearts   = _hearts(pct)
    scanning = personality.get("scanning", False)
    msg      = personality.get("message", "")
    up       = personality.get("uptime_seconds", 0)
    h, m     = divmod(up // 60, 60)
    status   = "SCANNING" if scanning else "IDLE"
    print("\033[2J\033[H", end="")
    print("┌" + "─" * 40 + "┐")
    print(f"│ RADIOMAN  {hearts} {pct:>3}%  {status:<8}  up {h:02d}h{m:02d}m │")
    print("├" + "─" * 40 + "┤")
    s = stats
    print(f"│  APs {s.get('networks',0):<5}  CLI {s.get('clients',0):<5}  HS {s.get('captures',0):<4}  PWD {s.get('cracked',0):<3} │")
    print("├" + "─" * 40 + "┤")
    print(f"│  {msg:<38} │")
    print("└" + "─" * 40 + "┘")
