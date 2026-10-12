"""``panos knowledge``: company folders, websites and cloud drives as knowledge (FR-019/020)."""

from typing import Annotated, Any

import typer

from panos_cli.common import call, print_json

knowledge_app = typer.Typer(
    help="Knowledge collections outside projects, and ranked search.", no_args_is_help=True
)

Privacy = Annotated[str, typer.Option("--privacy", help="LOCAL_ONLY (default) | STANDARD")]
Connector = Annotated[str | None, typer.Option("--account", help="connector account id")]


def _line(c: dict[str, Any]) -> str:
    indexed = c["last_indexed_at"][:16] if c["last_indexed_at"] else "never indexed"
    state = "" if c["enabled"] else ", disabled"
    return f"{c['name']:<20} {c['kind']:<13} [{c['privacy_mode']}{state}]  {indexed}"


def _add(name: str, kind: str, location: dict[str, Any], privacy: str, account: str | None) -> None:
    body = {
        "name": name,
        "kind": kind,
        "location": location,
        "connector": account,
        "privacy_mode": privacy.upper(),
    }
    created = call(lambda c: c.post("/knowledge/collections", body))
    typer.echo(f"added {_line(created)}")
    typer.echo(f"run `panos knowledge index {name}` to index it")


@knowledge_app.command("add-folder")
def add_folder(
    name: str, path: str, privacy: Privacy = "LOCAL_ONLY", account: Connector = None
) -> None:
    """A local company folder (Office, PDF, Markdown and text files)."""
    _add(name, "folder", {"path": path}, privacy, account)


@knowledge_app.command("add-site")
def add_site(
    name: str,
    urls: Annotated[list[str], typer.Argument(help="page URLs (no crawling)")],
    privacy: Privacy = "LOCAL_ONLY",
) -> None:
    """Selected web pages; robots.txt is honoured."""
    _add(name, "website", {"urls": urls}, privacy, None)


@knowledge_app.command("add-drive")
def add_drive(
    name: str, folder_id: str, privacy: Privacy = "LOCAL_ONLY", account: Connector = None
) -> None:
    """A Google Drive folder (needs `panos connect google --feature drive`)."""
    _add(name, "google_drive", {"folder_id": folder_id}, privacy, account)


@knowledge_app.command("add-onedrive")
def add_onedrive(
    name: str,
    drive_id: str,
    item: Annotated[str, typer.Option(help="folder item id")] = "root",
    privacy: Privacy = "LOCAL_ONLY",
    account: Connector = None,
) -> None:
    """A OneDrive or SharePoint folder (needs `panos connect microsoft`)."""
    _add(name, "onedrive", {"drive_id": drive_id, "item_id": item}, privacy, account)


@knowledge_app.command("list")
def list_collections(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    data: list[dict[str, Any]] = call(lambda c: c.get("/knowledge/collections"))  # type: ignore[assignment]
    if as_json:
        print_json(data)
        return
    if not data:
        typer.echo("No knowledge collections. Try `panos knowledge add-folder company <path>`.")
    for collection in data:
        typer.echo(_line(collection))


@knowledge_app.command("index")
def index(name: str) -> None:
    """Index (incrementally) one collection."""
    stats = call(lambda c: c.post(f"/knowledge/collections/{name}/index", {}))
    typer.echo(
        f"{name}: {stats['indexed']} indexed, {stats['unchanged']} unchanged, "
        f"{stats['removed']} removed, {stats['chunks']} chunks"
    )
    for reason in stats["skipped"]:
        typer.echo(f"  skipped {reason}")


@knowledge_app.command("set")
def update(
    name: str,
    privacy: Annotated[str | None, typer.Option(help="LOCAL_ONLY | STANDARD")] = None,
    enabled: Annotated[bool | None, typer.Option("--enable/--disable")] = None,
) -> None:
    body: dict[str, Any] = {}
    if privacy:
        body["privacy_mode"] = privacy.upper()
    if enabled is not None:
        body["enabled"] = enabled
    typer.echo(_line(call(lambda c: c.patch(f"/knowledge/collections/{name}", body))))


@knowledge_app.command("remove")
def remove(name: str) -> None:
    """Remove a collection and its index (the source itself is not touched)."""
    call(lambda c: c.delete(f"/knowledge/collections/{name}"))
    typer.echo(f"removed {name}")


@knowledge_app.command("search")
def search(
    query: str,
    project: Annotated[str | None, typer.Option("-p", "--project")] = None,
    limit: Annotated[int, typer.Option("-n", "--limit")] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search the project first, then company knowledge, then websites (FR-020)."""
    params: dict[str, Any] = {"q": query, "limit": limit}
    if project:
        params["project"] = project
    hits: list[dict[str, Any]] = call(lambda c: c.get("/knowledge/search", **params))  # type: ignore[assignment]
    if as_json:
        print_json(hits)
        return
    if not hits:
        typer.echo("no matches")
    for h in hits:
        typer.echo(f"[{h['source']}] {h['uri']}#L{h['start_line']}-L{h['end_line']}")
        typer.echo(f"    {h['snippet']}")
