"""
ai.py — Local AI engine for radioman using llama.cpp CLI.
Wraps llama-cli as a subprocess so the model is only in memory during inference.
Before every inference, live scan state from the DB is injected into the system prompt
so the model reasons about actual device data, not generic Wi-Fi concepts.

Install AI components first: sudo bash setup/install_ai.sh
"""

import logging
import os
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger("ai")

LLAMA_CLI  = os.environ.get("LLAMA_CLI",  "/opt/radioman/llama/llama-cli")
MODEL_PATH = os.environ.get("RADIOMAN_MODEL", "/opt/radioman/models/granite.gguf")

N_PREDICT = 300
CTX_SIZE  = 1024   # increased to fit system prompt + live context + conversation
THREADS   = 4
TIMEOUT   = 240    # Pi Zero 2W — 4 minutes max

# ── System prompt ─────────────────────────────────────────────────────────────
# Deep radioman + Wi-Fi security domain knowledge baked in.
# Tokens kept tight (~380) so context window fits conversation + output.
SYSTEM = """\
You are the AI assistant inside radioman — a Raspberry Pi Zero 2W Wi-Fi audit device.

HARDWARE: Raspberry Pi Zero 2W (ARM64, 512MB RAM, 4× Cortex-A53). Single wlan0 adapter. \
When scanning, wlan0 enters monitor mode and Wi-Fi SSH drops — management is via USB gadget \
at 10.55.0.1 or wired Ethernet. PiSugar 2 battery (I2C 0x75).

TOOLS ON DEVICE:
- bettercap: passive 802.11 scanning, PMKID capture, EAPOL/4-way handshake capture
- hcxpcapngtool (hcxtools): converts .pcapng captures to hashcat-ready format
- hashcat: dictionary cracking against rockyou.txt (no GPU — CPU only, slow)
- aircrack-ng: handshake verification
- nmap: LAN host discovery
- Captures stored: /opt/radioman/captures/  DB: /opt/radioman/radioman.db

CAPTURE TYPES:
- PMKID (type=pmkid): Grabbed from AP beacon without needing a live client. \
hashcat mode 22000. Faster to get, can crack offline anytime.
- EAPOL (type=eapol): Full WPA 4-way handshake. Requires a client to be actively \
connecting. hashcat mode 22000.

WI-FI SECURITY RISK LEVELS (worst → best):
1. Open (OPN) — no encryption, all traffic exposed, highest risk
2. WEP — cryptographically broken, crackable in minutes with aircrack-ng
3. WPA/WPA2-PSK + weak password — crackable offline with rockyou/dictionary
4. WPA2-PSK + strong password — dictionary-resistant, but PMKID still capturable
5. WPA2-Enterprise (MGT/802.1X) — no shared PSK, much harder to attack
6. WPA3-SAE — resistant to offline PMKID/EAPOL attacks; cannot crack captured material

WPS: Any WPS-enabled AP is vulnerable to Pixie-Dust + PIN brute-force. Always flag.

CHANNEL KNOWLEDGE:
- 2.4GHz ch 1–13: non-overlapping are 1, 6, 11. Dense = congested/residential.
- 5GHz ch 36–177: wider, less congested, shorter range, fewer APs typically.
- Hidden SSID (empty) on unusual channels can indicate evasion or corporate gear.
- Many APs on the same channel suggests a dense urban/apartment environment.

RSSI GUIDE: ≥-50 dBm excellent (nearby), -51–-70 good, -71–-85 fair, <-85 weak.

VENDOR INTEL: First 3 octets of BSSID identify hardware maker via OUI. ISP-provided \
routers (TP-Link, ASUS, Netgear, Arris, Sagemcom) often ship with predictable SSIDs \
and default password patterns (e.g. router model + serial suffix). Flag these.

PASSWORD PATTERNS TO FLAG: keyboard walks (qwerty, 12345678), years (2020–2024), \
common words + digits, all-lowercase dictionary words, short passwords (<10 chars), \
repeated chars, router default patterns (vendor+digits).

RADIOMAN WORKFLOW: Boot → bettercap scans passively → APs/clients logged to SQLite → \
PMKID/EAPOL auto-captured → hashcat cracks with rockyou.txt → results pushed to XPLT.

RESPONSE STYLE: Be concise. Lead with the most critical findings. Use bullet points. \
When uncertain, err on the side of flagging. Never suggest illegal use.\
"""


