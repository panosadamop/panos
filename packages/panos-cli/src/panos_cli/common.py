"""Shared CLI helpers: exit codes and calling the running node."""

import json
from collections.abc import Callable
from typing import Any, NoReturn

import httpx
import typer

from panos_cli.client import NodeClient, NodeNotRunningError
from panos_core.config import load_settings

EXIT_NOT_RUNNING = 1
EXIT_ALREADY_RUNNING = 3
EXIT_ERROR = 4


def not_running(message: str, as_json: bool) -> NoReturn:
    if as_json:
        typer.echo(json.dumps({"running": False, "detail": message}))
    else:
        typer.echo(f"Panos node is not running: {message}")
    raise typer.Exit(EXIT_NOT_RUNNING)


def call(fn: Callable[[NodeClient], dict[str, Any]]) -> dict[str, Any]:
    """Run a request against the running node, mapping errors to exit codes."""
    try:
        client = NodeClient.from_settings(load_settings())
    except NodeNotRunningError as exc:
        not_running(str(exc), False)
    try:
        return fn(client)
    except NodeNotRunningError as exc:
        not_running(str(exc), False)
    except httpx.HTTPStatusError as exc:
        detail = ""
        if exc.response.content:
            try:
                detail = str(exc.response.json().get("detail", ""))
            except ValueError:
                detail = exc.response.text[:200]
        typer.echo(f"error: {exc.response.status_code} {detail}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    finally:
        client.close()


def print_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
