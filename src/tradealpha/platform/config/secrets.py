"""
Secrets handling (findings SEC-03, SEC-04).

* Flask SECRET_KEY: taken from the environment unless it is a known placeholder; otherwise generated once and
  persisted in ``<instance>/secret_key`` (mode 0600).
* SecretBox: Fernet encryption for secrets stored in the DB (API key). The Fernet key comes from the OS keyring
  when one is usable (optional ``keyring`` package), else from ``<instance>/fernet.key`` (mode 0600).
  Set HISTORIFY_SECRET_BACKEND=file to force the key file.
"""
from __future__ import annotations

import logging
import os
import secrets as _stdlib_secrets

from cryptography.fernet import Fernet, InvalidToken

from tradealpha.platform.config.env import env

log = logging.getLogger(__name__)

PLACEHOLDER_SECRET_KEYS = {"", "dev_key_change_this", "historify_secret_key_change_in_production",
                           "your_secret_key_here"}
_KEYRING_SERVICE = "historify"


def _write_private(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass


def get_or_create_secret_key(instance_path: str) -> str:
    env = os.getenv("SECRET_KEY", "")
    if env and env not in PLACEHOLDER_SECRET_KEYS:
        return env
    path = os.path.join(instance_path, "secret_key")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().decode().strip()
    key = _stdlib_secrets.token_urlsafe(48)
    _write_private(path, key.encode())
    if env in PLACEHOLDER_SECRET_KEYS and env:
        log.warning("Ignoring placeholder SECRET_KEY from environment; generated a random key in %s", path)
    return key


class SecretBox:
    def __init__(self, instance_path: str, backend: str | None = None):
        self.instance_path = instance_path
        self.backend = (backend or env("SECRET_BACKEND") or "auto").lower()
        self._fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        keyring_id = f"fernet:{os.path.abspath(self.instance_path)}"
        if self.backend in ("auto", "keyring"):
            try:
                import keyring  # optional dependency
                from keyring.backends import fail

                if not isinstance(keyring.get_keyring(), fail.Keyring):
                    existing = keyring.get_password(_KEYRING_SERVICE, keyring_id)
                    if existing:
                        self.backend = "keyring"
                        return existing.encode()
                    key = Fernet.generate_key()
                    keyring.set_password(_KEYRING_SERVICE, keyring_id, key.decode())
                    self.backend = "keyring"
                    return key
            except Exception as e:  # no keyring / headless / locked
                if self.backend == "keyring":
                    raise
                log.debug("keyring unavailable (%s); using key file", e)
        self.backend = "file"
        path = os.path.join(self.instance_path, "fernet.key")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read().strip()
        key = Fernet.generate_key()
        _write_private(path, key)
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise ValueError("secret cannot be decrypted (key changed or data corrupted)") from e


def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    return "\u2022" * 8 + value[-keep:] if len(value) > keep else "\u2022" * len(value)