def _binary_ok() -> bool:
    return os.path.isfile(LLAMA_CLI) and os.access(LLAMA_CLI, os.X_OK)


def _model_ok() -> bool:
    return os.path.isfile(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 1_000_000


# ── Live context builder ──────────────────────────────────────────────────────

def _live_context(db_path: Optional[str]) -> str:
    """Query the radioman DB and return a compact status block for prompt injection."""
    if not db_path:
        return ""
    try:
        import db as _db
        stats    = _db.get_stats(db_path)
        sec      = _db.get_security_stats(db_path)   # {security: count}
        ch       = _db.get_channel_stats(db_path)    # {channel: count}
        events   = _db.get_events(db_path, limit=5)  # recent log lines

        nets     = stats.get("networks", 0)
        clients  = stats.get("clients",  0)
        caps     = stats.get("captures", 0)
        cracked  = stats.get("cracked",  0)

        # Security mix (top 4 by count)
        sec_str = ", ".join(
            f"{k}: {v}"
            for k, v in sorted(sec.items(), key=lambda x: x[1], reverse=True)[:4]
        ) if sec else "none"

        # Busiest channels (top 4 by count)
        ch_str = ", ".join(
            f"ch{k} ({v})"
            for k, v in sorted(ch.items(), key=lambda x: x[1], reverse=True)[:4]
        ) if ch else "none"

        # Recent events (trim to 80 chars each)
        ev_lines = "\n".join(
            f"  [{e.get('level','?')}] {str(e.get('message',''))[:80]}"
            for e in (events or [])[:5]
        ) or "  none"

        return (
            f"\nRADIOMAN LIVE STATE:\n"
            f"- Networks: {nets} | Clients: {clients} | Captures: {caps} | Cracked: {cracked}\n"
            f"- Security mix: {sec_str}\n"
            f"- Active channels: {ch_str}\n"
            f"- Recent events:\n{ev_lines}"
        )
    except Exception as e:
        log.debug("Context build failed: %s", e)
        return ""


class AIEngine:
    def __init__(self, db_path: Optional[str] = None):
        self._lock    = threading.Lock()
        self._busy    = False
        self._db_path = db_path
        ready = _binary_ok() and _model_ok()
        if ready:
            sz = os.path.getsize(MODEL_PATH) // (1024 * 1024)
            log.info("AI ready — model=%s (%dMB)", os.path.basename(MODEL_PATH), sz)
        else:
            if not _binary_ok():
                log.warning("AI: llama-cli not found at %s — run setup/install_ai.sh", LLAMA_CLI)
            if not _model_ok():
                log.warning("AI: model not found at %s — run setup/install_ai.sh", MODEL_PATH)

    def status(self) -> dict:
        binary = _binary_ok()
        model  = _model_ok()
        sz = os.path.getsize(MODEL_PATH) // (1024 * 1024) if model else 0
        return {
            "ready":       binary and model,
            "busy":        self._busy,
            "binary":      binary,
            "model":       os.path.basename(MODEL_PATH) if model else None,
            "model_mb":    sz,
            "binary_path": LLAMA_CLI,
            "model_path":  MODEL_PATH,
        }

    def _build_prompt(self, messages: list, extra_system: str = "") -> str:
        """Format messages using IBM Granite chat template with live context injected."""
        system = SYSTEM + extra_system
        parts  = [
            f"<|system|>\n{system}",
            "<|assistant|>\nUnderstood. I have your current scan data and am ready to help.",
        ]
        for m in messages:
            role    = m.get("role", "user")
            content = str(m.get("content", "")).strip()
            if role == "user":
                parts.append(f"<|user|>\n{content}")
                parts.append("<|assistant|>")
            elif role == "assistant":
                if parts and parts[-1] == "<|assistant|>":
                    parts[-1] = f"<|assistant|>\n{content}"
                else:
                    parts.append(f"<|assistant|>\n{content}")
        # Ensure open assistant turn at the end
        if not parts[-1].startswith("<|assistant|>") or "\n" in parts[-1][14:]:
            parts.append("<|assistant|>")
        return "\n".join(parts)

    def _infer(self, prompt: str) -> Optional[str]:
        cmd = [
            LLAMA_CLI,
            "--model",          MODEL_PATH,
            "--threads",        str(THREADS),
            "--ctx-size",       str(CTX_SIZE),
            "--n-predict",      str(N_PREDICT),
            "--temp",           "0.7",
            "--top-p",          "0.9",
            "--repeat-penalty", "1.1",
            "--no-display-prompt",
            "--log-disable",
            "--prompt",         prompt,
        ]
        log.debug("llama-cli: ctx=%d n_predict=%d threads=%d", CTX_SIZE, N_PREDICT, THREADS)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
            )
            out = result.stdout
            if out.startswith(prompt):
                out = out[len(prompt):]
            out = out.strip()
            for stop in ["<|user|>", "<|system|>", "<|endoftext|>"]:
                if stop in out:
                    out = out[:out.index(stop)].strip()
            if result.returncode != 0 and not out:
                log.error("llama-cli rc=%d: %s", result.returncode, result.stderr[:300])
                return None
            return out or None
        except subprocess.TimeoutExpired:
            log.warning("AI inference timed out after %ds", TIMEOUT)
            return None
        except FileNotFoundError:
            log.error("llama-cli not found at %s", LLAMA_CLI)
            return None
        except Exception as e:
            log.error("AI inference error: %s", e)
            return None

    def _run(self, messages: list, extra_system: str = "") -> dict:
        prompt  = self._build_prompt(messages, extra_system)
        log.debug("AI prompt: %d turns, %d chars (ctx budget: %d tokens)",
                  len(messages), len(prompt), CTX_SIZE)
        t0      = time.time()
        resp    = self._infer(prompt)
        elapsed = round(time.time() - t0, 1)
        if resp is None:
            log.warning("AI inference returned nothing (%.1fs)", elapsed)
            return {"error": "Inference failed or timed out", "elapsed": elapsed}
        log.info("AI: %.1fs, %d chars output", elapsed, len(resp))
        return {"response": resp, "elapsed": elapsed}

    def chat(self, messages: list) -> dict:
        if not (_binary_ok() and _model_ok()):
            return {"error": "AI model not installed — run: sudo bash setup/install_ai.sh"}
        if not self._lock.acquire(blocking=False):
            return {"error": "AI is busy — please wait", "busy": True}
        self._busy = True
        try:
            context = _live_context(self._db_path)
            return self._run(messages[-8:], extra_system=context)
        finally:
            self._busy = False
            self._lock.release()

    def analyze_networks(self, networks: list) -> dict:
        if not networks:
            return {"error": "No networks to analyze"}
        top   = sorted(networks, key=lambda n: n.get("rssi", -100), reverse=True)[:20]
        lines = []
        for n in top:
            ssid   = (n.get("ssid") or "(hidden)")[:28]
            sec    = n.get("security", "?")
            ch     = n.get("channel", 0)
            rssi   = n.get("rssi", 0)
            vendor = (n.get("vendor") or "")[:20]
            wps    = " [WPS]" if n.get("wps") else ""
            lines.append(f"{ssid} | {sec}{wps} | ch{ch} | {rssi}dBm | {vendor}")
        summary = "\n".join(lines)
        messages = [{
            "role": "user",
            "content": (
                f"Analyze these {len(networks)} scanned Wi-Fi networks "
                f"(top {len(lines)} by signal strength):\n\n{summary}\n\n"
                "Flag: open networks, WEP, WPS-enabled, weak vendor defaults, "
                "unusual channel use, hidden SSIDs. Rank by risk. Be concise."
            ),
        }]
        return self.chat(messages)

    def analyze_passwords(self, cracked: list) -> dict:
        if not cracked:
            return {"error": "No cracked passwords to analyze"}
        sample = cracked[:12]
        items  = ", ".join(repr(p) for p in sample)
        messages = [{
            "role": "user",
            "content": (
                f"Analyze these {len(cracked)} cracked Wi-Fi passwords for patterns: {items}\n\n"
                "Identify: password pattern types (keyboard walk, year, dictionary word, "
                "default router pattern, etc.), estimated crack time categories, "
                "and 2-3 concrete recommendations for users in this area. "
                "Do not repeat passwords verbatim in your response."
            ),
        }]
        return self.chat(messages)
