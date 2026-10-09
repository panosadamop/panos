"""``panos new``, ``panos interview`` and ``panos req``: the requirements pipeline (M6)."""

from typing import Annotated, Any

import typer

from panos_cli.common import call, print_json

interview_app = typer.Typer(help="Answer the requirements interview.", no_args_is_help=True)
req_app = typer.Typer(help="Requirements: list, edit, approve, trace.", no_args_is_help=True)
AsJson = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
Project = Annotated[str, typer.Option("--project", "-p", help="project id or name")]


def _show_interview(data: dict[str, Any]) -> None:
    typer.echo(
        f"Interview {data['id']} ({data['status']})"
        + (f" · stack requested: {data['explicit_stack']}" if data["explicit_stack"] else "")
    )
    for amb in data["ambiguities"]:
        typer.echo(f"  ? {amb}")
    for q in data["questions"]:
        state = (
            f"→ {q['answer']}" if q.get("answer") else "(skipped)" if q.get("skipped") else "(open)"
        )
        typer.echo(f"  {q['id']}. {q['text']} {state}")
    if data["status"] == "READY":
        typer.echo(f"All answered. Next: panos interview complete {data['id']}")


def new(
    brief: Annotated[list[str], typer.Argument(help="describe the project")],
    name: Annotated[str, typer.Option("--name", "-n", help="project name")],
    as_json: AsJson = False,
) -> None:
    """Start a new project from a brief: Panos asks clarifying questions first."""
    data = call(lambda c: c.post("/interviews", {"brief": " ".join(brief), "name": name}))
    if as_json:
        print_json(data)
    else:
        _show_interview(data)


@interview_app.command("show")
def interview_show(interview_id: str, as_json: AsJson = False) -> None:
    data = call(lambda c: c.get(f"/interviews/{interview_id}"))
    if as_json:
        print_json(data)
    else:
        _show_interview(data)


@interview_app.command("answer")
def interview_answer(
    interview_id: str,
    question_id: str,
    answer: Annotated[list[str] | None, typer.Argument()] = None,
    skip: Annotated[bool, typer.Option("--skip", help="skip (becomes an assumption)")] = False,
) -> None:
    text = None if skip else " ".join(answer or [])
    if not skip and not text:
        typer.echo("give an answer or --skip", err=True)
        raise typer.Exit(2)
    _show_interview(
        call(
            lambda c: c.post(
                f"/interviews/{interview_id}/answers", {"question_id": question_id, "answer": text}
            )
        )
    )


@interview_app.command("complete")
def interview_complete(interview_id: str, as_json: AsJson = False) -> None:
    """Generate the draft specification and the stack decision."""
    items: Any = call(lambda c: c.post(f"/interviews/{interview_id}/complete", {}))
    if as_json:
        print_json(items)
        return
    typer.echo(f"Created {len(items)} draft item(s).")
    for item in items:
        if item["type"] in ("BR", "FR", "ADR"):
            typer.echo(f"  {item['key']:<12} {item['title']}")
    typer.echo("Review with `panos req list -p <project>`, approve with `panos req approve`.")


def _line(r: dict[str, Any]) -> str:
    stale = " STALE" if r["stale"] else ""
    return f"{r['key']:<14} v{r['version']:<3} {r['status']:<16}{stale} {r['title'][:70]}"


@req_app.command("list")
def req_list(
    project: Project,
    type: Annotated[str | None, typer.Option("--type", "-t")] = None,
    status: Annotated[str | None, typer.Option("--status", "-s")] = None,
    as_json: AsJson = False,
) -> None:
    data: Any = call(lambda c: c.get(f"/projects/{project}/requirements", type=type, status=status))
    if as_json:
        print_json(data)
        return
    for r in data:
        typer.echo(_line(r))
    if not data:
        typer.echo("No requirements.")


@req_app.command("show")
def req_show(project: Project, key: str, as_json: AsJson = False) -> None:
    data = call(lambda c: c.get(f"/projects/{project}/requirements/{key}"))
    if as_json:
        print_json(data)
        return
    typer.echo(_line(data))
    if data["body"]:
        typer.echo(f"\n{data['body']}\n")
    if data["source"]:
        typer.echo("sources: " + ", ".join(data["source"]))
    for a in data["approvals"]:
        via = " (cascaded)" if a["cascaded_from_id"] else ""
        typer.echo(
            f"  v{a['version']} {a['decision']} by {a['decided_by']} "
            f"{a['decided_at'][:19]}{via} {a['comments']}"
        )


