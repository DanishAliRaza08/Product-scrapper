"""Credential storage. SQL keeps only an opaque ``credential_reference``.

``LocalSecretStore`` (development / single-host) encrypts each secret with Fernet into a
file under ``MPI_SECRETS_DIR``. Production deployments should implement
:class:`SecretStore` against a managed secrets service (AWS Secrets Manager, Azure Key
Vault, GCP Secret Manager) and select it with ``MPI_SECRETS_BACKEND``.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretStore(Protocol):
    def put(self, payload: dict) -> str: ...
    def get(self, reference: str) -> dict: ...
    def update(self, reference: str, payload: dict) -> None: ...
    def delete(self, reference: str) -> None: ...


class SecretNotFound(KeyError):
    pass


class LocalSecretStore:
    def __init__(self, directory: Path, key: bytes):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(key)

    def _path(self, reference: str) -> Path:
        if not reference.startswith("sec_") or not reference[4:].isalnum():
            raise SecretNotFound(reference)
        return self.directory / f"{reference}.bin"

    def put(self, payload: dict) -> str:
        reference = "sec_" + secrets.token_hex(16)
        self.update(reference, payload)
        return reference

    def get(self, reference: str) -> dict:
        path = self._path(reference)
        if not path.exists():
            raise SecretNotFound(reference)
        try:
            return json.loads(self._fernet.decrypt(path.read_bytes()))
        except InvalidToken as exc:
            raise SecretNotFound(reference) from exc

    def update(self, reference: str, payload: dict) -> None:
        path = self._path(reference)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(payload).encode()))
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def delete(self, reference: str) -> None:
        self._path(reference).unlink(missing_ok=True)


_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.secrets_backend != "local":
            raise RuntimeError(f"Unsupported secrets backend: {settings.secrets_backend}")
        if settings.secrets_encryption_key is not None:
            key = settings.secrets_encryption_key.get_secret_value().encode()
        elif settings.is_production:
            raise RuntimeError("MPI_SECRETS_ENCRYPTION_KEY must be set in production")
        else:
            key_file = settings.secrets_dir / ".dev-key"
            settings.secrets_dir.mkdir(parents=True, exist_ok=True)
            if not key_file.exists():
                key_file.write_bytes(Fernet.generate_key())
                os.chmod(key_file, 0o600)
            key = key_file.read_bytes()
        _store = LocalSecretStore(settings.secrets_dir, key)
    return _store


def set_secret_store(store: SecretStore | None) -> None:
    global _store
    _store = store
