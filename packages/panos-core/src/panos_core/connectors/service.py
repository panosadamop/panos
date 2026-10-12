"""Connector accounts: connect, list, use and disconnect (ADR-026, M8-00).

* OAuth client registrations (the Google desktop client, the Microsoft app's client id) are
  the owner's and are stored in the OS keyring (``connector-client:<kind>``), as are all
  account tokens (``connector:<account id>``). The database only has non-secret facts.
* Connecting runs as a background *flow*: the caller gets the Google consent URL (opened in
  the browser) or the Microsoft device code, and polls the flow until it is connected.
* :meth:`client` returns a :class:`ConnectorHttp` bound to the account's allowlist with a
  token provider that refreshes and persists tokens; a failed refresh marks the account
  NEEDS_REAUTH.
"""

import asyncio
import base64
import json
import secrets
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import httpx

from panos_core.audit import AuditService
from panos_core.connectors import allowlists, oauth
from panos_core.connectors.http import AuthExpiredError, ConnectorHttp, ConnectorHttpError
from panos_core.domain.clock import utc_now
from panos_core.domain.connectors import ConnectorAccount, ConnectorKind, ConnectorStatus
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.errors import DomainError
from panos_core.domain.ids import new_id
from panos_core.observability import get_logger
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.secrets import SecretStore

log = get_logger(__name__)

GOOGLE_FEATURES: Final = {
    "mail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        # Needed for drafts. It would also allow sending, which Panos blocks itself: the
        # send endpoints are not on the allowlist and no email.send tool exists (BR-019).
        "https://www.googleapis.com/auth/gmail.compose",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ],
    "drive": ["https://www.googleapis.com/auth/drive.readonly"],
}
MICROSOFT_SCOPES: Final = [
    "offline_access",
    "openid",
    "profile",
    "User.Read",
    "Files.Read.All",
    "Sites.Read.All",
]


class ConnectorError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class ConnectFlow:
    id: str
    kind: ConnectorKind
    status: str = "pending"  # pending | connected | failed
    auth_url: str | None = None  # Google: open this in a browser
    user_code: str | None = None  # Microsoft: enter this code …
    verification_uri: str | None = None  # … at this address
    account_id: str | None = None
    error: str | None = None
    task: "asyncio.Task[None] | None" = field(default=None, repr=False)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "status": self.status,
            "auth_url": self.auth_url,
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "account_id": self.account_id,
            "error": self.error,
        }


