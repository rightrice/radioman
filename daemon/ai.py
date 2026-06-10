"""
ai.py — Local AI engine for radioman using llama.cpp CLI.
Wraps llama-cli as a subprocess so the model is only in memory during inference.
Before every inference, live scan state from the DB is injected into the system prompt
so the model reasons about actual device data, not generic Wi-Fi concepts.

Install AI components first: sudo bash setup/install_ai.sh
"""

import logging
import os
import pty
import re
import select
import subprocess
import threading
import time
from typing import Optional

import hwinfo

log = logging.getLogger("ai")

# Defaults; the binary/model can be overridden by env or the [ai] config section.
DEFAULT_LLAMA_CLI = os.environ.get("LLAMA_CLI",  "/opt/radioman/llama/llama-cli")
DEFAULT_MODEL     = os.environ.get("RADIOMAN_MODEL", "/opt/radioman/models/granite-4.0-350m-Q4_K_M.gguf")

BATCH = 128   # prompt-processing batch (smaller = less compute-buffer RAM)
# Per-host runtime params (threads / ctx_size / n_predict / timeout) are chosen
# from hwinfo.recommended_ai() and can be overridden in [ai] — see AIEngine.

# AI HAT+ 2 (Hailo-10H) backend: a local Ollama-compatible HTTP API (loopback,
# no internet). US-origin model only — Llama 3.2 1B (Meta). Both overridable in [ai].
DEFAULT_HAILO_URL   = os.environ.get("HAILO_URL", "http://127.0.0.1:11434")
DEFAULT_HAILO_MODEL = os.environ.get("HAILO_MODEL", "llama3.2:1b")

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


