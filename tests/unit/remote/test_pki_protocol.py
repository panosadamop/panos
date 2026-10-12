"""Node identity PKI and the hub protocol (M9-02/03, ADR-028/029, NFR-SEC-006, AC-US062-02)."""

import asyncio
import datetime as dt
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from panos_core.remote import pki
from panos_core.remote.protocol import (
    Control,
    Dispatch,
    Hello,
    ProtocolError,
    decode,
    encode,
)

pytestmark = pytest.mark.req("NFR-SEC-006")

Hub = tuple[ec.EllipticCurvePrivateKey, x509.Certificate, bytes, bytes]
NODE = "nod_0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def hub() -> "Hub":
    ca_key, ca_cert = pki.create_ca("test")
    server_key, issued = pki.issue_server_cert(ca_key, ca_cert, ["localhost"], ["127.0.0.1"])
    return ca_key, ca_cert, issued.cert_pem, pki.key_to_pem(server_key)


def _node(hub_: Hub, node_id: str = NODE) -> tuple[bytes, bytes, pki.Issued]:
    ca_key, ca_cert, _, _ = hub_
    key = pki.generate_key()
    issued = pki.sign_node_csr(ca_key, ca_cert, pki.make_csr(key, node_id), node_id)
    return issued.cert_pem, pki.key_to_pem(key), issued


def test_node_certificate_is_bound_to_the_node(hub: Hub) -> None:
    cert_pem, _, issued = _node(hub)
    cert = pki.load_cert(cert_pem)
    assert pki.node_id_of(cert) == NODE and issued.serial == pki.serial_hex(cert)
    ca_key, ca_cert, _, _ = hub
    other = pki.generate_key()
    with pytest.raises(pki.PkiError, match="not for this node"):
        pki.sign_node_csr(ca_key, ca_cert, pki.make_csr(other, "nod_evil"), NODE)
    with pytest.raises(pki.PkiError):
        pki.sign_node_csr(ca_key, ca_cert, b"garbage", NODE)
    assert not pki.needs_renewal(cert)
    assert pki.needs_renewal(cert, now=issued.not_after - dt.timedelta(days=10))


def test_fingerprints_and_encrypted_keys(hub: Hub) -> None:
    _, ca_cert, _, _ = hub
    fp = pki.fingerprint(ca_cert)
    assert fp.startswith("SHA256:") and len(fp) == 71
    assert pki.same_fingerprint(fp, fp.lower().replace("sha256:", ""))
    assert not pki.same_fingerprint(fp, "SHA256:" + "0" * 64)
    key = pki.generate_key()
    pem = pki.key_to_pem(key, b"passphrase")
    assert b"ENCRYPTED" in pem
    with pytest.raises((TypeError, ValueError)):
        pki.load_key(pem)
    assert pki.load_key(pem, b"passphrase").public_key() == key.public_key()


async def _handshake(server_ctx: ssl.SSLContext, client_ctx: ssl.SSLContext) -> str | None:
    """Return the client's node id as the server saw it, or raise on a failed handshake."""
    seen: list[str | None] = []

    async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        seen.append(pki.node_id_of(x509.load_der_x509_certificate(der)) if der else None)
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=client_ctx, server_hostname="localhost"
        )
        # TLS 1.3: a refused client certificate surfaces as a closed connection.
        if await reader.read(2) != b"ok":
            raise ConnectionError("the server closed the connection")
        writer.close()
    finally:
        server.close()
    return seen[0] if seen else None


async def test_mutual_tls_accepts_only_hub_issued_nodes(hub: Hub) -> None:
    _, ca_cert, server_cert, server_key = hub
    ca_pem = pki.cert_pem(ca_cert)
    server_ctx = pki.server_context(ca_pem, server_cert, server_key, require_client_cert=True)
    cert, key, _ = _node(hub)
    assert await _handshake(server_ctx, pki.client_context(ca_pem, cert, key)) == NODE

    # A certificate from another CA is refused by the hub.
    rogue_key, rogue_ca = pki.create_ca("rogue")
    rk = pki.generate_key()
    rogue = pki.sign_node_csr(rogue_key, rogue_ca, pki.make_csr(rk, NODE), NODE)
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        await _handshake(server_ctx, pki.client_context(ca_pem, rogue.cert_pem, pki.key_to_pem(rk)))
    # A node that pins a different CA refuses the hub.
    with pytest.raises(ssl.SSLCertVerificationError):
        await _handshake(server_ctx, pki.client_context(pki.cert_pem(rogue_ca), cert, key))


def test_protocol_allowlist_by_direction() -> None:
    frame = encode(Dispatch(dispatch_id="d1", objective="Run tests", requested_by="user@hub"))
    env, msg = decode(frame, "to_node")
    assert env.type == "dispatch" and isinstance(msg, Dispatch)
    with pytest.raises(ProtocolError, match="not allowed to_hub"):
        decode(frame, "to_hub")  # a node never accepts... nor sends a dispatch upward
    hello = encode(
        Hello(node_id=NODE, name="pc", platform="windows", version="0.1", api_version="v1")
    )
    assert decode(hello, "to_hub")[0].type == "hello"
    for bad in (
        '{"v":1,"id":"x","type":"shell","payload":{"cmd":"rm -rf /"}}',
        '{"v":1,"id":"x","type":"read_file","payload":{"path":"C:/secret"}}',
        '{"v":2,"id":"x","type":"control","payload":{}}',
        '{"v":1,"id":"x","type":"control","payload":{"job_id":"j","action":"delete","requested_by":"u"}}',
        '{"v":1,"id":"x","type":"dispatch","payload":{"dispatch_id":"d","objective":"x y z",'
        '"requested_by":"u","extra":"field"}}',
        "not json",
    ):
        with pytest.raises(ProtocolError):
            decode(bad, "to_node")
    assert decode(encode(Control(job_id="j", action="cancel", requested_by="u")), "to_node")
