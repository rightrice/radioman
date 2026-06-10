"""
hwinfo.py — board / CPU / RAM detection and AI HAT+ (Hailo) presence.

Pure, no dependencies. Lets radioman run on both the Pi Zero 2W (512MB, A53)
and the Pi 5 (up to 16GB, A76) and scale itself to the host: the AI threads /
context / model and the installer's swap decision all key off this.

NOTE on the AI HAT+ generations:
  * AI HAT+   (Hailo-8/8L) — vision/CNN NPU, NOT an LLM accelerator.
  * AI HAT+ 2 (Hailo-10H)  — generative-AI NPU that DOES run LLMs on-device via
    a local Ollama-compatible server (hailo-ollama). radioman's AI uses it when
    `[ai] backend = hailo|auto` and the server is reachable (see daemon/ai.py).
We detect presence + architecture here; ai.py decides whether to use it.
"""

import os
import re
import shutil
import subprocess


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def board_model() -> str:
    """e.g. 'Raspberry Pi 5 Model B Rev 1.0' / 'Raspberry Pi Zero 2 W Rev 1.0'."""
    return _read("/proc/device-tree/model").replace("\x00", "").strip() or "unknown"


def cpu_cores() -> int:
    return os.cpu_count() or 1


def ram_mb() -> int:
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo"))
    return int(m.group(1)) // 1024 if m else 0


def is_pi5() -> bool:
    return "raspberry pi 5" in board_model().lower()


def is_low_memory() -> bool:
    """True for the Zero 2W class (<= ~1GB) — drives the installer's swap setup."""
    return 0 < ram_mb() <= 1280


def hailo() -> dict:
    """Detect an AI HAT+ / Hailo NPU and its architecture. The Hailo-10H
    (AI HAT+ 2) can run LLMs on-device; the Hailo-8/8L cannot."""
    dev = os.path.exists("/dev/hailo0")
    cli = shutil.which("hailortcli") is not None
    arch = ""
    if cli:
        try:
            out = subprocess.run(
                ["hailortcli", "fw-control", "identify"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            m = re.search(r"Device Architecture:\s*(\S+)", out) or \
                re.search(r"Board Name:\s*(.+)", out)
            if m:
                arch = m.group(1).strip()
        except Exception:
            pass
    is_10h = "10" in arch  # HAILO10H → LLM-capable
    return {
        "present":  dev or cli,
        "device":   dev,            # /dev/hailo0 (driver loaded)
        "runtime":  cli,            # hailortcli on PATH (HailoRT installed)
        "arch":     arch,
        "llm_capable": is_10h,
        "note": ("Hailo-10H — on-device LLM accelerator (AI HAT+ 2)" if is_10h
                 else "Hailo NPU detected" if (dev or cli)
                 else "no Hailo NPU detected"),
    }


def recommended_ai() -> dict:
    """Default llama.cpp params for the detected host. Overridable via [ai] config."""
    cores, ram = cpu_cores(), ram_mb()
    if ram >= 4096 and cores >= 4:
        # Pi 5 class — lean into the A76 + RAM headroom.
        return {"threads": min(cores, 8), "ctx_size": 4096,
                "n_predict": 512, "timeout": 120}
    if ram >= 1536:
        # Pi 3/4-ish middle ground.
        return {"threads": min(cores, 4), "ctx_size": 2048,
                "n_predict": 384, "timeout": 200}
    # Zero 2W class — conservative; slow under memory pressure.
    return {"threads": min(cores, 4), "ctx_size": 2048,
            "n_predict": 256, "timeout": 300}


def summary() -> dict:
    return {
        "board":   board_model(),
        "cores":   cpu_cores(),
        "ram_mb":  ram_mb(),
        "is_pi5":  is_pi5(),
        "hailo":   hailo(),
    }
