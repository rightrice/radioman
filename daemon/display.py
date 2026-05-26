import logging
import threading
from typing import Optional

log = logging.getLogger("display")

WIDTH  = 250
HEIGHT = 122
GHOST_W = 62   # pixels reserved for ghost panel
STATS_X = 66   # x-start of stats panel


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
                    ImageFont.truetype(path, 10),
                    ImageFont.truetype(path, 9),
                )
            else:
                raise FileNotFoundError
        except Exception:
            default = ImageFont.load_default()
            self._fonts = (default, default)
        return self._fonts

    def _make_frame(self, personality: dict, stats: dict, battery: dict) -> "Image":
        from PIL import Image, ImageDraw

        img  = Image.new("1", (WIDTH, HEIGHT), 255)
        draw = ImageDraw.Draw(img)

        font_sm, font_xs = self._load_fonts()

        pct      = battery.get("percent", -1)
        chrg     = battery.get("charging", False)
        hearts   = _hearts(pct)
        batt_str = f"{hearts} {pct}%" if pct >= 0 else "??%"
        if chrg:
            batt_str += "+"

        # ── Header bar ───────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (WIDTH, 16)], fill=0)
        draw.text((3, 2), "RADIOMAN", font=font_sm, fill=255)
        batt_w = int(draw.textlength(batt_str, font=font_sm))
        draw.text((WIDTH - batt_w - 3, 2), batt_str, font=font_sm, fill=255)

        # ── Ghost (left panel) ────────────────────────────────────────────────
        mood = personality.get("mood", "default")
        _draw_ghost(draw, ox=3, oy=18, mood=mood)

        # ── Vertical divider ─────────────────────────────────────────────────
        draw.line([(STATS_X - 2, 17), (STATS_X - 2, HEIGHT)], fill=0, width=1)

        # ── Stats (right panel) ───────────────────────────────────────────────
        sx = STATS_X

        msg = personality.get("message", "")
        draw.text((sx, 20), msg[:24], font=font_sm, fill=0)

        draw.line([(sx, 33), (WIDTH - 2, 33)], fill=0, width=1)

        aps  = stats.get("networks",  0)
        clis = stats.get("clients",   0)
        hs   = stats.get("captures",  0)
        crk  = stats.get("cracked",   0)

        draw.text((sx, 37), f"APs  {aps}",      font=font_xs, fill=0)
        draw.text((sx, 50), f"CLI  {clis}",     font=font_xs, fill=0)
        draw.text((sx, 63), f"HS   {hs}",       font=font_xs, fill=0)
        draw.text((sx, 76), f"PWD  {crk}",      font=font_xs, fill=0)

        up   = personality.get("uptime_seconds", 0)
        h, m = divmod(up // 60, 60)
        draw.text((sx, 89), f"Up {h:02d}h{m:02d}m", font=font_xs, fill=0)

        draw.line([(sx, 102), (WIDTH - 2, 102)], fill=0, width=1)
        draw.text((sx, 105), mood[:16], font=font_xs, fill=0)

        if self._rotate:
            img = img.rotate(self._rotate)

        return img

    def update(self, personality: dict, stats: dict, battery: dict):
        if not self._ready:
            return

        key = (f"{personality.get('mood')}|{battery.get('percent')}|"
               f"{stats.get('networks')}|{stats.get('clients')}|"
               f"{stats.get('captures')}|{stats.get('cracked')}")
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


# ── Ghost pixel art ───────────────────────────────────────────────────────────

def _draw_ghost(draw: "ImageDraw.ImageDraw", ox: int, oy: int, mood: str):
    """
    Draw the radioman ghost mascot at (ox, oy).
    Fits within GHOST_W x 100px. 1-bit: 0=black, 255=white.
    """
    # Antenna dots
    draw.ellipse([ox+15, oy+0,  ox+19, oy+4],  fill=0)
    draw.ellipse([ox+38, oy+0,  ox+42, oy+4],  fill=0)
    draw.line(   [ox+17, oy+4,  ox+17, oy+9],  fill=0, width=1)
    draw.line(   [ox+40, oy+4,  ox+40, oy+9],  fill=0, width=1)

    # Ghost body — filled black dome + rectangle
    draw.ellipse([ox+2,  oy+6,  ox+55, oy+46], fill=0)
    draw.rectangle([ox+2, oy+26, ox+55, oy+76], fill=0)

    # Wavy bottom — 3 white scallops with 2px black peaks between them
    draw.ellipse([ox+1,  oy+60, ox+18, oy+88], fill=255)
    draw.ellipse([ox+20, oy+60, ox+37, oy+88], fill=255)
    draw.ellipse([ox+39, oy+60, ox+56, oy+88], fill=255)

    # Eyes — white ovals
    draw.ellipse([ox+9,  oy+18, ox+26, oy+36], fill=255)
    draw.ellipse([ox+31, oy+18, ox+48, oy+36], fill=255)

    # Pupils + WiFi emblem based on mood
    _draw_eyes(draw, ox, oy, mood)
    _draw_wifi_emblem(draw, ox, oy, mood)


def _draw_eyes(draw: "ImageDraw.ImageDraw", ox: int, oy: int, mood: str):
    if mood in ("sleeping", "tired"):
        # Half-closed: fill lower half of eye white, draw drooping lid
        draw.rectangle([ox+9,  oy+27, ox+26, oy+36], fill=0)
        draw.rectangle([ox+31, oy+27, ox+48, oy+36], fill=0)
        draw.ellipse(  [ox+11, oy+18, ox+24, oy+32], fill=255)
        draw.ellipse(  [ox+33, oy+18, ox+46, oy+32], fill=255)
        # Small pupils looking down
        draw.ellipse([ox+15, oy+24, ox+20, oy+29], fill=0)
        draw.ellipse([ox+37, oy+24, ox+42, oy+29], fill=0)

    elif mood in ("excited", "cracked"):
        # Wide shining eyes — pupils shifted up-center
        draw.ellipse([ox+14, oy+20, ox+21, oy+27], fill=0)
        draw.ellipse([ox+36, oy+20, ox+43, oy+27], fill=0)
        # Shine dots
        draw.ellipse([ox+21, oy+20, ox+24, oy+23], fill=255)
        draw.ellipse([ox+43, oy+20, ox+46, oy+23], fill=255)

    elif mood == "frustrated":
        # Angled pupils — looking inward and down
        draw.ellipse([ox+17, oy+24, ox+24, oy+31], fill=0)
        draw.ellipse([ox+33, oy+24, ox+40, oy+31], fill=0)
        # Furrowed brow lines
        draw.line([ox+9,  oy+18, ox+26, oy+22], fill=0, width=2)
        draw.line([ox+31, oy+22, ox+48, oy+18], fill=0, width=2)

    elif mood == "bored":
        # Half-mast pupils
        draw.rectangle([ox+9,  oy+28, ox+26, oy+36], fill=0)
        draw.rectangle([ox+31, oy+28, ox+48, oy+36], fill=0)
        draw.ellipse(  [ox+11, oy+18, ox+24, oy+34], fill=255)
        draw.ellipse(  [ox+33, oy+18, ox+46, oy+34], fill=255)
        draw.ellipse([ox+15, oy+22, ox+20, oy+28], fill=0)
        draw.ellipse([ox+37, oy+22, ox+42, oy+28], fill=0)

    else:
        # Default / happy / hunting — centered pupils
        draw.ellipse([ox+14, oy+22, ox+21, oy+30], fill=0)
        draw.ellipse([ox+36, oy+22, ox+43, oy+30], fill=0)


def _draw_wifi_emblem(draw: "ImageDraw.ImageDraw", ox: int, oy: int, mood: str):
    """
    WiFi symbol on ghost chest — white on black body.
    Dot at bottom, arcs curve upward. Signal strength follows mood:
      1 bar: bored / sleeping / tired
      2 bars: default / hunting / happy
      3 bars: excited / cracked (full signal)
    """
    cx = ox + 28
    cy = oy + 56

    # Dot
    draw.ellipse([cx-3, cy-3, cx+3, cy+3], fill=255)

    if mood in ("bored", "sleeping", "tired"):
        bars = [8]
    elif mood in ("excited", "cracked"):
        bars = [8, 14, 20]
    else:
        bars = [8, 14]

    for r in bars:
        draw.arc([cx-r, cy-r, cx+r, cy+r], start=215, end=325, fill=255, width=2)


def _draw_mouth(draw: "ImageDraw.ImageDraw", ox: int, oy: int, mood: str):
    if mood in ("happy", "hunting"):
        # Smile arc
        draw.arc([ox+18, oy+40, ox+40, oy+56], start=10, end=170, fill=255, width=2)

    elif mood in ("excited", "cracked"):
        # Open O — big excited mouth
        draw.ellipse([ox+18, oy+40, ox+40, oy+58], fill=255)
        draw.ellipse([ox+21, oy+43, ox+37, oy+55], fill=0)

    elif mood == "frustrated":
        # Frown arc
        draw.arc([ox+18, oy+46, ox+40, oy+62], start=190, end=350, fill=255, width=2)

    elif mood in ("sleeping", "bored"):
        # Flat line
        draw.line([ox+19, oy+48, ox+39, oy+48], fill=255, width=2)

    elif mood == "tired":
        # Slight frown
        draw.arc([ox+20, oy+44, ox+38, oy+56], start=200, end=340, fill=255, width=2)

    else:
        # Small oval — neutral/default
        draw.ellipse([ox+22, oy+42, ox+36, oy+52], fill=255)
        draw.ellipse([ox+24, oy+44, ox+34, oy+50], fill=0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hearts(pct: int, total: int = 5) -> str:
    if pct < 0:
        return "♡" * total
    filled = round((pct / 100) * total)
    return "♥" * filled + "♡" * (total - filled)


def _sim_print(personality: dict, stats: dict, battery: dict):
    pct    = battery.get("percent", -1)
    hearts = _hearts(pct)
    mood   = personality.get("mood", "default")
    print("\033[2J\033[H", end="")
    print("┌" + "─" * 38 + "┐")
    print(f"│ RADIOMAN [{mood:<10}]  {hearts} {pct:>3}%  │")
    print(f"│ {personality.get('message',''):<38} │")
    print("├" + "─" * 38 + "┤")
    print(f"│ APs:{stats.get('networks',0):<5} CLI:{stats.get('clients',0):<5} HS:{stats.get('captures',0):<4} PWD:{stats.get('cracked',0):<3} │")
    print("└" + "─" * 38 + "┘")
