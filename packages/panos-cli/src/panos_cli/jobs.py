"""``panos job``: start and control background jobs (US-050/052, FR-024)."""

import time
from typing import Annotated, Any

import typer

from panos_cli.common import EXIT_ERROR, call, print_json

job_app = typer.Typer(
    help="Start, follow, pause/resume/cancel and roll back jobs.", no_args_is_help=True
)
AsJson = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
_WAITING = {"WAITING_FOR_APPROVAL", "WAITING_FOR_USER", "PAUSED", "BLOCKED"}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _line(job: dict[str, Any]) -> str:
    extra = f" — {job['status_reason']}" if job.get("status_reason") else ""
    phase = f" [{job['phase']}]" if job.get("phase") else ""
    approval = f" (approval {job['pending_approval']})" if job.get("pending_approval") else ""
    return f"{job['id']}  {job['status']:<20}{phase} {job['objective'][:60]}{extra}{approval}"


@job_app.command("start")
def job_start(
    objective: Annotated[list[str], typer.Argument(help="what to do")],
    project: Annotated[str | None, typer.Option("--project", "-p")] = None,
    command: Annotated[
        list[str] | None,
        typer.Option("--cmd", help="run this command as a job (repeat per argument)"),
    ] = None,
    no_commit: Annotated[bool, typer.Option("--no-commit", help="never propose a commit")] = False,
    max_repairs: Annotated[int | None, typer.Option(help="repair attempts limit")] = None,
    as_json: AsJson = False,
) -> None:
    """Start a coding job (default) or, with --cmd, a command job."""
    body: dict[str, Any] = {
        "objective": " ".join(objective),
        "project": project,
        "kind": "command" if command else "coding",
        "commit": not no_commit,
        "argv": command or None,
        "budget": {"max_repair_attempts": max_repairs} if max_repairs is not None else {},
    }
    job = call(lambda c: c.post("/jobs", body))
    if as_json:
        print_json(job)
        return
    typer.echo(f"Started {job['id']} ({job['kind']}). Follow with: panos job watch {job['id']}")


@job_app.command("list")
def job_list(
    status: Annotated[list[str] | None, typer.Option(help="filter by status")] = None,
    as_json: AsJson = False,
) -> None:
    data = call(lambda c: c.get("/jobs", status=status))
    if as_json:
        print_json(data)
        return
    if not data["jobs"]:
        typer.echo("No jobs.")
    for job in data["jobs"]:
        typer.echo(_line(job))


@job_app.command("show")
def job_show(job_id: str, as_json: AsJson = False) -> None:
    """Status, steps and the final report of a job."""
    job = call(lambda c: c.get(f"/jobs/{job_id}"))
    if as_json:
        print_json(job)
        return
    typer.echo(_line(job))
    for step in job["steps"]:
        typer.echo(
            f"  {step['sequence']:>3}. {step['status']:<9} {step['agent'] or '':<16} "
            f"{(step['input_summary'] or '')[:70]}"
            + (f" → {step['output_summary'][:60]}" if step["output_summary"] else "")
        )
    result = job.get("result") or {}
    if result:
        typer.echo(f"\nresult: {'success' if result.get('success') else 'not successful'}")
        for key in ("summary", "reason", "error"):
            if result.get(key):
                typer.echo(f"  {key}: {result[key]}")
        for change in result.get("changed_files", []):
            typer.echo(f"  {change['status']} {change['path']}")
        for check in result.get("verification", []):
            typer.echo(
                f"  {'ok  ' if check['ok'] else 'FAIL'} {check['kind']}: {' '.join(check['argv'])}"
            )
        for issue in result.get("unresolved", []):
            typer.echo(f"  ! {issue}")
        if result.get("commit"):
            typer.echo(f"  commit: {result['commit'].get('status')}")
    if job["checkpoints"]:
        typer.echo(
            f"\ncheckpoints: {', '.join(c['id'] for c in job['checkpoints'])} "
            f"(roll back with: panos job rollback {job['id']})"
        )


@job_app.command("watch")
def job_watch(
    job_id: str,
    interval: Annotated[float, typer.Option(help="seconds between polls")] = 2.0,
    timeout: Annotated[float, typer.Option(help="give up after N seconds")] = 3600.0,
) -> None:
    """Follow a job until it finishes or needs you."""
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        job = call(lambda c: c.get(f"/jobs/{job_id}"))
        line = _line(job)
        if line != last:
            typer.echo(line)
            last = line
        if job["status"] in _TERMINAL or (job["status"] in _WAITING and not job["running"]):
            if job["status"] == "WAITING_FOR_APPROVAL" and job.get("pending_approval"):
                typer.echo(f"Approve with: panos approvals approve {job['pending_approval']}")
            raise typer.Exit(
                0
                if job["status"] == "COMPLETED"
                else EXIT_ERROR
                if job["status"] in {"FAILED", "BLOCKED"}
                else 0
            )
        if time.monotonic() > deadline:
            raise typer.Exit(EXIT_ERROR)
        time.sleep(interval)


def _control(job_id: str, action: str) -> None:
    job = call(lambda c: c.post(f"/jobs/{job_id}/{action}", {}))
    typer.echo(_line(job))


@job_app.command("pause")
def job_pause(job_id: str) -> None:
    """Pause at the next safe point."""
    _control(job_id, "pause")


@job_app.command("resume")
def job_resume(job_id: str) -> None:
    _control(job_id, "resume")


@job_app.command("cancel")
def job_cancel(job_id: str) -> None:
    """Cancel; running processes (and their children) are stopped."""
    _control(job_id, "cancel")


@job_app.command("rollback")
def job_rollback(
    job_id: str,
    checkpoint: Annotated[str | None, typer.Option(help="checkpoint id (default: first)")] = None,
) -> None:
    """Restore the project files to the job's checkpoint (your .git is not touched)."""
    report = call(lambda c: c.post(f"/jobs/{job_id}/rollback", {"checkpoint_id": checkpoint}))
    typer.echo(
        f"Restored {len(report['restored'])} file(s), removed {len(report['deleted'])} "
        f"new file(s); verified: {report['verified']}."
    )
    typer.echo(f"Undo point (shadow commit): {report['safety_reference'][:12]}")


@job_app.command("events")
def job_events(job_id: str, as_json: AsJson = False) -> None:
    """The job's audit trail."""
    events: Any = call(lambda c: c.get(f"/jobs/{job_id}/events"))
    if as_json:
        print_json(events)
        return
    for e in reversed(events):
        typer.echo(f"{e['created_at'][:19]}  {e['event_type']:<20} {e['action']}")
