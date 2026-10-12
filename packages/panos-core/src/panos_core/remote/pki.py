"""Private PKI for the hub and its nodes (ADR-029, NFR-SEC-006).

* The hub has its own EC P-256 CA. It signs the hub's server certificate and one client
  certificate per enrolled node.
* A node certificate is bound to the node id twice: as the subject CN and as the URI SAN
  ``panos:node:<id>``. Only client authentication is allowed for it.
* ``ssl`` cannot load a private key from memory, so TLS contexts write the key to a
  private temporary file, load it and delete it at once.
"""

import datetime as dt
import hashlib
import ipaddress
import os
import ssl
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

NODE_URI_PREFIX: Final = "panos:node:"
CA_DAYS: Final = 3650
SERVER_DAYS: Final = 365
NODE_DAYS: Final = 90
RENEW_WITHIN: Final = dt.timedelta(days=30)


class PkiError(ValueError):
    """A certificate or request is invalid for its purpose."""


@dataclass(frozen=True)
class Issued:
    cert_pem: bytes
    serial: str  # hex
    not_after: dt.datetime


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def generate_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_to_pem(key: ec.EllipticCurvePrivateKey, password: bytes | None = None) -> bytes:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )


def load_key(pem: bytes, password: bytes | None = None) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise PkiError("expected an EC private key")
    return key


def load_cert(pem: bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem)
    except ValueError as exc:
        raise PkiError("not a PEM certificate") from exc


def cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def fingerprint(cert: x509.Certificate | bytes) -> str:
    """``SHA256:<hex>`` of the DER certificate (shown when joining a node)."""
    if isinstance(cert, bytes):
        cert = load_cert(cert)
    return "SHA256:" + hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def same_fingerprint(a: str, b: str) -> bool:
    norm = [x.strip().lower().removeprefix("sha256:").replace(":", "") for x in (a, b)]
    return norm[0] == norm[1] and len(norm[0]) == 64


def serial_hex(cert: x509.Certificate) -> str:
    return format(cert.serial_number, "x")


def create_ca(name: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = generate_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Panos hub CA ({name})")])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    subject: x509.Name,
    public_key: ec.EllipticCurvePublicKey,
    sans: list[x509.GeneralName],
    usage: x509.ObjectIdentifier,
    days: int,
) -> Issued:
    now = _now()
    not_after = now + dt.timedelta(days=days)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return Issued(cert_pem(cert), serial_hex(cert), not_after)


def issue_server_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    hostnames: Iterable[str],
    addresses: Iterable[str] = (),
    *,
    days: int = SERVER_DAYS,
) -> tuple[ec.EllipticCurvePrivateKey, Issued]:
    names = [h.strip().lower() for h in hostnames if h.strip()]
    if not names:
        raise PkiError("the hub certificate needs at least one hostname")
    sans: list[x509.GeneralName] = [x509.DNSName(n) for n in dict.fromkeys(names)]
    sans += [x509.IPAddress(ipaddress.ip_address(a)) for a in dict.fromkeys(addresses)]
    key = generate_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    issued = _leaf(
        ca_key, ca_cert, subject, key.public_key(), sans, ExtendedKeyUsageOID.SERVER_AUTH, days
    )
    return key, issued


def make_csr(key: ec.EllipticCurvePrivateKey, node_id: str) -> bytes:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


def sign_node_csr(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    csr_pem: bytes,
    node_id: str,
    *,
    days: int = NODE_DAYS,
) -> Issued:
    """Sign a node's CSR after checking its signature, key type and subject."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise PkiError("not a PEM certificate request") from exc
    if not csr.is_signature_valid:
        raise PkiError("the certificate request signature is invalid")
    public_key = csr.public_key()
    if (
        not isinstance(public_key, ec.EllipticCurvePublicKey)
        or public_key.curve.name != "secp256r1"
    ):
        raise PkiError("node keys must be EC P-256")
    names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if len(names) != 1 or names[0].value != node_id:
        raise PkiError("the certificate request is not for this node")
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    sans: list[x509.GeneralName] = [x509.UniformResourceIdentifier(f"{NODE_URI_PREFIX}{node_id}")]
    return _leaf(ca_key, ca_cert, subject, public_key, sans, ExtendedKeyUsageOID.CLIENT_AUTH, days)


def node_id_of(cert: x509.Certificate) -> str:
    """The node id a client certificate is bound to (CN and URI SAN must agree)."""
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    try:
        uris = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise PkiError("the certificate has no node identity") from exc
    bound = [
        u.removeprefix(NODE_URI_PREFIX)
        for u in uris.get_values_for_type(x509.UniformResourceIdentifier)
        if u.startswith(NODE_URI_PREFIX)
    ]
    if len(names) != 1 or bound != [names[0].value]:
        raise PkiError("the certificate's node identity is inconsistent")
    return str(names[0].value)


def needs_renewal(cert: x509.Certificate, *, now: dt.datetime | None = None) -> bool:
    return cert.not_valid_after_utc - (now or _now()) < RENEW_WITHIN


# ---------------------------------------------------------------------- TLS contexts
def _load_chain(ctx: ssl.SSLContext, cert: bytes, key: bytes, scratch: Path | None) -> None:
    folder = scratch or Path(tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="panos-tls-", suffix=".pem", dir=folder)
    try:
        with os.fdopen(fd, "wb") as handle:  # mkstemp creates it readable by the owner only
            handle.write(key + b"\n" + cert)
        ctx.load_cert_chain(name)
    finally:
        os.unlink(name)


def server_context(
    ca_pem: bytes,
    cert: bytes,
    key: bytes,
    *,
    require_client_cert: bool,
    scratch: Path | None = None,
) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    _load_chain(ctx, cert, key, scratch)
    if require_client_cert:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=ca_pem.decode())
    return ctx


def client_context(
    ca_pem: bytes,
    cert: bytes | None = None,
    key: bytes | None = None,
    *,
    scratch: Path | None = None,
) -> ssl.SSLContext:
    """Trust only the pinned hub CA; present the node certificate when given."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca_pem.decode())
    if cert is not None and key is not None:
        _load_chain(ctx, cert, key, scratch)
    return ctx
