"""``panos`` command."""

import json
from typing import Annotated, NoReturn

import httpx
import typer

from panos_cli.ai import ai_app, ask
from panos_cli.client import NodeClient, NodeNotRunningError
from panos_cli.common import (
    EXIT_ALREADY_RUNNING,
    EXIT_ERROR,
    call,
    not_running,
)
from panos_cli.jobs import job_app
from panos_cli.projects import apps_app, project_app
from panos_core import API_VERSION, __version__
from panos_core.config import load_settings
from panos_core.config.settings import default_config_file

app = typer.Typer(
    name="panos",
    help="Panos — local-first AI workstation assistant.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@app.command()
def version() -> None:
    """Print the Panos version."""
    typer.echo(f"panos {__version__} (api {API_VERSION})")


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Loopback port (0 = random free port).")] = 0,
) -> None:
    """Run the local Panos node in the foreground."""
    from panos_node_api.server import AlreadyRunningError
    from panos_node_api.server import serve as run_node

    try:
        run_node(load_settings(), port=port)
    except AlreadyRunningError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_ALREADY_RUNNING) from exc
    except KeyboardInterrupt:
        pass


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show whether the local node is running and healthy."""
    settings = load_settings()
    try:
        client = NodeClient.from_settings(settings)
    except NodeNotRunningError as exc:
        _not_running(str(exc), as_json)
    try:
        health = client.get("/health")
    except NodeNotRunningError as exc:
        _not_running(str(exc), as_json)
    except httpx.HTTPStatusError as exc:
        typer.echo(f"error: node responded {exc.response.status_code}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    finally:
        client.close()

    if as_json:
        typer.echo(json.dumps({"running": True, "url": client.info.base_url, **health}))
    else:
        typer.echo(
            f"Panos node running at {client.info.base_url} (pid {client.info.pid})\n"
            f"  status:   {health['status']}\n"
            f"  node id:  {health['node_id']}\n"
            f"  version:  {health['version']} (api {health['api_version']})\n"
            f"  uptime:   {health['uptime_s']:.0f}s"
        )


def _not_running(message: str, as_json: bool) -> NoReturn:
    not_running(message, as_json)


@config_app.command("show")
def config_show() -> None:
    """Print the effective configuration (contains no secrets)."""
    settings = load_settings()
    typer.echo(settings.model_dump_json(indent=2))


@config_app.command("path")
def config_path() -> None:
    """Print configuration, data, log, runtime and workspace locations."""
    settings = load_settings()
    rows = {
        "config file": default_config_file(),
        "config dir": settings.config_dir,
        "data dir": settings.data_dir,
        "database": settings.database_path,
        "log dir": settings.log_dir,
        "runtime dir": settings.runtime_dir,
        "workspace": settings.workspace_dir,
    }
    width = max(len(k) for k in rows)
    for key, value in rows.items():
        typer.echo(f"{key.ljust(width)}  {value}")


audit_app = typer.Typer(help="Inspect the audit log.", no_args_is_help=True)
approvals_app = typer.Typer(help="Review and decide pending approvals.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
app.add_typer(approvals_app, name="approvals")
app.add_typer(project_app, name="project")
app.add_typer(apps_app, name="apps")
app.add_typer(ai_app, name="ai")
app.add_typer(job_app, name="job")
app.command("ask")(ask)


_call = call


@audit_app.command("list")
def audit_list(
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 20,
    job: Annotated[str | None, typer.Option(help="Filter by job id.")] = None,
    event_type: Annotated[str | None, typer.Option("--type", help="Filter by event type.")] = None,
) -> None:
    """Show recent audit events (newest first)."""
    data = _call(lambda c: c.get("/audit", limit=limit, job_id=job, event_type=event_type))
    for e in data["events"]:
        decision = f" [{e['policy_decision']}]" if e["policy_decision"] else ""
        typer.echo(
            f"{e['created_at']}  {e['event_type']:<22} {e['action']}{decision}  ({e['actor']})"
        )


@audit_app.command("verify")
def audit_verify() -> None:
    """Verify the audit log hash chain (tamper evidence)."""
    data = _call(lambda c: c.get("/audit/verify"))
    if data["ok"]:
        typer.echo(f"audit chain OK ({data['checked']} events)")
        return
    typer.echo(f"audit chain BROKEN at seq {data['first_broken_seq']}: {data['detail']}", err=True)
    raise typer.Exit(EXIT_ERROR)


@approvals_app.command("list")
def approvals_list() -> None:
    """List pending approvals."""
    data = _call(lambda c: c.get("/approvals"))
    if not data["approvals"]:
        typer.echo("no pending approvals")
    for a in data["approvals"]:
        typer.echo(f"{a['id']}  {a['action_type']:<20} {a['summary']}  (expires {a['expires_at']})")


def _decide(approval_id: str, decision: str, comment: str) -> None:
    data = _call(
        lambda c: c.post(
            f"/approvals/{approval_id}/decision", {"decision": decision, "comment": comment}
        )
    )
    typer.echo(f"{approval_id}: {data['approval']['status']}")


@approvals_app.command("approve")
def approvals_approve(approval_id: str, comment: Annotated[str, typer.Option()] = "") -> None:
    """Approve one exact pending action."""
    _decide(approval_id, "approve", comment)


@approvals_app.command("reject")
def approvals_reject(approval_id: str, comment: Annotated[str, typer.Option()] = "") -> None:
    """Reject a pending action."""
    _decide(approval_id, "reject", comment)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
