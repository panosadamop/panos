"""Connector accounts (ADR-026, M8-00): OAuth client registration, connect flows, privacy.

Secrets travel only inbound (client secret, API token, PAT) and go straight to the OS
keyring; no response ever contains one.
"""

from typing import Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from panos_core.connectors.service import ConnectorError
from panos_core.domain.connectors import ConnectorAccount, ConnectorKind
from panos_core.domain.enums import PrivacyMode
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(prefix="/connectors", tags=["connectors"])

GoogleFeature = Literal["mail", "calendar", "drive"]  # = GOOGLE_FEATURES (tested)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountView(BaseModel):
    id: str
    kind: str
    label: str
    base_url: str | None
    scopes: list[str]
    privacy_mode: str
    status: str
    created_at: str
    last_used_at: str | None


def _view(a: ConnectorAccount) -> AccountView:
    return AccountView(
        id=a.id,
        kind=a.kind.value,
        label=a.label,
        base_url=a.base_url,
        scopes=a.scopes,
        privacy_mode=a.privacy_mode.value,
        status=a.status.value,
        created_at=a.created_at.isoformat(),
        last_used_at=a.last_used_at.isoformat() if a.last_used_at else None,
    )


class FlowView(BaseModel):
    id: str
    kind: str
    status: Literal["pending", "connected", "failed"]
    auth_url: str | None
    user_code: str | None
    verification_uri: str | None
    account_id: str | None
    error: str | None


class ClientView(BaseModel):
    kind: str
    registered: bool
    client_id: str | None = None  # not secret; shown so the owner can recognise it
    tenant: str | None = None


class ClientIn(_In):
    client_id: str = Field(min_length=4, max_length=256)
    client_secret: str | None = Field(default=None, max_length=512)
    tenant: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9.-]+$")


class GoogleIn(_In):
    features: list[GoogleFeature] = Field(min_length=1)
    open_browser: bool = True


class JiraIn(_In):
    site_url: str = Field(min_length=3, max_length=255, examples=["acme.atlassian.net"])
    email: str = Field(min_length=3, max_length=255)
    api_token: str = Field(min_length=8, max_length=512)


class AzureDevOpsIn(_In):
    org_url: str = Field(min_length=3, max_length=255, examples=["https://dev.azure.com/acme"])
    pat: str = Field(min_length=8, max_length=512)


class AccountPatch(_In):
    privacy_mode: PrivacyMode


OAuthKind = Literal["google", "microsoft"]


@router.get("", response_model=list[AccountView])
async def list_accounts(container: Container) -> Any:
    return [_view(a) for a in await container.connectors.accounts()]


@router.get("/clients/{kind}", response_model=ClientView)
async def get_client(kind: OAuthKind, container: Container) -> Any:
    reg = container.connectors.client_registration(ConnectorKind(kind))
    if reg is None:
        return ClientView(kind=kind, registered=False)
    return ClientView(kind=kind, registered=True, client_id=reg["client_id"], tenant=reg["tenant"])


@router.put("/clients/{kind}", response_model=ClientView)
async def set_client(kind: OAuthKind, body: ClientIn, container: Container) -> Any:
    try:
        container.connectors.set_client(
            ConnectorKind(kind), body.client_id, body.client_secret, tenant=body.tenant
        )
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    await container.audit.record(
        actor=LOCAL_USER,
        event_type="connector.client_registered",
        action="connector.client",
        metadata={"kind": kind, "client_id": body.client_id},
    )
    return await get_client(kind, container)


@router.post("/google", response_model=FlowView, status_code=status.HTTP_202_ACCEPTED)
async def connect_google(body: GoogleIn, container: Container) -> Any:
    try:
        flow = await container.connectors.start_google(
            list(body.features), actor=LOCAL_USER, open_browser=body.open_browser
        )
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    return flow.view()


@router.post("/microsoft", response_model=FlowView, status_code=status.HTTP_202_ACCEPTED)
async def connect_microsoft(container: Container) -> Any:
    try:
        flow = await container.connectors.start_microsoft(actor=LOCAL_USER)
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    return flow.view()


@router.get("/flows/{flow_id}", response_model=FlowView)
async def get_flow(flow_id: str, container: Container) -> Any:
    try:
        return container.connectors.flow(flow_id).view()
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.post("/jira", response_model=AccountView, status_code=status.HTTP_201_CREATED)
async def connect_jira(body: JiraIn, container: Container) -> Any:
    try:
        account = await container.connectors.connect_jira(
            body.site_url, body.email, body.api_token, actor=LOCAL_USER
        )
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(account)


@router.post("/azure-devops", response_model=AccountView, status_code=status.HTTP_201_CREATED)
async def connect_azure_devops(body: AzureDevOpsIn, container: Container) -> Any:
    try:
        account = await container.connectors.connect_azure_devops(
            body.org_url, body.pat, actor=LOCAL_USER
        )
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(account)


@router.patch("/{ref}", response_model=AccountView)
async def update_account(ref: str, body: AccountPatch, container: Container) -> Any:
    try:
        account = await container.connectors.set_privacy(ref, body.privacy_mode, actor=LOCAL_USER)
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(account)


@router.delete("/{ref}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(ref: str, container: Container) -> None:
    try:
        await container.connectors.disconnect(ref, actor=LOCAL_USER)
    except ConnectorError as exc:
        raise_for_reason(exc.reason, str(exc))
