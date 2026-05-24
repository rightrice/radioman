import logging
import threading
import time
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


class Display:
    def __init__(self, model: str = "epd2in13_V3", rotate: int = 180):
        self._model   = model
        self._rotate  = rotate
        self._epd     = None
        self._lock    = threading.Lock()
        self._last    = ""
        self._ready   = False

    def init(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            log.error("Pillow not installed — display disabled")
            return

        self._epd = _load_epd(self._model)
        if self._epd:
            try:
                self._epd.init(self._epd.FULL_UPDATE)
            except TypeError:
                self._epd.init()
            self._epd.Clear(0xFF)
            log.info("E-ink display initialised (%s)", self._model)
        else:
            log.info("Display running in simulation mode")
        self._ready = True

    def _make_frame(self, personality: dict, stats: dict, battery: dict) -> "Image":
        from PIL import Image, ImageDraw, ImageFont
        import os

        img = Image.new("1", (WIDTH, HEIGHT), 255)
        draw = ImageDraw.Draw(img)

        try:
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 10)
            font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
        except Exception:
            font_sm = font_md = font_lg = ImageFont.load_default()

        pct  = battery.get("percent", -1)
        chrg = battery.get("charging", False)
        hearts = _hearts(pct)
        batt_str = f"{hearts} {pct}%" if pct >= 0 else "??%"
        if chrg:
            batt_str += "+"

        # ── Header bar ───────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (WIDTH, 16)], fill=0)
        draw.text((3, 2),  "RADIOMAN",        font=font_sm, fill=255)
        draw.text((WIDTH - len(batt_str) * 6 - 3, 2), batt_str, font=font_sm, fill=255)

        # ── Face ─────────────────────────────────────────────────────────────
        face = personality.get("face", "(•‿•)")
        draw.text((4, 22), face, font=font_lg, fill=0)

        # ── Mood message ──────────────────────────────────────────────────────
        msg = personality.get("message", "")[:28]
        draw.text((4, 42), msg, font=font_sm, fill=0)

        # ── Divider ───────────────────────────────────────────────────────────
        draw.line([(0, 56), (WIDTH, 56)], fill=0, width=1)

        # ── Stats grid ───────────────────────────────────────────────────────
        aps  = stats.get("networks", 0)
        clis = stats.get("clients", 0)
        hs   = stats.get("captures", 0)
        crk  = stats.get("cracked", 0)

        row1 = f"APs:{aps:<4} CLI:{clis:<4} HS:{hs}"
        row2 = f"Cracked:{crk}"

        up = personality.get("uptime_seconds", 0)
        h, m = divmod(up // 60, 60)
        row3 = f"Up:{h:02d}h{m:02d}m  mood:{personality.get('mood','?')[:8]}"

        draw.text((3, 60), row1, font=font_sm, fill=0)
        draw.text((3, 74), row2, font=font_sm, fill=0)
        draw.text((3, 88), row3, font=font_sm, fill=0)

        if self._rotate:
            img = img.rotate(self._rotate)

        return img

    def update(self, personality: dict, stats: dict, battery: dict):
        if not self._ready:
            return

        key = f"{personality.get('face')}|{stats}|{battery.get('percent')}"
        if key == self._last:
            return

        with self._lock:
            try:
                img = self._make_frame(personality, stats, battery)
                if self._epd:
                    buf = self._epd.getbuffer(img)
                    try:
                        self._epd.displayPartBaseImage(buf)
                    except AttributeError:
                        self._epd.display(buf)
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
                self._epd.init()
                self._epd.Clear(0xFF)
            except Exception:
                pass


def _hearts(pct: int, total: int = 5) -> str:
    if pct < 0:
        return "♡" * total
    filled = round((pct / 100) * total)
    return "♥" * filled + "♡" * (total - filled)


def _sim_print(personality: dict, stats: dict, battery: dict):
    pct = battery.get("percent", -1)
    print(f"\033[2J\033[H", end="")
    print("┌" + "─" * 36 + "┐")
    face = personality.get("face", "(•‿•)")
    hearts = _hearts(pct)
    print(f"│ RADIOMAN   {hearts} {pct:>3}%       │")
    print(f"│ {face:<34} │")
    print(f"│ {personality.get('message',''):<34} │")
    print("├" + "─" * 36 + "┤")
    print(f"│ APs:{stats.get('networks',0):<5} CLI:{stats.get('clients',0):<5} HS:{stats.get('captures',0):<4} │")
    print(f"│ Cracked:{stats.get('cracked',0):<5} Mood:{personality.get('mood','?'):<10} │")
    print("└" + "─" * 36 + "┘")
