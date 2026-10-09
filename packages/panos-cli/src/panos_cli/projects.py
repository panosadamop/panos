"""``panos project`` and ``panos apps`` commands (M3)."""

from pathlib import Path
from typing import Annotated, Any

import typer

from panos_cli.common import EXIT_ERROR, call, print_json

project_app = typer.Typer(help="Register, analyze, search and open projects.", no_args_is_help=True)
apps_app = typer.Typer(help="Discover and launch supported applications.", no_args_is_help=True)

AsJson = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def _summary(p: dict[str, Any]) -> str:
    ide = p["preferred_ide"] or "-"
    return f"{p['name']:<24} {p['privacy_mode']:<10} ide={ide:<18} {p['path']}"


@project_app.command("add")
def project_add(
    path: Annotated[Path, typer.Argument(help="Existing project directory.")],
    name: Annotated[str | None, typer.Option(help="Project name (default: folder).")] = None,
    ide: Annotated[str | None, typer.Option(help="Preferred IDE (key or alias).")] = None,
) -> None:
    """Register an existing directory as a project (local-only by default)."""
    body = {"path": str(path.resolve()), "name": name, "preferred_ide": ide}
    data = call(lambda c: c.post("/projects", body))
    typer.echo(f"registered {data['name']} ({data['id']}) — privacy {data['privacy_mode']}")


@project_app.command("create")
def project_create(name: str) -> None:
    """Create a new empty project folder in the workspace (~/Panos/Projects)."""
    data = call(lambda c: c.post("/projects", {"name": name, "create": True}))
    typer.echo(f"created {data['name']} at {data['path']}")


@project_app.command("list")
def project_list(as_json: AsJson = False) -> None:
    """List registered projects."""
    data = call(lambda c: c.get("/projects"))
    if as_json:
        print_json(data)
        return
    if not data["projects"]:
        typer.echo("no projects registered (use: panos project add <path>)")
    for p in data["projects"]:
        typer.echo(_summary(p))


@project_app.command("show")
def project_show(ref: str, as_json: AsJson = False) -> None:
    """Show a project's settings."""
    data = call(lambda c: c.get(f"/projects/{ref}"))
    if as_json:
        print_json(data)
    else:
        typer.echo(_summary(data))
        for action, level in data["policy_overrides"].items():
            typer.echo(f"  policy override: {action} = {level}")


@project_app.command("analyze")
def project_analyze(ref: str, as_json: AsJson = False) -> None:
    """Analyze the repository: stack, build/test commands, structure, Git state."""
    data = call(lambda c: c.post(f"/projects/{ref}/analyze", {}))
    if as_json:
        print_json(data)
        return
    stack = data["stack"]
    typer.echo(f"primary language : {stack.get('primary_language') or '-'}")
    typer.echo(f"frameworks       : {', '.join(stack.get('frameworks', [])) or '-'}")
    typer.echo(f"build systems    : {', '.join(stack.get('build_systems', [])) or '-'}")
    typer.echo(f"package managers : {', '.join(stack.get('package_managers', [])) or '-'}")
    if stack.get("databases"):
        typer.echo(f"databases        : {', '.join(stack['databases'])}")
    if stack.get("infrastructure"):
        typer.echo(f"infrastructure   : {', '.join(stack['infrastructure'])}")
    for kind, commands in data["commands"].items():
        for argv in commands:
            typer.echo(f"{kind:<17}: {' '.join(argv)}")
    git = data["structure"].get("git", {})
    if git.get("is_repository"):
        typer.echo(
            f"git              : {git.get('branch') or '(detached)'} @ "
            f"{(git.get('head') or '')[:10]} "
            f"({git.get('changed_files', 0)} changed, {git.get('untracked_files', 0)} "
            "untracked)"
        )
    typer.echo(f"confidence       : {data['confidence']:.2f}")
    for question in data["clarifications"]:
        typer.echo(f"  ? {question}")


