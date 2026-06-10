"""
passwords.py — offline password intelligence over cracked Wi-Fi keys.

Pure, deterministic analysis: no hardware, no network, no LLM required. Operates
on cracked captures (captures.password where cracked=1) plus their SSID for
context. Surfaces:
  * strength scoring (length, character classes, entropy estimate, rating)
  * pattern detection (keyboard walks, year/date suffixes, name+digits, …)
  * ISP/router default-password shapes (uppercase-hex, SSID-derived, …)
  * cross-network password reuse

The AI tab consumes summarize() for grounded prompts; the dashboard panel and
/api/passwords consume analyze() directly, so the feature is useful even when
the local model isn't installed.
"""

import math
import re
from collections import Counter

# ── Helpers ────────────────────────────────────────────────────────────────

def mask(pw: str) -> str:
    """First two chars + stars — mirrors the crack-event masking elsewhere."""
    pw = pw or ""
    if len(pw) <= 2:
        return "*" * len(pw)
    return pw[:2] + "*" * (len(pw) - 2)


def _charset_size(pw: str) -> int:
    size = 0
    if re.search(r"[a-z]", pw): size += 26
    if re.search(r"[A-Z]", pw): size += 26
    if re.search(r"[0-9]", pw): size += 10
    if re.search(r"[^A-Za-z0-9]", pw): size += 33
    return size or 1


def entropy_bits(pw: str) -> float:
    """Rough Shannon-space estimate: length × log2(charset). Not zxcvbn, but a
    consistent, explainable signal for ranking weak vs strong keys."""
    if not pw:
        return 0.0
    return round(len(pw) * math.log2(_charset_size(pw)), 1)


def _rating(bits: float, length: int) -> str:
    # WPA keys are >=8 chars; bands tuned for that floor.
    if length < 8 or bits < 28:  return "very weak"
    if bits < 36:                return "weak"
    if bits < 60:                return "fair"
    if bits < 90:                return "strong"
    return "very strong"


# A small embedded list of the most common passwords / base words. The entropy
# model can't know "password1" is guessable, so a dictionary hit caps the rating.
_COMMON = {
    "password", "passw0rd", "welcome", "admin", "letmein", "monkey", "dragon",
    "sunshine", "princess", "football", "baseball", "iloveyou", "qwerty",
    "abc123", "master", "login", "starwars", "whatever", "trustno1", "superman",
    "batman", "internet", "computer", "secret", "changeme", "default", "guest",
    "summer", "winter", "spring", "autumn", "wifi", "wireless", "linksys",
    "netgear", "home", "family", "hello", "love", "money", "freedom",
}


def _base_word(pw: str) -> str:
    """Strip a trailing run of digits/symbols so 'password1!' → 'password'."""
    return re.sub(r"[0-9!@#$%^&*._\-]+$", "", pw).lower()


def is_common(pw: str) -> bool:
    if not pw:
        return False
    return pw.lower() in _COMMON or _base_word(pw) in _COMMON


