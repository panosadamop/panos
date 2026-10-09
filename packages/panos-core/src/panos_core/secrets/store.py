"""Secret store implementations."""

import base64
import contextlib
import secrets
from typing import Literal, Protocol

import keyring
import keyring.backend
import keyring.errors
from keyring.backends import fail

SERVICE_NAME = "Panos"
NODE_KEY_NAME = "node-hmac-key"
NODE_KEY_BYTES = 32

SecretBackend = Literal["keyring", "memory"]


class SecretStoreUnavailableError(RuntimeError):
    """No secure OS-backed store is available (e.g. headless Linux without Secret Service)."""


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringSecretStore:
    """OS-backed store: Windows Credential Manager, macOS Keychain, Linux Secret Service."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        backend = keyring.get_keyring()
        if _is_insecure(backend):
            raise SecretStoreUnavailableError(
                f"no secure keyring backend available (got {type(backend).__name__}); "
                "Panos refuses to store secrets in plaintext"
            )
        self._service = service

    def get(self, name: str) -> str | None:
        return keyring.get_password(self._service, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(self._service, name, value)

    def delete(self, name: str) -> None:
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(self._service, name)


def _is_insecure(backend: keyring.backend.KeyringBackend) -> bool:
    if isinstance(backend, fail.Keyring):
        return True
    module = type(backend).__module__
    # keyrings.alt plaintext/"encrypted" file backends are not OS-backed.
    return module.startswith("keyrings.alt")


class InMemorySecretStore:
    """Process-local store for tests. Never use in production: values vanish on exit."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


def create_secret_store(backend: SecretBackend) -> SecretStore:
    if backend == "memory":
        return InMemorySecretStore()
    return KeyringSecretStore()


def get_or_create_node_key(store: SecretStore) -> bytes:
    """Return the node HMAC key used to sign approval tokens, creating it on first use."""
    existing = store.get(NODE_KEY_NAME)
    if existing:
        return base64.urlsafe_b64decode(existing.encode())
    key = secrets.token_bytes(NODE_KEY_BYTES)
    store.set(NODE_KEY_NAME, base64.urlsafe_b64encode(key).decode())
    return key
