"""``panos hub`` (on the hub PC) and ``panos node`` (on every PC): multi-PC setup (M9).

``panos hub`` talks to the running hub on loopback with the local admin token
(``<data>/hub/admin.token``) and verifies the hub certificate against the hub CA.
``panos node`` talks to this PC's node, which joins the hub and keeps the link.
"""

import asyncio
import ssl
from typing import Annotated, Any

import httpx
import typer

from panos_cli.common import EXIT_ERROR, call, print_json
from panos_core.config import load_settings

hub_app = typer.Typer(
    help="Run this PC as the hub (control plane) for your other PCs.", no_args_is_help=True
)
node_app = typer.Typer(help="This PC's membership of a hub.", no_args_is_help=True)


def _control_plane() -> Any:
    try:
        import panos_control_plane.material as material
    except ImportError as exc:  # pragma: no cover - packaging
        typer.echo("the hub component is not installed (panos-control-plane)", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    return material


def _secrets() -> Any:
    from panos_core.secrets import create_secret_store

    return create_secret_store(load_settings().secret_backend)


def _hub(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    settings = load_settings()
    folder = settings.data_dir / "hub"
    token_file = folder / "admin.token"
    if not token_file.exists():
        typer.echo("the hub is not running on this PC (panos hub serve)", err=True)
        raise typer.Exit(EXIT_ERROR)
    ctx = ssl.create_default_context(cadata=(folder / "ca.crt").read_text(encoding="utf-8"))
    url = f"https://localhost:{settings.hub.api_port}/api/v1{path}"
    headers = {"Authorization": f"Bearer {token_file.read_text(encoding='utf-8').strip()}"}
    try:
        with httpx.Client(verify=ctx, timeout=60) as client:
            response = client.request(method, url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        typer.echo(f"cannot reach the hub: {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    if response.status_code >= 400:
        detail = response.json().get("detail", response.text) if response.content else ""
        typer.echo(f"error: {response.status_code} {detail}", err=True)
        raise typer.Exit(EXIT_ERROR)
    return response.json()


# ===================================================================== hub
@hub_app.command("init")
def hub_init(
    hostname: Annotated[
        str | None, typer.Option(help="LAN host name (default: this PC's name)")
    ] = None,
    address: Annotated[
        list[str] | None, typer.Option(help="LAN IP address(es) for the certificate")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option(help="existing PostgreSQL instead of Docker")
    ] = None,
) -> None:
    """Create the hub CA and certificate, and the PostgreSQL setup (once per hub PC)."""
    material = _control_plane()
    settings = load_settings()
    made = material.init_hub(
        settings,
        _secrets(),
        hostname=hostname,
        addresses=address or None,
        database_url=database_url,
    )
    folder = material.hub_dir(settings)
    typer.echo(f"Hub ready in {folder}")
    typer.echo(f"  host names:      {', '.join(made.hostnames)}")
    typer.echo(f"  CA fingerprint:  {made.fingerprint}")
    if database_url is None and (folder / "docker-compose.yml").exists():
        typer.echo(f'Start PostgreSQL:  docker compose -f "{folder / "docker-compose.yml"}" up -d')
    typer.echo("Then:              panos hub serve")


@hub_app.command("serve")
def hub_serve() -> None:
    """Run the hub (dashboard/API on 48700, node link on 48701) until Ctrl+C."""
    _control_plane()
    from panos_control_plane.server import serve

    settings = load_settings()
    typer.echo(f"Panos hub on https://{settings.hub.hostname or 'this-pc'}:{settings.hub.api_port}")
    try:
        asyncio.run(serve(settings, _secrets()))
    except KeyboardInterrupt:
        typer.echo("hub stopped")


@hub_app.command("status")
def hub_status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    health = _hub("GET", "/health")
    summary = _hub("GET", "/summary")
    if as_json:
        print_json({"health": health, "summary": summary})
        return
    typer.echo(
        f"hub {health['version']} up {health['uptime_s']:.0f}s · CA {health['ca_fingerprint']}"
    )
    typer.echo(
        f"nodes online {summary['nodes']['online']}/{summary['nodes']['total']} · "
        f"running jobs {summary['running']} · pending approvals {summary['pending_approvals']}"
    )


@hub_app.command("enroll-code")
def hub_enroll_code(name: Annotated[str, typer.Option(help="a label for the PC")] = "") -> None:
    """A one-time code (10 minutes) for `panos node join` on another PC."""
    out = _hub("POST", "/codes", {"purpose": "enroll", "label": name})
    typer.echo("On the other PC run:")
    typer.echo(f"  panos node join {out['hub_url']} --code {out['code']} \\")
    typer.echo(f"      --fingerprint {out['ca_fingerprint']}")
    typer.echo(f"(valid until {out['expires_at'][:19]}, single use)")


@hub_app.command("passkey-code")
def hub_passkey_code() -> None:
    """A one-time code to register your first dashboard passkey."""
    out = _hub("POST", "/codes", {"purpose": "passkey", "label": "dashboard"})
    typer.echo(f"Open {out['hub_url']} and choose 'Set up a passkey' with the code: {out['code']}")


@hub_app.command("nodes")
def hub_nodes(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    data = _hub("GET", "/nodes")
    if as_json:
        print_json(data)
        return
    if not data:
        typer.echo("No PCs have joined yet (panos hub enroll-code).")
    for n in data:
        seen = (n["last_seen_at"] or "never")[:19]
        typer.echo(
            f"{n['id']}  {n['name']:<16} {n['platform']:<8} {n['status']:<8} seen {seen} "
            f"· {len(n['projects'])} project(s) · running {n['running_jobs']}"
        )


@hub_app.command("revoke")
def hub_revoke(node: Annotated[str, typer.Argument(help="node id or name")]) -> None:
    """Revoke a PC now: its link closes and it can no longer connect."""
    out = _hub("POST", f"/nodes/{node}/revoke")
    typer.echo(f"revoked {out['name']} ({out['id']})")


@hub_app.command("dispatch")
def hub_dispatch(
    node: Annotated[str, typer.Argument(help="target PC (node id or name)")],
    objective: Annotated[list[str], typer.Argument(help="what to do")],
    project: Annotated[str | None, typer.Option("--project", "-p")] = None,
    kind: Annotated[str, typer.Option(help="coding | command | research")] = "coding",
) -> None:
    """Send a job to another PC; it runs there under that PC's policy and approvals."""
    out = _hub(
        "POST",
        "/dispatch",
        {"node": node, "objective": " ".join(objective), "project": project, "kind": kind},
    )
    if out["state"] == "accepted":
        typer.echo(f"{node} accepted it as job {out['job_id']}")
    else:
        typer.echo(f"{node} refused: {out['reason']}", err=True)
        raise typer.Exit(EXIT_ERROR)


@hub_app.command("export-ca")
def hub_export_ca(out: Annotated[str, typer.Argument(help="folder for the backup")]) -> None:
    """Back up the hub CA (certificate + key encrypted with a password you choose)."""
    from pathlib import Path

    from panos_core.remote import pki

    material = _control_plane()
    loaded = material.load_material(load_settings(), _secrets())
    password = typer.prompt("Backup password", hide_input=True, confirmation_prompt=True)
    if len(password) < 12:
        typer.echo("use at least 12 characters", err=True)
        raise typer.Exit(EXIT_ERROR)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "panos-hub-ca.crt").write_bytes(loaded.ca_pem)
    (target / "panos-hub-ca.key").write_bytes(pki.key_to_pem(loaded.ca_key, password.encode()))
    typer.echo(f"wrote {target / 'panos-hub-ca.crt'} and the encrypted key")


# ===================================================================== node
def _line(s: dict[str, Any]) -> str:
    if not s["joined"]:
        return "Not part of a hub (panos node join <hub-url> --code … --fingerprint …)."
    extra = f" — {s['last_error']}" if s["last_error"] and s["link"] != "connected" else ""
    until = (s["certificate_expires"] or "")[:10]
    return f"{s['name']} → {s['hub_url']} · link {s['link']}{extra} · certificate until {until}"


@node_app.command("join")
def node_join(
    hub_url: Annotated[str, typer.Argument(help="https://<hub>:48700")],
    code: Annotated[str, typer.Option(help="from `panos hub enroll-code`")],
    fingerprint: Annotated[str, typer.Option(help="the hub CA fingerprint (SHA256:…)")],
    name: Annotated[str | None, typer.Option(help="how this PC appears on the hub")] = None,
) -> None:
    out = call(
        lambda c: c.post(
            "/remote/join",
            {"hub_url": hub_url, "code": code, "fingerprint": fingerprint, "name": name},
        )
    )
    typer.echo(_line(out))


@node_app.command("status")
def node_status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    out = call(lambda c: c.get("/remote"))
    if as_json:
        print_json(out)
    else:
        typer.echo(_line(out))


@node_app.command("leave")
def node_leave() -> None:
    """Leave the hub and delete this PC's hub certificate and key."""
    call(lambda c: c.post("/remote/leave", {}))
    typer.echo("left the hub")
