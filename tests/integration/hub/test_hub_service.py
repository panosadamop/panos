"""Hub service foundation (M9-01/02 setup, ADR-028/029) on PostgreSQL."""

import httpx
import pytest
from cryptography import x509
from sqlalchemy import inspect, text, update

from panos_control_plane import audit
from panos_control_plane.app import create_app
from panos_control_plane.db.orm import HubAudit
from panos_control_plane.material import (
    DATABASE_SECRET,
    PASSPHRASE_SECRET,
    init_hub,
    load_material,
    renew_server_if_needed,
)
from panos_control_plane.state import HubState
from panos_core.config import PanosSettings
from panos_core.remote import pki
from panos_core.secrets import InMemorySecretStore
from tests.integration.hub.conftest import hub_dir_of

pytestmark = pytest.mark.req("BR-024", "NFR-SEC-006")


async def test_migrations_create_the_hub_schema(hub: HubState) -> None:
    async with hub.engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        version = await conn.scalar(text("SELECT version()"))
    assert {"node", "node_cert", "setup_code", "dispatch", "job_mirror", "approval_mirror",
            "hub_audit", "passkey", "hub_session", "hub_user"} <= tables  # fmt: skip
    assert "PostgreSQL" in str(version)


def test_init_creates_encrypted_keys_and_a_stable_ca(settings: PanosSettings) -> None:
    store = InMemorySecretStore()
    first = init_hub(settings, store, hostname="hubpc", addresses=["192.168.1.20"])
    folder = hub_dir_of(settings)
    assert b"ENCRYPTED" in (folder / "ca.key").read_bytes()
    assert b"ENCRYPTED" in (folder / "server.key").read_bytes()
    assert store.get(PASSPHRASE_SECRET) and "panos_hub" in (store.get(DATABASE_SECRET) or "")
    compose = (folder / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:55432:5432" in compose and "POSTGRES_PASSWORD" in compose
    assert (folder / ".env").read_text(encoding="utf-8").startswith("POSTGRES_PASSWORD=")
    assert set(first.hostnames) == {"hubpc", "hubpc.local", "localhost"}
    server = pki.load_cert((folder / "server.crt").read_bytes())
    ips = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {str(a) for a in ips.get_values_for_type(x509.IPAddress)} == {
        "192.168.1.20",
        "127.0.0.1",
    }

    again = init_hub(settings, store, hostname="hubpc")  # re-init keeps the CA
    assert again.fingerprint == first.fingerprint
    assert not renew_server_if_needed(settings, store)
    with pytest.raises(Exception, match="not a hub"):
        load_material(settings, InMemorySecretStore())


async def test_audit_chain_detects_tampering(hub: HubState) -> None:
    async with hub.db() as session, session.begin():
        await audit.record(session, "user", "hub.started")
        await audit.record(session, "user", "node.enrolled", node_id="nod_1",
                           details={"name": "laptop", "api_key": "sk-ant-" + "x" * 30})  # fmt: skip
    async with hub.db() as session:
        check = await audit.verify(session)
        assert check.ok and check.checked == 2
        rows = list(await session.scalars(text("SELECT details FROM hub_audit ORDER BY seq")))
        assert "sk-ant-" not in str(rows)  # redacted before hashing
    async with hub.db() as session, session.begin():
        await session.execute(update(HubAudit).where(HubAudit.seq == 1).values(actor="mallory"))
    async with hub.db() as session:
        broken = await audit.verify(session)
    assert not broken.ok and broken.first_broken_seq == 1


async def test_health_reports_the_ca_fingerprint(hub: HubState) -> None:
    app = create_app(hub)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hub"
    ) as c:
        body = (await c.get("/api/v1/health")).json()
    assert body["status"] == "ok" and body["role"] == "hub"
    assert body["ca_fingerprint"] == hub.material.fingerprint
