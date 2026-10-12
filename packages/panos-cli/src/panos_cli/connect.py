"""``panos connect``: link Google, Microsoft, Jira and Azure DevOps accounts (ADR-026).

Secrets (client secret, API token, PAT) are prompted without echo and sent once to the
local node, which keeps them in the OS keyring.
"""

import time
from typing import Annotated, Any

import typer

from panos_cli.common import EXIT_ERROR, call, print_json

connect_app = typer.Typer(
    help="Connected accounts: Gmail/Calendar/Drive, OneDrive/SharePoint, Jira, Azure DevOps.",
    no_args_is_help=True,
)

FLOW_TIMEOUT_S = 600.0
POLL_S = 1.0


def _line(a: dict[str, Any]) -> str:
    scopes = ",".join(s for s in a["scopes"] if "://" not in s)
    return f"{a['id']}  {a['kind']:<12} {a['label']}  [{a['status']}, {a['privacy_mode']}]" + (
        f"  {scopes}" if scopes else ""
    )


@connect_app.command("list")
def connect_list(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List connected accounts."""
    data: list[dict[str, Any]] = call(lambda c: c.get("/connectors"))  # type: ignore[assignment]
    if as_json:
        print_json(data)
        return
    if not data:
        typer.echo("No connected accounts. Try `panos connect google --feature mail`.")
    for account in data:
        typer.echo(_line(account))


@connect_app.command("client")
def connect_client(
    kind: Annotated[str, typer.Argument(help="google | microsoft")],
    client_id: Annotated[str, typer.Option("--id", help="OAuth client id")],
    tenant: Annotated[str | None, typer.Option(help="Microsoft tenant (default: common)")] = None,
) -> None:
    """Register your own OAuth client (see docs/planning/connectors-setup.md)."""
    secret = None
    if kind == "google":
        secret = typer.prompt("Client secret", hide_input=True)
    data = call(
        lambda c: c.put(
            f"/connectors/clients/{kind}",
            {"client_id": client_id, "client_secret": secret, "tenant": tenant},
        )
    )
    typer.echo(f"{kind} client registered: {data['client_id']}")


def _wait(flow: dict[str, Any]) -> None:
    path = f"/connectors/flows/{flow['id']}"
    deadline = time.monotonic() + FLOW_TIMEOUT_S
    while flow["status"] == "pending":
        if time.monotonic() > deadline:
            typer.echo("timed out waiting for sign-in", err=True)
            raise typer.Exit(EXIT_ERROR)
        time.sleep(POLL_S)
        flow = call(lambda c: c.get(path))
    if flow["status"] != "connected":
        typer.echo(f"sign-in failed: {flow['error']}", err=True)
        raise typer.Exit(EXIT_ERROR)
    accounts: list[dict[str, Any]] = call(lambda c: c.get("/connectors"))  # type: ignore[assignment]
    found = [a for a in accounts if a["id"] == flow["account_id"]]
    typer.echo("Connected: " + (_line(found[0]) if found else str(flow["account_id"])))


@connect_app.command("google")
def connect_google(
    feature: Annotated[
        list[str], typer.Option("--feature", "-f", help="mail, calendar, drive (repeatable)")
    ],
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Sign in to Google in the browser (installed-app OAuth with PKCE)."""
    flow = call(
        lambda c: c.post(
            "/connectors/google", {"features": feature, "open_browser": not no_browser}
        )
    )
    typer.echo("Sign in to Google in your browser. If it did not open, visit:")
    typer.echo(flow["auth_url"])
    _wait(flow)


@connect_app.command("microsoft")
def connect_microsoft() -> None:
    """Sign in to Microsoft 365 (OneDrive/SharePoint) with a device code."""
    flow = call(lambda c: c.post("/connectors/microsoft", {}))
    typer.echo(f"Open {flow['verification_uri']} and enter the code {flow['user_code']}")
    _wait(flow)


@connect_app.command("jira")
def connect_jira(
    site: Annotated[str, typer.Argument(help="e.g. acme.atlassian.net")],
    email: Annotated[str, typer.Option(help="your Atlassian account e-mail")],
) -> None:
    """Connect Jira Cloud with an API token."""
    token = typer.prompt("API token", hide_input=True)
    data = call(
        lambda c: c.post("/connectors/jira", {"site_url": site, "email": email, "api_token": token})
    )
    typer.echo("Connected: " + _line(data))


@connect_app.command("azure-devops")
def connect_azure_devops(
    org: Annotated[str, typer.Argument(help="e.g. https://dev.azure.com/acme")],
) -> None:
    """Connect Azure DevOps with a personal access token (Work Items read & write)."""
    pat = typer.prompt("Personal access token", hide_input=True)
    data = call(lambda c: c.post("/connectors/azure-devops", {"org_url": org, "pat": pat}))
    typer.echo("Connected: " + _line(data))


@connect_app.command("privacy")
def connect_privacy(
    ref: Annotated[str, typer.Argument(help="account id or kind")],
    mode: Annotated[str, typer.Argument(help="LOCAL_ONLY | STANDARD")],
) -> None:
    """Change what may be sent to cloud models from this account (default LOCAL_ONLY)."""
    data = call(lambda c: c.patch(f"/connectors/{ref}", {"privacy_mode": mode.upper()}))
    typer.echo(_line(data))


@connect_app.command("remove")
def connect_remove(ref: Annotated[str, typer.Argument(help="account id or kind")]) -> None:
    """Disconnect an account and delete its tokens (Google grants are revoked too)."""
    call(lambda c: c.delete(f"/connectors/{ref}"))
    typer.echo(f"disconnected {ref}")