@req_app.command("add")
def req_add(
    project: Project,
    type: str,
    title: str,
    parent: Annotated[str | None, typer.Option("--parent")] = None,
    body: Annotated[str, typer.Option("--body")] = "",
) -> None:
    data = call(
        lambda c: c.post(
            f"/projects/{project}/requirements",
            {"type": type.upper(), "title": title, "body": body, "parent_key": parent},
        )
    )
    typer.echo(_line(data))


@req_app.command("edit")
def req_edit(
    project: Project,
    key: str,
    title: Annotated[str | None, typer.Option("--title")] = None,
    body: Annotated[str | None, typer.Option("--body")] = None,
) -> None:
    """Create a new version (approved items: downstream becomes stale)."""
    data = call(
        lambda c: c.patch(f"/projects/{project}/requirements/{key}", {"title": title, "body": body})
    )
    typer.echo(_line(data["requirement"]))
    if data["stale"]:
        typer.echo(f"Marked stale ({len(data['stale'])}): {', '.join(data['stale'])}")
        typer.echo("An impact-analysis job was created (see `panos job list`).")


@req_app.command("approve")
def req_approve(
    project: Project,
    key: str,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", "-x")] = None,
    no_cascade: Annotated[bool, typer.Option("--no-cascade")] = False,
    comment: Annotated[str, typer.Option("--comment")] = "",
) -> None:
    """Approve an item and (by default) its descendants, except --exclude subtrees."""
    records: Any = call(
        lambda c: c.post(
            f"/projects/{project}/requirements/{key}/approval",
            {
                "decision": "approve",
                "comments": comment,
                "cascade": not no_cascade,
                "exclude": exclude or [],
            },
        )
    )
    typer.echo(f"Approved {len(records)} item(s): " + ", ".join(r["key"] for r in records))


@req_app.command("reject")
def req_reject(
    project: Project, key: str, comment: Annotated[str, typer.Option("--comment")] = ""
) -> None:
    call(
        lambda c: c.post(
            f"/projects/{project}/requirements/{key}/approval",
            {"decision": "reject", "comments": comment},
        )
    )
    typer.echo(f"Rejected {key}.")


@req_app.command("clear-stale")
def req_clear_stale(
    project: Project, keys: list[str], reason: Annotated[str, typer.Option("--reason")]
) -> None:
    data = call(
        lambda c: c.post(
            f"/projects/{project}/requirements/clear-stale", {"keys": keys, "reason": reason}
        )
    )
    typer.echo("Cleared: " + (", ".join(data["cleared"]) or "nothing"))


@req_app.command("link")
def req_link(project: Project, source: str, relation: str, target: str) -> None:
    """e.g. `panos req link -p shop TC-004 verifies AC-US002-01`."""
    call(
        lambda c: c.post(
            f"/projects/{project}/links",
            {"source_key": source, "target": target, "relation": relation},
        )
    )
    typer.echo(f"{source} {relation} {target}")


@req_app.command("trace")
def req_trace(
    project: Project, root: Annotated[str | None, typer.Option()] = None, as_json: AsJson = False
) -> None:
    """Traceability from business requirements down to tests and evidence, with gaps."""
    data = call(lambda c: c.get(f"/projects/{project}/traceability", root=root))
    if as_json:
        print_json(data)
        return

    def show(node: dict[str, Any], depth: int) -> None:
        flags = (" STALE" if node["stale"] else "") + (
            f" ← {', '.join(link['key'] for link in node['links'])}" if node["links"] else ""
        )
        evidence = f" ✓ {', '.join(node['evidence'])}" if node["evidence"] else ""
        typer.echo(
            f"{'  ' * depth}{node['key']} [{node['status']}] {node['title'][:60]}{flags}{evidence}"
        )
        for child in node["children"]:
            show(child, depth + 1)

    for tree in data["roots"]:
        show(tree, 0)
    for gap in data["gaps"]:
        typer.echo(f"gap: {gap['key']} has no {gap['missing']}")
    if data["stale"]:
        typer.echo("stale: " + ", ".join(data["stale"]))