def score(pw: str) -> dict:
    classes = sum(bool(re.search(p, pw)) for p in
                  (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    bits = entropy_bits(pw)
    rating = _rating(bits, len(pw))
    # A dictionary hit means it's guessable regardless of raw entropy.
    if is_common(pw) and rating not in ("very weak", "weak"):
        rating = "weak"
    return {
        "length":  len(pw),
        "classes": classes,
        "entropy": bits,
        "rating":  rating,
    }


# ── Pattern detection ────────────────────────────────────────────────────────

# Rows/sequences people walk across a keyboard or keypad.
_KEYBOARD_RUNS = [
    "qwertyuiop", "asdfghjkl", "zxcvbnm",
    "1234567890", "0987654321",
    "1qaz2wsx3edc", "qazwsxedc", "1q2w3e4r5t", "qwerty", "qweasd",
    "abcdefghijklmnopqrstuvwxyz",
]


def _has_keyboard_walk(pw: str, minlen: int = 4) -> bool:
    low = pw.lower()
    for run in _KEYBOARD_RUNS:
        for i in range(len(run) - minlen + 1):
            seg = run[i:i + minlen]
            if seg in low or seg[::-1] in low:
                return True
    return False


def detect_patterns(pw: str, ssid: str = "") -> list:
    """Return a list of human-readable weakness tags for one password."""
    tags = []
    if not pw:
        return tags
    low = pw.lower()

    if re.fullmatch(r"[a-z]+", pw):            tags.append("all lowercase")
    elif re.fullmatch(r"[A-Z]+", pw):          tags.append("all uppercase")
    if re.fullmatch(r"\d+", pw):               tags.append("digits only")
    if re.fullmatch(r"[a-z0-9]+", pw) and re.search(r"[a-z]", pw) and re.search(r"\d", pw):
        tags.append("lowercase+digits only")
    if _has_keyboard_walk(pw):                 tags.append("keyboard walk")
    if re.search(r"(19[5-9]\d|20[0-3]\d)$", pw): tags.append("year suffix")
    if re.fullmatch(r"\d{6,8}", pw):           tags.append("date-like")
    if re.fullmatch(r"[A-Za-z]{2,}\d{1,6}", pw): tags.append("word+digits")
    if re.search(r"(.)\1{3,}", pw):            tags.append("repeated chars")
    if re.fullmatch(r"\d{10,11}", pw):         tags.append("phone-number-like")
    if is_common(pw):                          tags.append("common word/password")
    if ssid and len(ssid) >= 3 and ssid.lower() in low:
        tags.append("contains SSID")
    return tags


# ── Default-password (ISP / router) heuristics ──────────────────────────────

def default_shape(pw: str, ssid: str = "") -> str:
    """Return a label if the key matches a known factory-default shape, else ''.
    These shapes are how many ISP routers ship their out-of-box WPA keys."""
    if not pw:
        return ""
    # Require at least one hex LETTER so pure-digit keys (dates/PINs) don't
    # masquerade as hex defaults.
    has_hex_letter = bool(re.search(r"[A-Fa-f]", pw))
    if has_hex_letter and re.fullmatch(r"[0-9A-F]{8,10}", pw):
        return "uppercase-hex (common ISP default)"
    if has_hex_letter and re.fullmatch(r"[0-9a-f]{10,16}", pw):
        return "lowercase-hex (common ISP default)"
    if has_hex_letter and re.fullmatch(r"[0-9A-Fa-f]{12,}", pw):
        return "long hex (router default)"
    if re.fullmatch(r"[A-Za-z]{4,}\d{4,8}", pw) and ssid and ssid.lower()[:4] in pw.lower():
        return "SSID-derived default"
    return ""


# ── Reuse detection ──────────────────────────────────────────────────────────

def find_reuse(items: list) -> list:
    """Passwords used on more than one distinct network (BSSID or SSID)."""
    by_pw = {}
    for it in items:
        pw = it.get("password")
        if not pw:
            continue
        key = (it.get("bssid") or "").upper() or (it.get("ssid") or "")
        by_pw.setdefault(pw, set()).add(key)
    out = []
    for pw, nets in by_pw.items():
        if len(nets) > 1:
            ssids = sorted({(it.get("ssid") or it.get("bssid") or "?")
                            for it in items if it.get("password") == pw})
            out.append({"masked": mask(pw), "count": len(nets), "ssids": ssids})
    return sorted(out, key=lambda x: x["count"], reverse=True)


# ── Aggregate analysis ───────────────────────────────────────────────────────

def analyze(items: list) -> dict:
    """Full structured analysis over cracked captures.
    items: list of dicts with keys ssid, bssid, password (cracked keys only)."""
    items = [it for it in items if it.get("password")]
    total = len(items)
    if not total:
        return {"total": 0, "entries": [], "ratings": {}, "patterns": {},
                "defaults": 0, "reuse": [], "avg_entropy": 0, "weak_pct": 0,
                "recommendations": []}

    entries, ratings, patterns = [], Counter(), Counter()
    defaults, ent_sum, weak = 0, 0.0, 0
    for it in items:
        pw   = it["password"]
        ssid = it.get("ssid") or ""
        sc   = score(pw)
        pats = detect_patterns(pw, ssid)
        dflt = default_shape(pw, ssid)
        ratings[sc["rating"]] += 1
        for p in pats:
            patterns[p] += 1
        if dflt:
            defaults += 1
        ent_sum += sc["entropy"]
        if sc["rating"] in ("very weak", "weak"):
            weak += 1
        entries.append({
            "ssid":    ssid or "(hidden)",
            "bssid":   it.get("bssid") or "",
            "masked":  mask(pw),
            "length":  sc["length"],
            "entropy": sc["entropy"],
            "rating":  sc["rating"],
            "patterns": pats,
            "default": dflt,
        })

    reuse = find_reuse(items)
    weak_pct = round(100 * weak / total)
    recs = _recommendations(total, ratings, patterns, defaults, reuse, weak_pct)
    return {
        "total":           total,
        "entries":         sorted(entries, key=lambda e: e["entropy"]),
        "ratings":         dict(ratings),
        "patterns":        dict(patterns.most_common()),
        "defaults":        defaults,
        "reuse":           reuse,
        "avg_entropy":     round(ent_sum / total, 1),
        "weak_pct":        weak_pct,
        "recommendations": recs,
    }


def _recommendations(total, ratings, patterns, defaults, reuse, weak_pct) -> list:
    recs = []
    if weak_pct >= 40:
        recs.append(f"{weak_pct}% of cracked keys are weak — passphrase length is the "
                    "single biggest win; recommend 4+ random words (>=16 chars).")
    if defaults:
        recs.append(f"{defaults} network(s) still use a factory-default-shaped key — "
                    "these are in public default-password lists; change them first.")
    if reuse:
        recs.append(f"{len(reuse)} password(s) reused across multiple networks — "
                    "one crack compromises every network sharing the key.")
    if patterns.get("keyboard walk"):
        recs.append(f"{patterns['keyboard walk']} key(s) are keyboard walks "
                    "(qwerty/12345) — trivially brute-forced; avoid.")
    if patterns.get("year suffix"):
        recs.append(f"{patterns['year suffix']} key(s) end in a year — predictable suffix.")
    if not recs:
        recs.append("No systemic weaknesses detected in the cracked sample.")
    return recs


# ── Compact text for AI prompt grounding ─────────────────────────────────────

def summarize(items: list) -> str:
    """A compact, literal-password-free block to inject into an AI prompt."""
    a = analyze(items)
    if not a["total"]:
        return "No cracked passwords to analyze."
    ratings = ", ".join(f"{k}: {v}" for k, v in a["ratings"].items())
    pats    = ", ".join(f"{k} ({v})" for k, v in list(a["patterns"].items())[:8]) or "none"
    lines = [
        f"CRACKED PASSWORD ANALYSIS ({a['total']} keys):",
        f"- Avg entropy: {a['avg_entropy']} bits | weak share: {a['weak_pct']}%",
        f"- Strength mix: {ratings}",
        f"- Patterns: {pats}",
        f"- Factory-default-shaped: {a['defaults']}",
        f"- Reused across networks: {len(a['reuse'])}",
    ]
    return "\n".join(lines)