@project_app.command("index")
def project_index(
    ref: str,
    semantic: Annotated[
        bool, typer.Option("--semantic", help="also compute embeddings with a local model")
    ] = False,
) -> None:
    """(Re)index project files into the local knowledge index (incremental)."""
    data = call(lambda c: c.post(f"/projects/{ref}/index", {}, semantic=semantic or None))
    typer.echo(
        f"{data['files_seen']} files: {data['indexed']} indexed, "
        f"{data['unchanged']} unchanged, {data['removed']} removed "
        f"({data['chunks']} chunks)"
    )


@project_app.command("search")
def project_search(
    ref: str, query: str, limit: Annotated[int, typer.Option(min=1, max=100)] = 10
) -> None:
    """Search indexed project knowledge."""
    data = call(lambda c: c.get(f"/projects/{ref}/search", q=query, limit=limit))
    if not data["hits"]:
        typer.echo("no matches")
    for h in data["hits"]:
        typer.echo(f"{h['uri']}:{h['start_line']}-{h['end_line']}  {h['snippet']}")


@project_app.command("open")
def project_open(
    ref: str,
    app: Annotated[str | None, typer.Option(help="IDE/editor (default: preferred).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would open.")] = False,
) -> None:
    """Open a project in its preferred IDE."""
    data = call(lambda c: c.post(f"/projects/{ref}/open", {"app": app, "dry_run": dry_run}))
    if dry_run:
        typer.echo(f"would open {data['name']}: {data['executable']} {' '.join(data['arguments'])}")
        return
    if data["status"] != "SUCCEEDED":
        typer.echo(f"not opened: {data['status']} {data.get('error') or ''}", err=True)
        raise typer.Exit(EXIT_ERROR)
    typer.echo(f"opened in {data['output']['app']} (pid {data['output']['pid']})")


@project_app.command("ide")
def project_ide(ref: str, app: str) -> None:
    """Set the project's preferred IDE ("" to clear)."""
    data = call(lambda c: c.patch(f"/projects/{ref}", {"preferred_ide": app}))
    typer.echo(f"{data['name']}: preferred IDE = {data['preferred_ide'] or '-'}")