def _binary_ok(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _model_ok(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 1_000_000


def _safe_close(fd):
    try:
        os.close(fd)
    except Exception:
        pass


def _diagnose_stderr(stderr: str) -> str:
    """Turn llama-cli's stderr into a human-readable failure reason for the UI."""
    if not stderr.strip():
        return ("Inference produced no output and llama-cli printed nothing. "
                "The binary may be the wrong architecture — verify with: "
                "file /opt/radioman/llama/llama-cli")
    low = stderr.lower()
    if "unknown argument" in low or "invalid argument" in low or "error: " in low and "usage:" in low:
        # Pull the offending line so we know which flag this build rejects.
        for line in stderr.splitlines():
            if "argument" in line.lower():
                return (f"llama-cli rejected a flag: {line.strip()[:160]} — "
                        "this build uses a different CLI version than expected.")
        return "llama-cli rejected a command-line flag (CLI version mismatch)."
    if "failed to load model" in low or "error loading model" in low or "no such file" in low:
        return ("llama-cli could not load the model. Re-download it: "
                "sudo bash setup/install_ai.sh")
    if "exec format error" in low or "cannot execute" in low:
        return ("llama-cli is the wrong CPU architecture (not arm64). "
                "Rebuild on a Linux/Mac host: bash scripts/build_llama_ubuntu.sh radioman.local")
    if "out of memory" in low or "cannot allocate" in low or "killed" in low:
        return ("Out of memory during inference. Confirm swap is active (free -h) "
                "and that no scan/crack is running simultaneously.")
    # Fallback: last non-empty stderr line.
    tail = [l.strip() for l in stderr.splitlines() if l.strip()]
    return f"Inference failed: {tail[-1][:200]}" if tail else "Inference failed (unknown error)."


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
    def __init__(self, db_path: Optional[str] = None, config: Optional[dict] = None):
        self._lock    = threading.Lock()
        self._busy    = False
        self._db_path = db_path

        cfg = config or {}
        self._hw  = hwinfo.summary()
        rec       = hwinfo.recommended_ai()

        # Binary + model: [ai] config overrides env/defaults.
        self._llama = cfg.get("llama_cli") or DEFAULT_LLAMA_CLI
        self._model = cfg.get("model") or DEFAULT_MODEL
        # Runtime params: explicit [ai] override → hardware recommendation.
        def _pick(key):
            return int(cfg[key]) if str(cfg.get(key, "")).strip() else int(rec[key])
        self._threads   = _pick("threads")
        self._ctx       = _pick("ctx_size")
        self._n_predict = _pick("n_predict")
        self._timeout   = _pick("timeout")
        # ~3.2 chars/token input budget, leaving room for the generation.
        self._max_prompt_chars = (self._ctx - self._n_predict) * 3

        # ── Backend selection ────────────────────────────────────────────────
        # 'hailo' offloads the LLM to the AI HAT+ 2 (Hailo-10H) via its local
        # Ollama-compatible HTTP API (loopback only — no internet). 'llama' is
        # the CPU llama.cpp path. 'auto' uses the HAT if it's present + reachable,
        # else falls back to the CPU. US-origin models only: Llama 3.2 (Meta) on
        # the HAT, IBM Granite on the CPU.
        self._backend_cfg = (cfg.get("backend") or "auto").strip().lower()
        self._hailo_url   = (cfg.get("hailo_url") or DEFAULT_HAILO_URL).rstrip("/")
        self._hailo_model = cfg.get("hailo_model") or DEFAULT_HAILO_MODEL
        self._backend     = self._resolve_backend()

        log.info("AI host: %s | %d cores, %dMB RAM%s",
                 self._hw["board"], self._hw["cores"], self._hw["ram_mb"],
                 " | AI HAT+ 2 (Hailo-10H) present" if self._hw["hailo"]["present"] else "")

        if self._backend == "hailo":
            log.info("AI backend: Hailo-10H NPU — model=%s via %s",
                     self._hailo_model, self._hailo_url)
        else:
            log.info("AI backend: CPU llama.cpp — threads=%d ctx=%d n_predict=%d timeout=%ds",
                     self._threads, self._ctx, self._n_predict, self._timeout)
            if _binary_ok(self._llama) and _model_ok(self._model):
                sz = os.path.getsize(self._model) // (1024 * 1024)
                log.info("AI ready — model=%s (%dMB)", os.path.basename(self._model), sz)
            else:
                if not _binary_ok(self._llama):
                    log.warning("AI: llama-cli not found at %s — run setup/install_ai.sh", self._llama)
                if not _model_ok(self._model):
                    log.warning("AI: model not found at %s — run setup/install_ai.sh", self._model)

    def _hailo_reachable(self) -> bool:
        """True if the Hailo-Ollama endpoint answers (loopback HTTP, no internet)."""
        try:
            import requests
            requests.get(f"{self._hailo_url}/api/tags", timeout=2).raise_for_status()
            return True
        except Exception:
            return False

    def _resolve_backend(self) -> str:
        if self._backend_cfg == "hailo":
            return "hailo"
        if self._backend_cfg == "llama":
            return "llama"
        # auto: prefer the HAT only if the device is present AND its server answers.
        if self._hw["hailo"]["present"] and self._hailo_reachable():
            return "hailo"
        return "llama"

    def status(self) -> dict:
        if self._backend == "hailo":
            reachable = self._hailo_reachable()
            return {
                "ready":       reachable,
                "busy":        self._busy,
                "backend":     "hailo",
                "accelerator": "Hailo-10H NPU (AI HAT+ 2)",
                "model":       self._hailo_model,
                "model_mb":    0,
                "hailo_url":   self._hailo_url,
                "hailo_up":    reachable,
                "timeout":     self._timeout,
                "hardware":    self._hw,
            }
        binary = _binary_ok(self._llama)
        model  = _model_ok(self._model)
        sz = os.path.getsize(self._model) // (1024 * 1024) if model else 0
        return {
            "ready":       binary and model,
            "busy":        self._busy,
            "backend":     "llama",
            "accelerator": "CPU (llama.cpp)",
            "binary":      binary,
            "model":       os.path.basename(self._model) if model else None,
            "model_mb":    sz,
            "binary_path": self._llama,
            "model_path":  self._model,
            "threads":     self._threads,
            "ctx_size":    self._ctx,
            "n_predict":   self._n_predict,
            "timeout":     self._timeout,
            "hardware":    self._hw,
        }

    def _build_prompt(self, messages: list, extra_system: str = "") -> str:
        """Format messages using IBM Granite chat template with live context injected.

        Keeps the system block + live context intact and drops the oldest
        conversation turns if the assembled prompt would exceed the context
        window (which would make llama-cli error or return nothing).
        """
        system = SYSTEM + extra_system

        def assemble(msgs: list) -> str:
            parts = [
                f"<|system|>\n{system}",
                "<|assistant|>\nUnderstood. I have your current scan data and am ready to help.",
            ]
            for m in msgs:
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
            if not parts[-1].startswith("<|assistant|>") or "\n" in parts[-1][14:]:
                parts.append("<|assistant|>")
            return "\n".join(parts)

        msgs   = list(messages)
        prompt = assemble(msgs)
        # Drop oldest turns until the prompt fits the input budget (always keep
        # the most recent user message).
        while len(prompt) > self._max_prompt_chars and len(msgs) > 1:
            msgs.pop(0)
            prompt = assemble(msgs)
        if len(prompt) > self._max_prompt_chars:
            log.warning("AI prompt still %d chars after trimming (budget %d)",
                        len(prompt), self._max_prompt_chars)
        return prompt

    def _infer(self, prompt: str) -> dict:
        """Run llama-cli once and return {"text": str} or {"error": str}.

        stdout (the generated answer) is read over a PTY because this llama-cli
        build block-buffers stdout when it's a plain pipe. stderr (model-load
        logs, perf stats, and crucially any flag/load errors) is captured on a
        SEPARATE pipe so a real failure reason can be surfaced instead of a
        generic "inference failed".
        """
        cmd = [
            self._llama,
            "--model",          self._model,
            "--threads",        str(self._threads),
            "--ctx-size",       str(self._ctx),
            "--batch-size",     str(BATCH),
            "--n-predict",      str(self._n_predict),
            "--temp",           "0.7",
            "--top-p",          "0.9",
            "--repeat-penalty", "1.1",
            "-no-cnv",            # one-shot completion — NOT interactive chat mode
            "--no-display-prompt",
            "--prompt",         prompt,
        ]
        log.debug("llama-cli: ctx=%d batch=%d n_predict=%d threads=%d",
                  self._ctx, BATCH, self._n_predict, self._threads)

        master_fd, slave_fd = pty.openpty()
        stderr_r, stderr_w = os.pipe()
        # Non-blocking reads on both ends so we never wedge.
        for fd in (master_fd, stderr_r):
            os.set_blocking(fd, False)
        try:
            proc = subprocess.Popen(
                cmd, stdout=slave_fd, stderr=stderr_w,
                stdin=subprocess.DEVNULL, close_fds=True,
            )
        except FileNotFoundError:
            for fd in (master_fd, slave_fd, stderr_r, stderr_w): _safe_close(fd)
            return {"error": f"llama-cli not found at {self._llama} — run setup/install_ai.sh"}
        except Exception as e:
            for fd in (master_fd, slave_fd, stderr_r, stderr_w): _safe_close(fd)
            return {"error": f"Could not start llama-cli: {e}"}
        os.close(slave_fd)   # parent only reads the master side
        os.close(stderr_w)   # parent only reads stderr_r

        collected, buf, done = [], "", False
        stderr_buf = ""
        deadline = time.time() + self._timeout
        fds = [master_fd, stderr_r]
        try:
            while time.time() < deadline and not done:
                try:
                    ready, _, _ = select.select(fds, [], [], 1.0)
                except (OSError, ValueError):
                    break
                if not ready:
                    if proc.poll() is not None:
                        break                              # process gone
                    continue
                for fd in ready:
                    try:
                        data = os.read(fd, 4096)
                    except (OSError, BlockingIOError):
                        continue
                    if fd == stderr_r:
                        stderr_buf += data.decode("utf-8", "replace")
                        if len(stderr_buf) > 8000:
                            stderr_buf = stderr_buf[-8000:]
                        continue
                    # stdout (PTY)
                    if not data:
                        done = True                        # EOF on stdout
                        break
                    buf += data.decode("utf-8", "replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        # Stop early on the end-of-generation perf line if present;
                        # otherwise we still stop on process exit / EOF above.
                        if ("Generation:" in line and "t/s" in line) or \
                           "llama_perf_context_print" in line:
                            done = True
                            break
                        collected.append(line + "\n")
        finally:
            try: proc.kill()
            except Exception: pass
            try: proc.wait(timeout=5)
            except Exception: pass
            # Drain any remaining stderr (non-blocking).
            for _ in range(64):
                try:
                    d = os.read(stderr_r, 4096)
                except (OSError, BlockingIOError):
                    break
                if not d:
                    break
                stderr_buf += d.decode("utf-8", "replace")
            _safe_close(master_fd); _safe_close(stderr_r)

        out = "".join(collected) + buf
        if "<|assistant|>" in out:
            out = out.rsplit("<|assistant|>", 1)[-1]
        out = out.strip()
        for stop in ["<|user|>", "<|system|>", "<|endoftext|>", "<|end_of_text|>"]:
            if stop in out:
                out = out[:out.index(stop)].strip()
        out = re.sub(r"\n?>\s*$", "", out).strip()

        low = out.lower()
        is_banner = "available commands" in low or low.startswith("loading model")
        if out and not is_banner:
            return {"text": out}

        # No usable answer — surface the real reason from stderr.
        diag = _diagnose_stderr(stderr_buf)
        log.warning("AI: no response. stderr tail: %s", stderr_buf[-400:].replace("\n", " "))
        return {"error": diag}

    def _run(self, messages: list, extra_system: str = "") -> dict:
        if self._backend == "hailo":
            return self._run_hailo(messages, extra_system)
        prompt  = self._build_prompt(messages, extra_system)
        log.debug("AI prompt: %d turns, %d chars (ctx budget: %d tokens)",
                  len(messages), len(prompt), self._ctx)
        t0      = time.time()
        result  = self._infer(prompt)
        elapsed = round(time.time() - t0, 1)
        if "text" not in result:
            err = result.get("error", "Inference failed or timed out")
            log.warning("AI inference failed (%.1fs): %s", elapsed, err)
            return {"error": err, "elapsed": elapsed}
        text = result["text"]
        log.info("AI: %.1fs, %d chars output", elapsed, len(text))
        return {"response": text, "elapsed": elapsed}

    def _run_hailo(self, messages: list, extra_system: str = "") -> dict:
        """Run inference on the AI HAT+ 2 (Hailo-10H) via its local Ollama-compatible
        HTTP API. The server applies the model's chat template, so we send
        structured messages (system + live context, then the conversation) — no
        Granite-template building needed. Loopback only; no internet."""
        import requests
        sys_msg = SYSTEM + (extra_system or "")
        payload = {
            "model":    self._hailo_model,
            "messages": [{"role": "system", "content": sys_msg}] +
                        [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                         for m in messages],
            "stream":   False,
            "options":  {"num_predict": self._n_predict, "temperature": 0.7},
        }
        t0 = time.time()
        try:
            r = requests.post(f"{self._hailo_url}/api/chat",
                              json=payload, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.ConnectionError:
            return {"error": f"Hailo NPU server unreachable at {self._hailo_url} — "
                             "is hailo-ollama running? (sudo systemctl status hailo-ollama)"}
        except requests.exceptions.Timeout:
            return {"error": f"Hailo inference timed out after {self._timeout}s"}
        except Exception as e:
            return {"error": f"Hailo inference error: {e}"}
        elapsed = round(time.time() - t0, 1)
        text = (data.get("message", {}) or {}).get("content", "").strip()
        if not text:
            return {"error": "Hailo NPU returned an empty response", "elapsed": elapsed}
        log.info("AI (Hailo-10H): %.1fs, %d chars output", elapsed, len(text))
        return {"response": text, "elapsed": elapsed}

    def _available(self) -> tuple:
        """(ok, error_message) for the active backend."""
        if self._backend == "hailo":
            if self._hailo_reachable():
                return True, ""
            return False, (f"Hailo NPU server not reachable at {self._hailo_url} — "
                           "run setup/install_hailo.sh and ensure hailo-ollama is running")
        if _binary_ok(self._llama) and _model_ok(self._model):
            return True, ""
        return False, "AI model not installed — run: sudo bash setup/install_ai.sh"

    def chat(self, messages: list) -> dict:
        ok, err = self._available()
        if not ok:
            return {"error": err}
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

    def analyze_passwords(self, items: list) -> dict:
        """items: cracked captures (dicts with ssid/bssid/password). The model is
        grounded on a deterministic, literal-password-free analysis from
        passwords.py rather than the raw keys — better signal, no leakage."""
        items = [it for it in (items or []) if it.get("password")]
        if not items:
            return {"error": "No cracked passwords to analyze"}
        try:
            import passwords as _pw
            analysis = _pw.summarize(items)
        except Exception as e:
            log.debug("password summarize failed: %s", e)
            analysis = f"{len(items)} cracked passwords (analysis unavailable)."
        messages = [{
            "role": "user",
            "content": (
                "Here is a deterministic analysis of the Wi-Fi passwords cracked in "
                f"this area:\n\n{analysis}\n\n"
                "Explain what these password habits reveal about the neighborhood's "
                "security posture, which weaknesses to prioritize, and give 2-3 specific, "
                "actionable recommendations. Do not invent passwords or data not shown above."
            ),
        }]
        return self.chat(messages)
