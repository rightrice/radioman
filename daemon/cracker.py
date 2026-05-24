import logging
import os
import queue
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger("cracker")

_RESULT_MARKER = "KEY FOUND!"


class CrackJob:
    def __init__(self, capture_id: int, filepath: str,
                 bssid: str, ssid: str):
        self.capture_id = capture_id
        self.filepath   = filepath
        self.bssid      = bssid
        self.ssid       = ssid


class CrackQueue:
    def __init__(self, config: dict, on_cracked: Callable):
        self._wordlist   = config.get("wordlist", "/opt/radioman/wordlists/rockyou.txt")
        self._aircrack  = config.get("aircrack_bin", "aircrack-ng")
        self._max_jobs  = int(config.get("max_jobs", 1))
        self._on_cracked = on_cracked
        self._q          = queue.Queue()
        self._running    = False
        self._active     = 0
        self._lock       = threading.Lock()
        self._seen        = set()

    def enqueue(self, job: CrackJob):
        if job.filepath in self._seen:
            return
        self._seen.add(job.filepath)
        self._q.put(job)
        log.info("Queued crack job: %s (%s)", job.ssid or job.bssid, job.filepath)

    def _crack(self, job: CrackJob):
        if not os.path.exists(job.filepath):
            log.warning("Capture file missing: %s", job.filepath)
            return
        if not os.path.exists(self._wordlist):
            log.warning("Wordlist missing: %s", self._wordlist)
            return

        log.info("Cracking %s with %s...", job.ssid or job.bssid, self._wordlist)
        cmd = [
            self._aircrack, "-q",
            "-w", self._wordlist,
            "-b", job.bssid,
            job.filepath,
        ] if job.bssid else [
            self._aircrack, "-q",
            "-w", self._wordlist,
            job.filepath,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            output = result.stdout + result.stderr
            if _RESULT_MARKER in output:
                password = _extract_password(output)
                log.info("CRACKED %s → %s", job.ssid or job.bssid, password)
                self._on_cracked(job.capture_id, password)
            else:
                log.info("Not cracked: %s", job.ssid or job.bssid)
        except subprocess.TimeoutExpired:
            log.warning("Crack timed out for %s", job.filepath)
        except FileNotFoundError:
            log.error("aircrack-ng not found — install with: sudo apt install aircrack-ng")
        finally:
            with self._lock:
                self._active -= 1

    def _worker(self):
        while self._running:
            try:
                job = self._q.get(timeout=2)
            except queue.Empty:
                continue

            with self._lock:
                self._active += 1

            t = threading.Thread(
                target=self._crack, args=(job,),
                daemon=True, name=f"crack-{job.capture_id}"
            )
            t.start()

            while True:
                with self._lock:
                    if self._active < self._max_jobs:
                        break
                threading.Event().wait(1)

    def start(self):
        self._running = True
        t = threading.Thread(target=self._worker, daemon=True, name="crack-queue")
        t.start()
        log.info("Crack queue started (wordlist: %s)", self._wordlist)

    def stop(self):
        self._running = False

    @property
    def queue_size(self) -> int:
        return self._q.qsize()

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active


def _extract_password(output: str) -> str:
    for line in output.splitlines():
        if _RESULT_MARKER in line:
            parts = line.split("]")
            if len(parts) > 1:
                return parts[-1].strip()
    return "unknown"
