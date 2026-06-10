import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("cracker")

_AIRCRACK_MARKER = "KEY FOUND!"


class CrackJob:
    def __init__(self, capture_id: int, filepath: str,
                 bssid: str, ssid: str, cap_type: str = "EAPOL"):
        self.capture_id = capture_id
        self.filepath   = filepath
        self.bssid      = bssid
        self.ssid       = ssid
        self.cap_type   = cap_type


class CrackQueue:
    def __init__(self, config: dict, on_cracked: Callable, vault=None):
        self._wordlist   = config.get("wordlist", "/opt/radioman/wordlists/rockyou.txt")
        self._aircrack   = config.get("aircrack_bin", "aircrack-ng")
        self._hashcat    = config.get("hashcat_bin", "hashcat")
        self._max_jobs   = int(config.get("max_jobs", 1))
        self._on_cracked = on_cracked
        self._vault      = vault   # decrypts encrypted captures to a temp file
        self._q          = queue.Queue()
        self._running    = False
        self._active     = 0
        self._lock       = threading.Lock()
        self._seen       = set()

    def enqueue(self, job: CrackJob):
        if job.filepath in self._seen:
            return
        self._seen.add(job.filepath)
        self._q.put(job)
        log.info("Queued %s crack: %s", job.cap_type, job.ssid or job.bssid)

    def _crack(self, job: CrackJob):
        try:
            if not os.path.exists(self._wordlist):
                log.warning("Wordlist missing: %s", self._wordlist)
                return
            # If the capture is encrypted, decrypt to a temp file for the run.
            if self._vault and self._vault.is_encrypted(job.filepath):
                if self._vault.locked:
                    log.warning("Vault locked — deferring crack of %s (re-enqueues on unlock)",
                                job.ssid or job.bssid)
                    self._seen.discard(job.filepath)
                    return
                try:
                    with self._vault.plaintext(job.filepath) as ptxt:
                        self._run_crack(job, ptxt)
                except Exception as e:
                    log.error("decrypt-for-crack failed (%s): %s", job.filepath, e)
                return
            if not os.path.exists(job.filepath):
                log.warning("Capture file missing: %s", job.filepath)
                return
            self._run_crack(job, job.filepath)
        finally:
            with self._lock:
                self._active -= 1

    def _run_crack(self, job: CrackJob, path: str):
        """Run the appropriate cracker against a plaintext capture at `path`."""
        if job.cap_type == "PMKID":
            self._crack_hashcat(job, path)
        else:
            if not self._crack_aircrack(job, path):
                self._crack_hashcat(job, path)

    def _crack_aircrack(self, job: CrackJob, path: str) -> bool:
        log.info("aircrack-ng: %s", job.ssid or job.bssid)
        cmd = [self._aircrack, "-q", "-w", self._wordlist]
        if job.bssid:
            cmd += ["-b", job.bssid]
        cmd.append(path)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            output = result.stdout + result.stderr
            if _AIRCRACK_MARKER in output:
                password = _extract_aircrack_password(output)
                log.info("CRACKED (aircrack) %s → %s", job.ssid or job.bssid, password)
                self._on_cracked(job.capture_id, password)
                return True
            log.info("aircrack-ng: not cracked — %s", job.ssid or job.bssid)
        except subprocess.TimeoutExpired:
            log.warning("aircrack-ng timed out: %s", job.filepath)
        except FileNotFoundError:
            log.warning("aircrack-ng not found")
        return False

    def _crack_hashcat(self, job: CrackJob, path: str) -> bool:
        if not shutil.which("hcxpcapngtool"):
            log.warning("hcxpcapngtool not found — install hcxtools for PMKID cracking")
            return False
        if not shutil.which(self._hashcat):
            log.warning("hashcat not found — PMKID cracking unavailable")
            return False

        hash_file = path + ".hc22000"
        pot_file  = path + ".pot"

        try:
            conv = subprocess.run(
                ["hcxpcapngtool", "-o", hash_file, path],
                capture_output=True, timeout=60,
            )
            if not os.path.exists(hash_file) or os.path.getsize(hash_file) == 0:
                log.info("hcxpcapngtool: no hashes extracted from %s", job.filepath)
                return False

            log.info("hashcat: %s", job.ssid or job.bssid)
            subprocess.run(
                [
                    self._hashcat, "-m", "22000", "-a", "0",
                    "--force", "--quiet",
                    "--potfile-path", pot_file,
                    hash_file, self._wordlist,
                ],
                capture_output=True, text=True, timeout=3600,
            )

            if os.path.exists(pot_file) and os.path.getsize(pot_file) > 0:
                with open(pot_file) as f:
                    line = f.readline().strip()
                if ":" in line:
                    password = line.rsplit(":", 1)[-1]
                    log.info("CRACKED (hashcat) %s → %s", job.ssid or job.bssid, password)
                    self._on_cracked(job.capture_id, password)
                    return True

            log.info("hashcat: not cracked — %s", job.ssid or job.bssid)
        except subprocess.TimeoutExpired:
            log.warning("hashcat timed out: %s", job.filepath)
        except Exception as e:
            log.error("hashcat error: %s", e)
        finally:
            for f in (hash_file, pot_file):
                try:
                    os.unlink(f)
                except OSError:
                    pass
        return False

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
                time.sleep(1)

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


def _extract_aircrack_password(output: str) -> str:
    for line in output.splitlines():
        if _AIRCRACK_MARKER in line:
            parts = line.split("]")
            if len(parts) > 1:
                return parts[-1].strip()
    return "unknown"
