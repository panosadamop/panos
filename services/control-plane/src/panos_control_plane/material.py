"""Hub key material and first-time setup (ADR-029 §1).

Files under ``<data_dir>/hub``:
* ``ca.crt``, ``server.crt``: public certificates;
* ``ca.key``, ``server.key``: PEM keys, **encrypted** with a passphrase kept in the OS
  keyring (``hub-key-passphrase``);
* ``docker-compose.yml`` and ``.env``: PostgreSQL for the hub (``panos hub init`` without
  ``--database-url``). The database URL is also kept in the keyring (``hub-database-url``).
"""

import contextlib
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from panos_core.config import PanosSettings
from panos_core.remote import pki
from panos_core.secrets import SecretStore

PASSPHRASE_SECRET: Final = "hub-key-passphrase"  # noqa: S105 - secret name, not a secret
DATABASE_SECRET: Final = "hub-database-url"  # noqa: S105
DB_PORT: Final = 55432

COMPOSE: Final = """# Panos hub database (ADR-028). Started with: docker compose up -d
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: panos
      POSTGRES_DB: panos_hub
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
    ports:
      - "127.0.0.1:{port}:5432"   # local only: the hub process is the only client
    volumes:
      - panos_hub_pg:/var/lib/postgresql/data
volumes:
  panos_hub_pg:
"""


class HubSetupError(RuntimeError):
    pass


def hub_dir(settings: PanosSettings) -> Path:
    return settings.data_dir / "hub"


def default_hostname() -> str:
    return socket.gethostname().lower()


def lan_addresses() -> list[str]:
    """Best-effort IPv4 addresses of this PC on the local network (no traffic is sent)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routing lookup only
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    except OSError:
        pass
    return [a for a in dict.fromkeys(found) if not a.startswith("127.")]


@dataclass(frozen=True)
class HubMaterial:
    ca_key: ec.EllipticCurvePrivateKey
    ca_pem: bytes
    server_cert: Path
    server_key: Path  # encrypted PEM
    passphrase: str
    hostnames: list[str]

    @property
    def ca_cert(self) -> x509.Certificate:
        return pki.load_cert(self.ca_pem)

    @property
    def fingerprint(self) -> str:
        return pki.fingerprint(self.ca_pem)


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    # Windows ignores POSIX modes; the file lives in the user's private profile there.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def init_hub(
    settings: PanosSettings,
    secret_store: SecretStore,
    *,
    hostname: str | None = None,
    addresses: list[str] | None = None,
    database_url: str | None = None,
) -> HubMaterial:
    """Create the CA (once), a fresh server certificate and the database configuration."""
    folder = hub_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    passphrase = secret_store.get(PASSPHRASE_SECRET)
    if passphrase is None:
        passphrase = secrets.token_urlsafe(32)
        secret_store.set(PASSPHRASE_SECRET, passphrase)
    if not (folder / "ca.key").exists():
        ca_key, ca_cert = pki.create_ca(hostname or default_hostname())
        _write_private(folder / "ca.key", pki.key_to_pem(ca_key, passphrase.encode()))
        (folder / "ca.crt").write_bytes(pki.cert_pem(ca_cert))
    host = (hostname or settings.hub.hostname or default_hostname()).lower()
    names = [host, f"{host}.local", "localhost"] if "." not in host else [host, "localhost"]
    material = _issue_server(settings, secret_store, names, addresses or lan_addresses())
    if database_url is None and secret_store.get(DATABASE_SECRET) is None:
        password = secrets.token_urlsafe(24)
        (folder / "docker-compose.yml").write_text(COMPOSE.format(port=DB_PORT), encoding="utf-8")
        _write_private(folder / ".env", f"POSTGRES_PASSWORD={password}\n".encode())
        database_url = f"postgresql://panos:{password}@127.0.0.1:{DB_PORT}/panos_hub"
    if database_url is not None:
        secret_store.set(DATABASE_SECRET, database_url)
    return material


def _issue_server(
    settings: PanosSettings, secret_store: SecretStore, names: list[str], addresses: list[str]
) -> HubMaterial:
    folder = hub_dir(settings)
    base = load_material(settings, secret_store, require_server=False)
    key, issued = pki.issue_server_cert(base.ca_key, base.ca_cert, names, [*addresses, "127.0.0.1"])
    _write_private(folder / "server.key", pki.key_to_pem(key, base.passphrase.encode()))
    (folder / "server.crt").write_bytes(issued.cert_pem + base.ca_pem)  # chain for browsers
    return load_material(settings, secret_store)


def load_material(
    settings: PanosSettings, secret_store: SecretStore, *, require_server: bool = True
) -> HubMaterial:
    folder = hub_dir(settings)
    passphrase = secret_store.get(PASSPHRASE_SECRET)
    if passphrase is None or not (folder / "ca.key").exists():
        raise HubSetupError("this PC is not a hub yet: run `panos hub init`")
    ca_key = pki.load_key((folder / "ca.key").read_bytes(), passphrase.encode())
    ca_pem = (folder / "ca.crt").read_bytes()
    server = folder / "server.crt"
    if require_server and not server.exists():
        raise HubSetupError("the hub has no server certificate: run `panos hub init`")
    hostnames: list[str] = []
    if server.exists():
        cert = pki.load_cert(server.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        hostnames = list(san.value.get_values_for_type(x509.DNSName))
    return HubMaterial(ca_key, ca_pem, server, folder / "server.key", passphrase, hostnames)


def renew_server_if_needed(settings: PanosSettings, secret_store: SecretStore) -> bool:
    material = load_material(settings, secret_store)
    cert = pki.load_cert(material.server_cert.read_bytes())
    if not pki.needs_renewal(cert):
        return False
    addresses = [str(a) for a in _ips(cert)]
    _issue_server(settings, secret_store, material.hostnames, addresses)
    return True


def _ips(cert: x509.Certificate) -> list[object]:
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return [a for a in san.value.get_values_for_type(x509.IPAddress) if str(a) != "127.0.0.1"]


def database_url(settings: PanosSettings, secret_store: SecretStore) -> str:
    url = settings.hub.database_url or secret_store.get(DATABASE_SECRET)
    if not url:
        raise HubSetupError("no hub database configured: run `panos hub init`")
    return url