@project_app.command("privacy")
def project_privacy(
    ref: str,
    mode: Annotated[str, typer.Argument(help="local-only | standard")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the privacy change.")] = False,
) -> None:
    """Change a project's privacy mode (explicit, audited)."""
    normalized = {"local-only": "LOCAL_ONLY", "standard": "STANDARD"}.get(mode.lower())
    if normalized is None:
        typer.echo("mode must be local-only or standard", err=True)
        raise typer.Exit(EXIT_ERROR)
    if normalized == "STANDARD" and not yes:
        typer.confirm(
            "STANDARD allows cloud AI to receive this project's content when "
            "routing permits. Continue?",
            abort=True,
        )
    data = call(
        lambda c: c.patch(
            f"/projects/{ref}", {"privacy_mode": normalized, "confirm_privacy_change": True}
        )
    )
    typer.echo(f"{data['name']}: privacy = {data['privacy_mode']}")


# ===================================================================== apps
@apps_app.command("list")
def apps_list(
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-run discovery.")] = False,
    as_json: AsJson = False,
) -> None:
    """List discovered supported applications."""
    data = call(lambda c: c.post("/apps/refresh", {}) if refresh else c.get("/apps"))
    if as_json:
        print_json(data)
        return
    if not data["apps"]:
        typer.echo("no supported applications found (try --refresh)")
    for a in data["apps"]:
        typer.echo(f"{a['key']:<22} {a['kind']:<8} {a['version'] or '-':<14} {a['executable']}")


@apps_app.command("launch")
def apps_launch(
    app: str,
    url: Annotated[str | None, typer.Option(help="http(s) URL (browsers).")] = None,
    project: Annotated[str | None, typer.Option(help="Open this project.")] = None,
) -> None:
    """Launch a supported application, optionally with a project or URL."""
    body: dict[str, Any] = {"target": "none"}
    if url:
        body = {"target": "url", "url": url}
    elif project:
        body = {"target": "project", "project": project}
    data = call(lambda c: c.post(f"/apps/{app}/launch", body))
    if data["status"] != "SUCCEEDED":
        typer.echo(f"not launched: {data['status']} {data.get('error') or ''}", err=True)
        raise typer.Exit(EXIT_ERROR)
    typer.echo(f"launched {data['output']['app']} (pid {data['output']['pid']})")


@apps_app.command("prefer")
def apps_prefer(technology: str, app: str) -> None:
    """Prefer an IDE for a technology (e.g. `panos apps prefer Python pycharm`)."""
    data = call(lambda c: c.put(f"/preferences/ide/{technology}", {"app": app}))
    typer.echo(f"{technology}: prefer {data['name']}")


@project_app.command("estimate")
def project_estimate(ref: str, as_json: AsJson = False) -> None:
    """Story points, hours, person-days, cost and a sprint plan from the backlog."""
    data = call(lambda c: c.get(f"/projects/{ref}/estimate"))
    if as_json:
        print_json(data)
        return
    typer.echo(
        f"{data['total_points']} points · {data['total_hours']} h · "
        f"{data['person_days']} person-days · {data['total_cost']:.2f} "
        f"{data['currency']} (confidence {data['confidence']})"
    )
    for role, r in data["by_role"].items():
        typer.echo(f"  {role:<10} {r['hours']:>7} h × {r['rate']} = {r['cost']:.2f}")
    for sprint in data["sprints"]:
        typer.echo(
            f"  sprint {sprint['number']}: {', '.join(sprint['stories'])} ({sprint['points']} pts)"
        )
    for assumption in data["assumptions"]:
        typer.echo(f"  * {assumption}")


@project_app.command("docs")
def project_docs(ref: str) -> None:
    """(Re)generate the requirement documents under docs/requirements/."""
    data = call(lambda c: c.post(f"/projects/{ref}/documents", {}))
    typer.echo(f"Wrote {len(data['files'])} file(s): " + ", ".join(data["files"]))


@project_app.command("skills")
def project_skills(ref: str) -> None:
    """(Re)generate Claude skills from approved facts (versioned)."""
    data = call(lambda c: c.post(f"/projects/{ref}/skills", {}))
    for skill in data["skills"]:
        state = "updated" if skill["updated"] else "unchanged"
        typer.echo(f"{skill['name']} v{skill['version']} ({state}) → {skill['path']}")


@project_app.command("scaffold")
def project_scaffold(
    ref: str,
    open_ide: Annotated[bool, typer.Option("--open", help="open in the preferred IDE")] = False,
) -> None:
    """Create the project skeleton from the approved stack, then docs and skills."""
    data = call(lambda c: c.post(f"/projects/{ref}/scaffold", {"open_ide": open_ide}))
    typer.echo(
        f"Stack {data['pack']}"
        + (f" + {', '.join(data['addons'])}" if data["addons"] else "")
        + f": {len(data['created'])} file(s) created"
        + (f", {len(data['skipped'])} existing kept" if data["skipped"] else "")
    )
    typer.echo(
        f"Docs: {len(data['documents'])} · skills: "
        + ", ".join(f"{s['name']} v{s['version']}" for s in data["skills"])
    )
    if data.get("opened"):
        typer.echo(f"Open in IDE: {data['opened']['status']}")


def screen(
    question: Annotated[list[str] | None, typer.Argument(help="what to look for")] = None,
    project: Annotated[str | None, typer.Option("--project", "-p")] = None,
    cloud: Annotated[
        bool,
        typer.Option(
            "--cloud", help="allow a cloud model for this one request (STANDARD projects only)"
        ),
    ] = False,
) -> None:
    """Capture the active window (in memory) and explain any visible errors."""
    body = {"question": " ".join(question or []), "project": project, "allow_cloud": cloud}
    data = call(lambda c: c.post("/screen/analyze", body))
    typer.echo(data["text"])
    typer.echo(
        f"[{data['window_title'][:60]} · {data['model']} · {data['locality']} · "
        f"{data['privacy']} · not saved]",
        err=True,
    )