def _basic(user: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()


class ConnectorService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        secrets_store: SecretStore,
        audit: AuditService,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        open_url: Callable[[str], object] = webbrowser.open,
    ) -> None:
        self._uow_factory = uow_factory
        self._secrets = secrets_store
        self._audit = audit
        self._transport = transport  # tests inject a mock transport
        self._open_url = open_url
        self._flows: dict[str, ConnectFlow] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ client registrations
    def set_client(
        self,
        kind: ConnectorKind,
        client_id: str,
        client_secret: str | None = None,
        *,
        tenant: str | None = None,
    ) -> None:
        if kind not in (ConnectorKind.GOOGLE, ConnectorKind.MICROSOFT):
            raise ConnectorError("invalid", f"{kind.value} has no OAuth client registration")
        if kind == ConnectorKind.GOOGLE and not client_secret:
            raise ConnectorError("invalid", "a Google desktop client needs its client secret too")
        value = {
            "client_id": client_id.strip(),
            "client_secret": client_secret,
            "tenant": tenant or "common",
        }
        self._secrets.set(f"connector-client:{kind.value}", json.dumps(value))

    def client_registration(self, kind: ConnectorKind) -> dict[str, Any] | None:
        raw = self._secrets.get(f"connector-client:{kind.value}")
        return json.loads(raw) if raw else None

    def _registration(self, kind: ConnectorKind) -> dict[str, Any]:
        reg = self.client_registration(kind)
        if reg is None:
            hint = (
                "panos connect google-client <client-id>"
                if kind == ConnectorKind.GOOGLE
                else "panos connect microsoft-client <client-id>"
            )
            raise ConnectorError(
                "conflict",
                f"no {kind.value} OAuth client is configured; run `{hint}` first "
                "(see docs/planning/connectors-setup.md)",
            )
        return reg

    def _http(self, endpoints: tuple[Any, ...], auth: Any = None) -> ConnectorHttp:
        return ConnectorHttp(endpoints, auth, transport=self._transport)

    # ------------------------------------------------------------------ connect: Google
    async def start_google(
        self, features: list[str], *, actor: str = "user", open_browser: bool = True
    ) -> ConnectFlow:
        unknown = [f for f in features if f not in GOOGLE_FEATURES]
        if unknown or not features:
            raise ConnectorError("invalid", f"choose features from {sorted(GOOGLE_FEATURES)}")
        reg = self._registration(ConnectorKind.GOOGLE)
        scopes = ["openid", "email"] + [s for f in features for s in GOOGLE_FEATURES[f]]
        state = secrets.token_urlsafe(24)
        verifier, challenge = oauth.pkce_pair()
        receiver = oauth.LoopbackReceiver(state)
        redirect = await receiver.start()
        flow = ConnectFlow(
            id=new_id("connector").replace("con_", "flow_"), kind=ConnectorKind.GOOGLE
        )
        flow.auth_url = oauth.google_auth_url(reg["client_id"], redirect, scopes, state, challenge)

        async def finish() -> None:
            http = self._http(allowlists.GOOGLE_OAUTH)
            try:
                code = await receiver.wait()
                tokens = await oauth.google_exchange(
                    http,
                    client_id=reg["client_id"],
                    client_secret=reg["client_secret"],
                    code=code,
                    verifier=verifier,
                    redirect_uri=redirect,
                )
                info = await ConnectorHttp(
                    allowlists.GOOGLE_OAUTH,
                    _static(f"Bearer {tokens.access_token}"),
                    transport=self._transport,
                ).request("GET", oauth.GOOGLE_USERINFO_URL)
                label = str((info or {}).get("email") or "Google account")
                account = await self._save_new(
                    ConnectorKind.GOOGLE, label, None, features, tokens.as_dict(), actor
                )
                flow.account_id, flow.status = account.id, "connected"
            except Exception as exc:  # reported through the flow
                flow.status, flow.error = "failed", str(exc)[:300]
                log.warning("connector_flow_failed", kind="google", error=type(exc).__name__)
            finally:
                await receiver.close()
                await http.aclose()

        flow.task = asyncio.create_task(finish())
        self._flows[flow.id] = flow
        if open_browser:
            try:
                self._open_url(flow.auth_url)
            except Exception:  # headless: the caller shows the URL instead
                log.info("browser_open_failed")
        return flow

    # ------------------------------------------------------------------ connect: Microsoft
    async def start_microsoft(self, *, actor: str = "user") -> ConnectFlow:
        reg = self._registration(ConnectorKind.MICROSOFT)
        tenant = reg.get("tenant") or "common"
        http = self._http(allowlists.MICROSOFT_OAUTH)
        try:
            device = await oauth.microsoft_device_code(
                http, tenant=tenant, client_id=reg["client_id"], scopes=MICROSOFT_SCOPES
            )
        except oauth.OAuthError as exc:
            await http.aclose()
            raise ConnectorError("upstream", f"Microsoft sign-in could not start: {exc}") from exc
        flow = ConnectFlow(
            id=new_id("connector").replace("con_", "flow_"),
            kind=ConnectorKind.MICROSOFT,
            user_code=device.user_code,
            verification_uri=device.verification_uri,
        )

        async def finish() -> None:
            try:
                tokens = await oauth.microsoft_poll(
                    http, tenant=tenant, client_id=reg["client_id"], device=device
                )
                me = await ConnectorHttp(
                    allowlists.MICROSOFT_GRAPH,
                    _static(f"Bearer {tokens.access_token}"),
                    transport=self._transport,
                ).request("GET", "https://graph.microsoft.com/v1.0/me")
                label = str(
                    (me or {}).get("userPrincipalName")
                    or (me or {}).get("mail")
                    or "Microsoft account"
                )
                account = await self._save_new(
                    ConnectorKind.MICROSOFT, label, None, ["files"], tokens.as_dict(), actor
                )
                flow.account_id, flow.status = account.id, "connected"
            except Exception as exc:
                flow.status, flow.error = "failed", str(exc)[:300]
                log.warning("connector_flow_failed", kind="microsoft", error=type(exc).__name__)
            finally:
                await http.aclose()

        flow.task = asyncio.create_task(finish())
        self._flows[flow.id] = flow
        return flow

    def flow(self, flow_id: str) -> ConnectFlow:
        try:
            return self._flows[flow_id]
        except KeyError:
            raise ConnectorError("not_found", f"no connect flow {flow_id}") from None

    # ------------------------------------------------------------------ connect: tokens
    async def connect_jira(
        self, site_url: str, email: str, api_token: str, *, actor: str = "user"
    ) -> ConnectorAccount:
        site = _https_base(site_url)
        auth = _basic(email, api_token)
        http = ConnectorHttp(allowlists.jira(site), _static(auth), transport=self._transport)
        try:
            me = await http.request("GET", f"{site}/rest/api/3/myself")
        except ConnectorHttpError as exc:
            raise ConnectorError("upstream", f"Jira rejected the credentials: {exc}") from exc
        finally:
            await http.aclose()
        label = f"{(me or {}).get('emailAddress') or email} @ {site.removeprefix('https://')}"
        secret = {"type": "basic", "username": email, "token": api_token}
        return await self._save_new(ConnectorKind.JIRA, label, site, ["workitems"], secret, actor)

    async def connect_azure_devops(
        self, org_url: str, pat: str, *, actor: str = "user"
    ) -> ConnectorAccount:
        org = _https_base(org_url)
        http = ConnectorHttp(
            allowlists.azure_devops(org), _static(_basic("", pat)), transport=self._transport
        )
        try:
            data = await http.request("GET", f"{org}/_apis/connectionData")
        except ConnectorHttpError as exc:
            raise ConnectorError("upstream", f"Azure DevOps rejected the token: {exc}") from exc
        finally:
            await http.aclose()
        user = ((data or {}).get("authenticatedUser") or {}).get("providerDisplayName") or "user"
        secret = {"type": "basic", "username": "", "token": pat}
        return await self._save_new(
            ConnectorKind.AZURE_DEVOPS,
            f"{user} @ {org.removeprefix('https://')}",
            org,
            ["workitems"],
            secret,
            actor,
        )

    async def _save_new(
        self,
        kind: ConnectorKind,
        label: str,
        base_url: str | None,
        scopes: list[str],
        secret: dict[str, Any],
        actor: str,
    ) -> ConnectorAccount:
        account = ConnectorAccount(kind=kind, label=label[:256], base_url=base_url, scopes=scopes)
        self._secrets.set(account.secret_name, json.dumps(secret))
        async with self._uow_factory() as uow:
            await uow.connectors.save_account(account)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="connector.connected",
            action="connector.connect",
            metadata={
                "account": account.id,
                "kind": kind.value,
                "label": account.label,
                "scopes": scopes,
            },
        )
        return account

    # ------------------------------------------------------------------ accounts
    async def accounts(self, kind: ConnectorKind | None = None) -> list[ConnectorAccount]:
        async with self._uow_factory() as uow:
            return list(await uow.connectors.accounts(kind.value if kind else None))

    async def get(self, ref: str) -> ConnectorAccount:
        """By id, or by kind when exactly one account of that kind exists."""
        async with self._uow_factory() as uow:
            account = await uow.connectors.get_account(ref)
            if account is None and ref in ConnectorKind._value2member_map_:
                matches = list(await uow.connectors.accounts(ref))
                if len(matches) > 1:
                    raise ConnectorError(
                        "conflict", f"several {ref} accounts are connected; pass the account id"
                    )
                account = matches[0] if matches else None
        if account is None:
            raise ConnectorError("not_found", f"no connected account {ref!r}")
        return account

    async def first(self, kind: ConnectorKind, feature: str | None = None) -> ConnectorAccount:
        for account in await self.accounts(kind):
            if account.status != ConnectorStatus.DISCONNECTED and (
                feature is None or feature in account.scopes
            ):
                return account
        what = f"{kind.value} account with {feature}" if feature else f"{kind.value} account"
        raise ConnectorError(
            "conflict", f"no {what} is connected (panos connect {kind.value.replace('_', '-')})"
        )

    async def set_privacy(
        self, ref: str, mode: PrivacyMode, *, actor: str = "user"
    ) -> ConnectorAccount:
        account = await self.get(ref)
        account.privacy_mode = mode
        async with self._uow_factory() as uow:
            await uow.connectors.save_account(account)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="connector.privacy_changed",
            action="connector.privacy",
            metadata={"account": account.id, "privacy": mode.value},
        )
        return account

    async def disconnect(self, ref: str, *, actor: str = "user") -> None:
        account = await self.get(ref)
        tokens = self._load_secret(account)
        if account.kind == ConnectorKind.GOOGLE and tokens and tokens.get("refresh_token"):
            http = self._http(allowlists.GOOGLE_OAUTH)
            try:  # best effort: revoke at Google so the grant disappears there too
                await http.request_form(
                    "POST", oauth.GOOGLE_REVOKE_URL, {"token": tokens["refresh_token"]}
                )
            except ConnectorHttpError:
                log.info("google_revoke_failed", account=account.id)
            finally:
                await http.aclose()
        self._secrets.delete(account.secret_name)
        async with self._uow_factory() as uow:
            await uow.connectors.delete_account(account.id)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="connector.disconnected",
            action="connector.disconnect",
            metadata={"account": account.id, "kind": account.kind.value, "label": account.label},
        )

    # ------------------------------------------------------------------ using an account
    def _load_secret(self, account: ConnectorAccount) -> dict[str, Any] | None:
        raw = self._secrets.get(account.secret_name)
        return json.loads(raw) if raw else None

    def client(self, account: ConnectorAccount) -> ConnectorHttp:
        """An allowlisted HTTP client authenticated as this account."""
        return self._http(
            allowlists.for_account(account.kind.value, account.base_url),
            self._auth_provider(account),
        )

    def _auth_provider(self, account: ConnectorAccount) -> Any:
        lock = self._locks.setdefault(account.id, asyncio.Lock())

        async def provide(refresh: bool) -> str:
            async with lock:
                secret = self._load_secret(account)
                if secret is None:
                    await self._mark(account, ConnectorStatus.NEEDS_REAUTH)
                    raise AuthExpiredError("the account's credentials are missing; reconnect it")
                if secret.get("type") == "basic":
                    if refresh:  # nothing to refresh: the token itself was rejected
                        await self._mark(account, ConnectorStatus.NEEDS_REAUTH)
                        raise AuthExpiredError("the API token was rejected; reconnect the account")
                    return _basic(secret["username"], secret["token"])
                tokens = oauth.TokenSet(**secret)
                if refresh or tokens.expired():
                    try:
                        tokens = await self._refresh(account, tokens)
                    except oauth.OAuthError as exc:
                        await self._mark(account, ConnectorStatus.NEEDS_REAUTH)
                        raise AuthExpiredError(
                            f"token refresh failed; reconnect the account ({exc})"
                        ) from exc
                    self._secrets.set(account.secret_name, json.dumps(tokens.as_dict()))
                return f"Bearer {tokens.access_token}"

        return provide

    async def _refresh(self, account: ConnectorAccount, tokens: oauth.TokenSet) -> oauth.TokenSet:
        reg = self._registration(account.kind)
        if account.kind == ConnectorKind.GOOGLE:
            http = self._http(allowlists.GOOGLE_OAUTH)
            try:
                return await oauth.google_refresh(
                    http,
                    client_id=reg["client_id"],
                    client_secret=reg["client_secret"],
                    tokens=tokens,
                )
            finally:
                await http.aclose()
        http = self._http(allowlists.MICROSOFT_OAUTH)
        try:
            return await oauth.microsoft_refresh(
                http,
                tenant=reg.get("tenant") or "common",
                client_id=reg["client_id"],
                tokens=tokens,
            )
        finally:
            await http.aclose()

    async def _mark(self, account: ConnectorAccount, status: ConnectorStatus) -> None:
        account.status = status
        async with self._uow_factory() as uow:
            await uow.connectors.save_account(account)
            await uow.commit()

    async def touch(self, account: ConnectorAccount) -> None:
        account.last_used_at = utc_now()
        if account.status == ConnectorStatus.NEEDS_REAUTH:
            return
        async with self._uow_factory() as uow:
            await uow.connectors.save_account(account)
            await uow.commit()

    async def close(self) -> None:
        for flow in self._flows.values():
            if flow.task is not None and not flow.task.done():
                flow.task.cancel()


def _static(value: str) -> Any:
    async def provide(_refresh: bool) -> str:
        return value

    return provide


def _https_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        if "://" in url:
            raise ConnectorError("invalid", "only https:// service URLs are accepted")
        url = "https://" + url
    return url


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
