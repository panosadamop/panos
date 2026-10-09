import keyring
import pytest
from keyring.backends import fail

from panos_core.secrets import (
    InMemorySecretStore,
    KeyringSecretStore,
    SecretStoreUnavailableError,
    create_secret_store,
    get_or_create_node_key,
)

pytestmark = pytest.mark.req("NFR-SEC-003")


def test_node_key_created_once_and_stable() -> None:
    store = InMemorySecretStore()
    key = get_or_create_node_key(store)
    assert len(key) == 32
    assert get_or_create_node_key(store) == key


def test_memory_store_round_trip() -> None:
    store = create_secret_store("memory")
    store.set("a", "1")
    assert store.get("a") == "1"
    store.delete("a")
    store.delete("a")  # idempotent
    assert store.get("a") is None


def test_keyring_store_refuses_insecure_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())  # type: ignore[no-untyped-call]
    with pytest.raises(SecretStoreUnavailableError, match="plaintext"):
        KeyringSecretStore()
