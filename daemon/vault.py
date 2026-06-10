"""
vault.py — optional at-rest encryption for capture files.

bettercap writes plaintext .pcapng into the captures dir; radioman detects each
new file and (if the vault is unlocked) encrypts it in place to <name>.enc,
deleting the plaintext. The crack queue and the download endpoint get a
transparent plaintext view via plaintext() (decrypt to a temp file, wiped after).

Crypto: the system `openssl enc -aes-256-cbc -pbkdf2 -salt` — present on every
Pi, so no extra Python dependency and no fragile native wheel on the Zero 2W.
The passphrase is piped on stdin (never argv). CBC gives confidentiality at rest
(the threat model is a lost/seized device), not authentication.

Two key modes (config-selectable):
  * config — passphrase lives in radioman.conf; key always available, so
             capture→encrypt→auto-crack→download run unattended. Lowest friction.
  * pin    — no passphrase on disk; the operator unlocks via the dashboard each
             boot and the key is held only in memory. Strongest at rest. Captures
             that arrive before unlock stay plaintext until unlock, then migrate.
"""

import hashlib
import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager

log = logging.getLogger("vault")

ENC_EXT = ".enc"
_CAP_EXTS = (".pcap", ".pcapng", ".cap")


class Vault:
    def __init__(self, enabled: bool, mode: str, passphrase: str, captures_dir: str):
        self.enabled       = bool(enabled)
        self.mode          = (mode or "config").strip().lower()
        if self.mode not in ("config", "pin"):
            self.mode = "config"
        self._captures_dir = captures_dir
        self._key          = ""    # active passphrase, in memory only
        if self.enabled and self.mode == "config" and passphrase:
            self._key = passphrase
        if self.enabled:
            log.info("Vault enabled (mode=%s, %s)", self.mode,
                     "unlocked" if self._key else "LOCKED — awaiting dashboard unlock")
        else:
            log.info("Vault disabled — captures stored in plaintext")

    # ── State ───────────────────────────────────────────────────────────────
    @property
    def locked(self) -> bool:
        """Enabled but no key loaded — cannot encrypt or decrypt yet."""
        return self.enabled and not self._key

    def fingerprint(self) -> str:
        """A short, non-reversible id of the loaded key — safe to show on the
        e-ink / dashboard so the operator can confirm which key is in use."""
        if not self._key:
            return ""
        return hashlib.sha256(b"radioman-vault:" + self._key.encode()).hexdigest()[:8]

    def status(self) -> dict:
        return {
            "enabled":     self.enabled,
            "mode":        self.mode,
            "locked":      self.locked,
            "fingerprint": self.fingerprint(),
            "encrypted":   self.count_encrypted(),
            "plaintext":   self.count_plaintext(),
        }

    def count_encrypted(self) -> int:
        return len(self._list(ENC_EXT))

    def count_plaintext(self) -> int:
        return len([f for f in self._list() if f.endswith(_CAP_EXTS)])

    def _list(self, suffix: str = "") -> list:
        try:
            return [f for f in os.listdir(self._captures_dir)
                    if (not suffix or f.endswith(suffix))]
        except OSError:
            return []

    @staticmethod
    def is_encrypted(path: str) -> bool:
        return path.endswith(ENC_EXT)

    # ── Lock / unlock (pin mode) ──────────────────────────────────────────────
    def unlock(self, passphrase: str) -> dict:
        if not self.enabled:
            return {"ok": False, "error": "vault is disabled"}
        if not passphrase:
            return {"ok": False, "error": "passphrase/PIN required"}
        self._key = passphrase
        migrated = self.encrypt_pending()
        log.info("Vault unlocked (fp=%s) — migrated %d plaintext capture(s)",
                 self.fingerprint(), migrated)
        return {"ok": True, "fingerprint": self.fingerprint(), "migrated": migrated}

    def lock(self) -> dict:
        """Forget the in-memory key (pin mode only)."""
        if self.mode != "pin":
            return {"ok": False, "error": "lock only applies in pin mode"}
        self._key = ""
        log.info("Vault locked — key cleared from memory")
        return {"ok": True}

    # ── Encrypt ───────────────────────────────────────────────────────────────
    def encrypt_file(self, path: str) -> str:
        """Encrypt a plaintext capture in place → <path>.enc, remove the plaintext.
        Returns the new path, or the original path unchanged on any failure or if
        the vault can't act (disabled / locked / already encrypted)."""
        if not self.enabled or self.locked:
            return path
        if self.is_encrypted(path) or not os.path.isfile(path):
            return path
        enc = path + ENC_EXT
        try:
            self._openssl(["-salt", "-in", path, "-out", enc])
        except Exception as e:
            log.error("encrypt failed for %s: %s", os.path.basename(path), e)
            if os.path.exists(enc):
                _shred(enc)
            return path
        _shred(path)   # remove the plaintext original
        log.info("Encrypted %s", os.path.basename(enc))
        return enc

    def encrypt_pending(self) -> int:
        """Encrypt any plaintext captures sitting in the dir (used on unlock)."""
        if not self.enabled or self.locked:
            return 0
        n = 0
        for fname in self._list():
            if fname.endswith(_CAP_EXTS):
                full = os.path.join(self._captures_dir, fname)
                if self.encrypt_file(full) != full:
                    n += 1
        return n

    # ── Decrypt (transparent, temporary) ──────────────────────────────────────
    @contextmanager
    def plaintext(self, path: str):
        """Yield a readable plaintext path for `path`. If it's encrypted, decrypt
        to a temp file (wiped on exit). If it's already plaintext, yield it as-is.
        Raises RuntimeError if the file is encrypted but the vault is locked."""
        if not self.is_encrypted(path):
            yield path
            return
        if self.locked:
            raise RuntimeError("vault is locked — unlock to access encrypted captures")
        fd, tmp = tempfile.mkstemp(suffix=".pcapng", prefix="rmvault-")
        os.close(fd)
        try:
            self._openssl(["-d", "-in", path, "-out", tmp])
            yield tmp
        finally:
            _shred(tmp)

    # ── openssl plumbing ──────────────────────────────────────────────────────
    def _openssl(self, args: list):
        if not self._key:
            raise RuntimeError("no key loaded")
        cmd = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-pass", "stdin"] + args
        proc = subprocess.run(
            cmd, input=(self._key + "\n").encode(),
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "openssl failed")


def _shred(path: str):
    """Best-effort secure-ish delete: overwrite then unlink. (SD wear-levelling
    means this isn't a guarantee, but it beats leaving plaintext on the FS.)"""
    try:
        if os.path.isfile(path):
            size = os.path.getsize(path)
            with open(path, "wb") as f:
                f.write(os.urandom(min(size, 1 << 20)))
                f.flush()
                os.fsync(f.fileno())
        os.unlink(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
