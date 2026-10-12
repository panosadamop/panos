"""``panos effort``: log hours, review imports, learn better estimates (BR-015, ADR-031)."""

import re
from typing import Annotated, Any

import typer

from panos_cli.common import EXIT_ERROR, call, print_json

effort_app = typer.Typer(
    help="Estimated versus actual effort, and estimate calibration.", no_args_is_help=True
)
Project = Annotated[str, typer.Option("--project", "-p", help="project name or id")]
AsJson = Annotated[bool, typer.Option("--json")]


def _hours(text: str) -> float:
    """``2.5``, ``2.5h``, ``90m`` or ``1h30m``."""
    m = re.fullmatch(r"\s*(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m)?\s*", text.lower())
    if m and (m.group(1) or m.group(2)):
        return float(m.group(1) or 0) + int(m.group(2) or 0) / 60
    try:
        return float(text)
    except ValueError as exc:
        raise typer.BadParameter("use hours like 2.5, 2.5h, 90m or 1h30m") from exc


def _line(a: dict[str, Any]) -> str:
    what = (
        f"{a['hours']:g} h {a['role']}"
        if a["source"] != "job"
        else f"{a['machine'].get('runtime_s', 0):.0f} s, "
        f"{int(a['machine'].get('tokens', 0))} tokens"
    )
    done = " (finished)" if a["completes"] else ""
    head = f"{a['id']}  {a['work_date']}  {a['requirement_key']:<10} {a['source']:<8}"
    return f"{head} {what}{done}  [{a['status']}]"


@effort_app.command("log")
def log(
    key: Annotated[str, typer.Argument(help="requirement key, e.g. US-003")],
    hours: Annotated[str, typer.Argument(help="2.5, 2.5h, 90m or 1h30m")],
    project: Project,
    role: Annotated[str, typer.Option(help="developer | qa | analyst …")] = "developer",
    date: Annotated[str | None, typer.Option(help="YYYY-MM-DD (default today)")] = None,
    note: Annotated[str, typer.Option()] = "",
    done: Annotated[bool, typer.Option("--done", help="the story is finished with this")] = False,
) -> None:
    """Log time you spent on a requirement."""
    body = {
        "requirement_key": key,
        "hours": round(_hours(hours), 2),
        "role": role,
        "work_date": date,
        "note": note,
        "completes": done,
    }
    typer.echo(_line(call(lambda c: c.post(f"/projects/{project}/effort", body))))


@effort_app.command("list")
def list_actuals(
    project: Project,
    status: Annotated[str | None, typer.Option(help="PENDING | APPROVED | REJECTED")] = None,
    as_json: AsJson = False,
) -> None:
    data: Any = call(lambda c: c.get(f"/projects/{project}/effort", status=status))
    if as_json:
        print_json(data)
        return
    if not data:
        typer.echo("Nothing logged yet.")
    for a in data:
        typer.echo(_line(a))


@effort_app.command("import")
def import_tracker(project: Project) -> None:
    """Import worklogs of linked Jira/Azure DevOps items as pending entries."""
    out = call(lambda c: c.post(f"/projects/{project}/effort/import", {}))
    typer.echo(
        f"{out['imported']} imported, {out['skipped']} already known, {out['items']} linked item(s)"
    )
    for error in out["errors"]:
        typer.echo(f"  ! {error}", err=True)
    if out["imported"]:
        typer.echo(f"Review with: panos effort list -p {project} --status PENDING")


def _decide(actual_id: str, decision: str) -> None:
    out = call(lambda c: c.post(f"/effort/{actual_id}/decision", {"decision": decision}))
    typer.echo(_line(out))


@effort_app.command("approve")
def approve(actual_id: str) -> None:
    _decide(actual_id, "approve")


@effort_app.command("reject")
def reject(actual_id: str) -> None:
    _decide(actual_id, "reject")


@effort_app.command("baseline")
def baseline(project: Project) -> None:
    """Estimate now and keep it as the baseline actuals are compared with."""
    out = call(lambda c: c.post(f"/projects/{project}/estimate/baseline", {}))
    typer.echo(f"baseline: {out['total_points']} points, {out['total_hours']} h")
    for assumption in out["assumptions"]:
        typer.echo(f"  - {assumption}")


@effort_app.command("report")
def report(project: Project, as_json: AsJson = False) -> None:
    out = call(lambda c: c.get(f"/projects/{project}/effort/report"))
    if as_json:
        print_json(out)
        return
    typer.echo(
        f"{out['project']}: {out['logged_hours']} h logged, {out['pending']} pending review, "
        f"{out['baselines']} baseline(s)"
    )
    for f in out["finished"]:
        typer.echo(
            f"  {f['key']:<10} estimated {f['estimated_hours']:g} h, "
            f"actual {f['actual_hours']:g} h (× {f['ratio']:.2f})"
        )
    m = out["machine"]
    typer.echo(
        f"Panos jobs: {m['jobs']} · {m['runtime_s']:.0f} s · {m['tokens']} tokens · "
        f"${m['cost_usd']:.4f}"
    )


def _cal(c: dict[str, Any]) -> str:
    return (
        f"{c['id']}  {c['scope']:<24} {c['hours_per_point']:g} h/point "
        f"(was {c['base_hours_per_point']:g}) from {c['stories']} stories · "
        f"bias {c['bias']:+.0%} · MAPE {c['mape']:.0%}  [{c['status']}]"
    )


@effort_app.command("suggest")
def suggest() -> None:
    """Suggest calibrated hours-per-point from approved, finished stories (needs approval)."""
    out: Any = call(lambda c: c.post("/effort/calibrations/suggest", {}))
    if not out:
        typer.echo("Not enough finished stories with approved hours yet.")
    for c in out:
        typer.echo(_cal(c))
    if out:
        typer.echo("Approve with: panos effort calibration approve <id>")


calibration_app = typer.Typer(help="Calibration suggestions.", no_args_is_help=True)
effort_app.add_typer(calibration_app, name="calibration")


@calibration_app.command("list")
def calibrations() -> None:
    out: Any = call(lambda c: c.get("/effort/calibrations"))
    for c in out:
        typer.echo(_cal(c))


@calibration_app.command("approve")
def calibration_approve(calibration_id: str) -> None:
    typer.echo(
        _cal(
            call(
                lambda c: c.post(
                    f"/effort/calibrations/{calibration_id}/decision", {"decision": "approve"}
                )
            )
        )
    )


@calibration_app.command("reject")
def calibration_reject(calibration_id: str) -> None:
    out = call(
        lambda c: c.post(f"/effort/calibrations/{calibration_id}/decision", {"decision": "reject"})
    )
    if out["status"] != "REJECTED":
        raise typer.Exit(EXIT_ERROR)
    typer.echo(_cal(out))
