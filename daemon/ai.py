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
MODEL_PATH = os.environ.get("RADIOMAN_MODEL", "/opt/radioman/models/granite-4.0-350m-Q4_K_M.gguf")

N_PREDICT = 256
CTX_SIZE  = 1024   # system prompt + live context + conversation
THREADS   = 4
BATCH     = 128    # smaller prompt-processing batch = less compute-buffer RAM
TIMEOUT   = 300    # Pi Zero 2W — slow under memory pressure

# ── System prompt ─────────────────────────────────────────────────────────────
# Trimmed to ~200 tokens so the 1024-token window leaves room for live data +
# conversation + output on the memory-constrained Pi Zero 2W.
SYSTEM = """\
You are the AI assistant inside radioman, a Raspberry Pi Wi-Fi audit device \
(bettercap scanning, PMKID/EAPOL capture, hashcat + rockyou cracking, nmap LAN discovery).

CAPTURES: PMKID (no client needed, hashcat mode 22000) and EAPOL (full WPA 4-way \
handshake, needs a connecting client, mode 22000).

RISK (worst→best): Open (no encryption) > WEP (broken, cracks in minutes) > WPA/WPA2-PSK \
with a weak password (offline dictionary-crackable) > WPA2-PSK strong (PMKID still \
capturable) > WPA2-Enterprise/802.1X > WPA3-SAE (resists offline cracking). Always flag WPS \
(Pixie-Dust / PIN brute-force).

CHANNELS: 2.4GHz non-overlapping = 1/6/11; many APs on one channel = congestion. 5GHz (36+) \
less crowded. Hidden SSID can mean evasion or corporate gear.
RSSI: ≥-50 excellent, -51..-70 good, -71..-85 fair, <-85 weak.

VENDORS: ISP routers (TP-Link, ASUS, Netgear, Arris, Sagemcom) often ship default SSID/password \
patterns — flag them. Weak passwords: keyboard walks, years, dictionary words, <10 chars, vendor+digits.

STYLE: Concise. Lead with the most critical findings. Bullet points. When uncertain, flag it. \
Never suggest illegal use.\
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
        sec      = _db.get_security_stats(db_path)
        ch       = _db.get_channel_stats(db_path)
        events   = _db.get_events(db_path, limit=5)
        captures = _db.get_captures(db_path)

        nets    = stats.get("networks", 0)
        clients = stats.get("clients",  0)
        caps    = stats.get("captures", 0)
        cracked = stats.get("cracked",  0)

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

        # Capture summary — which networks have captures and which are cracked
        cap_lines = []
        for c in captures[:8]:
            ssid   = (c.get("ssid") or c.get("bssid") or "?")[:24]
            ctype  = c.get("type", "?")
            status = f"CRACKED: {c['password'][:3]}***" if c.get("cracked") and c.get("password") else "pending"
            cap_lines.append(f"  {ssid} [{ctype}] {status}")
        cap_str = "\n".join(cap_lines) or "  none"

        # Recent events
        ev_lines = "\n".join(
            f"  [{e.get('level','?')}] {str(e.get('message',''))[:80]}"
            for e in (events or [])[:5]
        ) or "  none"

        return (
            f"\nRADIOMAN LIVE STATE:\n"
            f"- Networks seen: {nets} | Clients: {clients} | Captures: {caps} | Cracked: {cracked}\n"
            f"- Security mix: {sec_str}\n"
            f"- Busiest channels: {ch_str}\n"
            f"- Captures:\n{cap_str}\n"
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
            "--batch-size",     str(BATCH),
            "--n-predict",      str(N_PREDICT),
            "--temp",           "0.7",
            "--top-p",          "0.9",
            "--repeat-penalty", "1.1",
            "--no-display-prompt",
            "--log-disable",
            "--prompt",         prompt,
        ]
        log.debug("llama-cli: ctx=%d batch=%d n_predict=%d threads=%d",
                  CTX_SIZE, BATCH, N_PREDICT, THREADS)
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

    def analyze_networks(self, networks: list, captures: Optional[list] = None) -> dict:
        if not networks:
            return {"error": "No networks to analyze"}

        # Build a set of BSSIDs that have captures, and which are cracked
        captured_bssids = {}
        if captures:
            for c in captures:
                bssid = (c.get("bssid") or "").upper()
                if bssid:
                    captured_bssids[bssid] = "cracked" if c.get("cracked") else "captured"

        top   = sorted(networks, key=lambda n: n.get("rssi", -100), reverse=True)[:12]
        lines = []
        for n in top:
            ssid     = (n.get("ssid") or "(hidden)")[:24]
            sec      = n.get("security", "?")
            ch       = n.get("channel", 0)
            rssi     = n.get("rssi", 0)
            vendor   = (n.get("vendor") or "unknown")[:18]
            clients  = n.get("clients", 0)
            wps      = "[WPS]" if n.get("wps") else ""
            bssid    = (n.get("bssid") or "").upper()
            cap_flag = f"[{captured_bssids[bssid]}]" if bssid in captured_bssids else ""
            lines.append(
                f"{ssid} | {sec} {wps}{cap_flag} | ch{ch} | {rssi}dBm"
                f" | {vendor} | {clients} client{'s' if clients != 1 else ''}"
            )
        summary = "\n".join(lines)

        messages = [{
            "role": "user",
            "content": (
                f"Analyze these {len(networks)} Wi-Fi networks "
                f"(top {len(lines)} shown, sorted by signal). "
                f"[captured] = handshake/PMKID already taken. [cracked] = password known.\n\n"
                f"{summary}\n\n"
                "Flag in order of risk: open networks, WEP, WPS, weak vendor defaults, "
                "high client counts (juicy targets), hidden SSIDs, channel congestion. "
                "Note which already have captures. Recommend next actions."
            ),
        }]
        return self.chat(messages)

    def analyze_passwords(self, cracked: list) -> dict:
        if not cracked:
            return {"error": "No cracked passwords to analyze"}
        sample = cracked[:12]
        items  = ", ".join(repr(p) for p in sample)
        total  = len(cracked)
        messages = [{
            "role": "user",
            "content": (
                f"{total} Wi-Fi password{'s' if total != 1 else ''} cracked in this area. "
                f"Sample ({len(sample)}): {items}\n\n"
                "For each pattern type found (keyboard walk, year suffix, dictionary word, "
                "name+digits, router default, etc.) estimate what % of the sample it represents. "
                "Give 2-3 specific security recommendations relevant to this neighborhood's "
                "password habits. Do not repeat passwords verbatim."
            ),
        }]
        return self.chat(messages)
