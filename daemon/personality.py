import time
import random
import threading
from dataclasses import dataclass, field
from typing import Optional

FACES = {
    "happy":     ["(^‿^)", "(^ᵕ^)", "(◕‿◕)"],
    "excited":   ["(>‿<)", "(★‿★)", "(✿◠‿◠)"],
    "bored":     ["(-‿-)", "(¬_¬)", "(-_-)zzz"],
    "tired":     ["(=_=)", "(￣o￣)", "(。-ω-)"],
    "frustrated":["(>_<)", "(ò_ó)", "(╯°□°）╯"],
    "sleeping":  ["(-.-)Zzz", "(￣ω￣)", "(¬д¬)"],
    "hunting":   ["(ಠ_ಠ)", "(⊙_⊙)", "(ó‿ò)"],
    "cracked":   ["(ﾉ◕ヮ◕)ﾉ", "(★^O^★)", "\\(^▽^)/"],
    "default":   ["(•‿•)", "(◑‿◐)", "(｡◕‿◕｡)"],
}

MOODS = ["happy", "excited", "bored", "tired", "frustrated", "sleeping", "hunting", "default"]

MESSAGES = {
    "happy":      ["found a handshake!", "capturing packets...", "networks everywhere!"],
    "excited":    ["new AP discovered!", "crack successful!", "lots of traffic!"],
    "bored":      ["no networks nearby...", "waiting for targets...", "so quiet..."],
    "tired":      ["battery running low", "been running for hours...", "need a charge"],
    "frustrated": ["missed that handshake", "capture failed", "interference detected"],
    "sleeping":   ["zzz...idle mode", "no activity", "power saving..."],
    "hunting":    ["scanning channels...", "found new client!", "channel hopping..."],
    "cracked":    ["PASSWORD FOUND!", "cracked it!", "got the key!"],
    "default":    ["radioman online", "monitoring...", "standing by"],
}


@dataclass
class State:
    mood:           str   = "default"
    face:           str   = "(•‿•)"
    message:        str   = "radioman online"
    happiness:      float = 0.5
    boredom:        float = 0.0
    last_capture:   float = field(default_factory=time.time)
    total_captures: int   = 0
    total_cracked:  int   = 0
    uptime_start:   float = field(default_factory=time.time)
    _lock:          threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict:
        return {
            "mood":           self.mood,
            "face":           self.face,
            "message":        self.message,
            "happiness":      round(self.happiness, 2),
            "total_captures": self.total_captures,
            "total_cracked":  self.total_cracked,
            "uptime_seconds": int(time.time() - self.uptime_start),
        }


class PersonalityEngine:
    def __init__(self):
        self.state = State()
        self._running = False

    def on_capture(self, ssid: str = ""):
        with self.state._lock:
            self.state.total_captures += 1
            self.state.last_capture = time.time()
            self.state.happiness = min(1.0, self.state.happiness + 0.3)
            self.state.boredom = 0.0
            self._set_mood("excited" if self.state.happiness > 0.8 else "happy")

    def on_crack(self, ssid: str = "", password: str = ""):
        with self.state._lock:
            self.state.total_cracked += 1
            self.state.happiness = 1.0
            self.state.boredom = 0.0
            self._set_mood("cracked")

    def on_new_network(self):
        with self.state._lock:
            self.state.happiness = min(1.0, self.state.happiness + 0.1)
            self.state.boredom = max(0.0, self.state.boredom - 0.1)
            if self.state.mood not in ("excited", "cracked"):
                self._set_mood("hunting")

    def on_low_battery(self, percent: int):
        with self.state._lock:
            self.state.happiness = max(0.0, self.state.happiness - 0.2)
            self._set_mood("tired")

    def tick(self, battery_pct: int, networks_seen: int):
        with self.state._lock:
            idle_seconds = time.time() - self.state.last_capture
            self.state.boredom = min(1.0, idle_seconds / 300)
            self.state.happiness = max(0.0, self.state.happiness - 0.01)

            if battery_pct != -1 and battery_pct < 15:
                self._set_mood("tired")
            elif self.state.mood == "cracked":
                pass  # hold cracked face for a full tick cycle
            elif idle_seconds > 600:
                self._set_mood("sleeping")
            elif self.state.boredom > 0.6:
                self._set_mood("bored")
            elif networks_seen == 0:
                self._set_mood("frustrated")
            elif self.state.happiness > 0.7:
                self._set_mood("happy")
            else:
                self._set_mood("hunting")

    def _set_mood(self, mood: str):
        if mood not in FACES:
            mood = "default"
        self.state.mood = mood
        self.state.face = random.choice(FACES[mood])
        self.state.message = random.choice(MESSAGES[mood])

    def snapshot(self) -> dict:
        with self.state._lock:
            return self.state.to_dict()
