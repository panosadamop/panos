"""This node's membership of a hub: join, stored identity, leave (ADR-029 §2–3).

* ``<data_dir>/remote/hub.json``: hub URLs, node id, certificate expiry;
* ``<data_dir>/remote/node.crt`` and ``ca.crt``: public certificates;
* the node's private key: OS keyring (``remote-node-key``).
"""

import json
import ssl
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx

from panos_core import API_VERSION
from panos_core.config import PanosSettings
from panos_core.domain.errors import DomainError
from panos_core.remote import pki
from panos_core.secrets import SecretStore

KEY_SECRET: Final = "remote-node-key"  # noqa: S105 - secret name, not a secret


class MembershipError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Membership:
    hub_url: str  # https://host:48700
    link_url: str  # wss://host:48701/link
    node_id: str
    name: str
    joined_at: str
    not_after: str
    ca_fingerprint: str


def remote_dir(settings: PanosSettings) -> Path:
    return settings.data_dir / "remote"


def load(settings: PanosSettings) -> Membership | None:
    path = remote_dir(settings) / "hub.json"
    if not path.exists():
        return None
    return Membership(**json.loads(path.read_text(encoding="utf-8")))


def _save(settings: PanosSettings, membership: Membership) -> None:
    folder = remote_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "hub.json").write_text(json.dumps(asdict(membership), indent=2), encoding="utf-8")


def credentials(settings: PanosSettings, secret_store: SecretStore) -> tuple[bytes, bytes, bytes]:
    """(CA PEM, node certificate PEM, node key PEM)."""
    folder = remote_dir(settings)
    key = secret_store.get(KEY_SECRET)
    if key is None or not (folder / "node.crt").exists():
        raise MembershipError("not_joined", "this node has not joined a hub (panos node join)")
    return (folder / "ca.crt").read_bytes(), (folder / "node.crt").read_bytes(), key.encode()


def store_certificate(
    settings: PanosSettings, cert_pem: bytes, key_pem: bytes, secret_store: SecretStore
) -> None:
    folder = remote_dir(settings)
    secret_store.set(KEY_SECRET, key_pem.decode())
    (folder / "node.crt").write_bytes(cert_pem)
    membership = load(settings)
    if membership is not None:
        not_after = pki.load_cert(cert_pem).not_valid_after_utc.isoformat()
        _save(settings, Membership(**{**asdict(membership), "not_after": not_after}))


def _base(hub_url: str) -> str:
    parts = urlsplit(hub_url if "://" in hub_url else f"https://{hub_url}")
    if parts.scheme != "https" or not parts.hostname:
        raise MembershipError("invalid", "the hub address must be https://<host>[:port]")
    port = parts.port or 48700
    return f"https://{parts.hostname}:{port}"


async def join(
    settings: PanosSettings,
    secret_store: SecretStore,
    *,
    hub_url: str,
    code: str,
    fingerprint: str,
    node_id: str,
    name: str,
    platform: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Membership:
    """Pin the hub CA by fingerprint, then enroll with a CSR and the one-time code."""
    base = _base(hub_url)
    # 1. Fetch the CA without trusting anything yet; accept it only if the fingerprint matches.
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    try:
        async with httpx.AsyncClient(verify=insecure, transport=transport, timeout=15) as client:
            response = await client.get(f"{base}/api/{API_VERSION}/ca")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MembershipError("unreachable", f"cannot reach the hub at {base}: {exc}") from exc
    ca_pem = response.text.encode()
    try:
        actual = pki.fingerprint(ca_pem)
    except pki.PkiError as exc:
        raise MembershipError("invalid", "the hub did not return a certificate") from exc
    if not pki.same_fingerprint(actual, fingerprint):
        raise MembershipError(
            "fingerprint", f"the hub's CA fingerprint is {actual}, not the one you were given"
        )
    # 2. Enroll over TLS verified against the pinned CA.
    key = pki.generate_key()
    csr = pki.make_csr(key, node_id).decode()
    verified = pki.client_context(ca_pem)
    try:
        async with httpx.AsyncClient(verify=verified, transport=transport, timeout=30) as client:
            response = await client.post(
                f"{base}/api/{API_VERSION}/enroll",
                json={
                    "code": code,
                    "csr": csr,
                    "node_id": node_id,
                    "name": name,
                    "platform": platform,
                },
            )
    except httpx.HTTPError as exc:
        raise MembershipError("unreachable", f"enrollment failed: {exc}") from exc
    if response.status_code != 200:
        detail = response.json().get("detail", response.text) if response.content else ""
        raise MembershipError("refused", f"the hub refused the enrollment: {detail}")
    body = response.json()
    cert_pem = body["certificate"].encode()
    if pki.node_id_of(pki.load_cert(cert_pem)) != node_id:
        raise MembershipError("invalid", "the hub issued a certificate for another node")
    host = urlsplit(base).hostname or ""
    folder = remote_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ca.crt").write_bytes(ca_pem)
    membership = Membership(
        hub_url=base,
        link_url=f"wss://{host}:{body['link_port']}/link",
        node_id=node_id,
        name=name,
        joined_at=datetime.now(UTC).isoformat(),
        not_after=body["not_after"],
        ca_fingerprint=actual,
    )
    _save(settings, membership)
    store_certificate(settings, cert_pem, pki.key_to_pem(key), secret_store)
    return membership


def leave(settings: PanosSettings, secret_store: SecretStore) -> bool:
    folder = remote_dir(settings)
    existed = (folder / "hub.json").exists()
    secret_store.delete(KEY_SECRET)
    for name in ("hub.json", "node.crt", "ca.crt"):
        (folder / name).unlink(missing_ok=True)
    return existed
