"""``panos ask`` and ``panos ai``: talk to Panos, inspect providers, models, usage and routing."""

import sys
from typing import Annotated, Any

import httpx
import typer

from panos_cli.client import NodeClient, NodeNotRunningError
from panos_cli.common import EXIT_ERROR, call, not_running, print_json
from panos_core.config import load_settings

ai_app = typer.Typer(help="AI providers, models, usage and routing.", no_args_is_help=True)
AsJson = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def ask(
    text: Annotated[list[str], typer.Argument(help="what to ask Panos")],
    project: Annotated[str | None, typer.Option("--project", "-p", help="project id/name")] = None,
    conversation: Annotated[
        str | None, typer.Option("--conversation", "-c", help="continue a conversation")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="show routing")] = False,
    as_json: AsJson = False,
) -> None:
    """Ask Panos something. The answer streams as it is generated."""
    body = {
        "text": " ".join(text),
        "project": project,
        "conversation_id": conversation,
        "stream": True,
    }
    try:
        client = NodeClient.from_settings(load_settings())
    except NodeNotRunningError as exc:
        not_running(str(exc), as_json)
    try:
        reply = _stream(client, body, verbose=verbose, as_json=as_json)
    except NodeNotRunningError as exc:
        not_running(str(exc), as_json)
    except httpx.HTTPStatusError as exc:
        typer.echo(f"error: {exc.response.status_code} {_detail(exc.response)}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    finally:
        client.close()
    if reply is None:
        raise typer.Exit(EXIT_ERROR)


def _stream(
    client: NodeClient, body: dict[str, Any], *, verbose: bool, as_json: bool
) -> dict[str, Any] | None:
    streamed = False
    for event in client.stream("/conversation/messages", body):
        kind = event.get("type")
        if kind == "route" and verbose and not as_json:
            typer.echo(f"[{event['provider']}/{event['model']} · {event['locality']}]", err=True)
        elif kind == "text" and not as_json:
            sys.stdout.write(event["text"])
            sys.stdout.flush()
            streamed = True
        elif kind == "reply":
            reply: dict[str, Any] = event["reply"]
            if as_json:
                print_json(reply)
            else:
                if not streamed:
                    typer.echo(reply["text"], nl=False)
                typer.echo("")
                if reply.get("sources"):
                    typer.echo("sources: " + ", ".join(reply["sources"]), err=True)
                if verbose:
                    typer.echo(
                        f"conversation {reply['conversation_id']} · {reply['privacy']}"
                        f" · {reply['latency_ms']} ms",
                        err=True,
                    )
            return reply
        elif kind == "error":
            error = event.get("error", {})
            typer.echo(f"\nerror: {error.get('detail', 'request failed')}", err=True)
            for item in error.get("rejected", []) if verbose else []:
                typer.echo(f"  - {item['candidate']}: {item['reason']}", err=True)
            return None
    return None


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", ""))
    except ValueError:
        return response.text[:200]


@ai_app.command("providers")
def ai_providers(
    refresh: Annotated[bool, typer.Option("--refresh", help="re-check health now")] = False,
    as_json: AsJson = False,
) -> None:
    """Configured model providers and their health."""
    data: Any = call(lambda c: c.get("/ai/providers", refresh=refresh or None))
    if as_json:
        print_json(data)
        return
    if not data:
        typer.echo("No AI providers are configured.")
    for p in data:
        state = "ok" if p["ok"] else f"unavailable ({p['detail']})"
        key = " · key set" if p["has_key"] else ""
        typer.echo(f"{p['id']:<12} {p['kind']:<18} {p['locality']:<6} {state}{key}")


@ai_app.command("models")
def ai_models(as_json: AsJson = False) -> None:
    """Known models, tiers, costs and measured speeds."""
    data: Any = call(lambda c: c.get("/ai/models"))
    if as_json:
        print_json(data)
        return
    for m in data:
        cost = f"${m['cost_in']}/${m['cost_out']} per 1M" if m["cost_in"] else "free"
        typer.echo(
            f"{m['kind']:<10} {m['name']:<22} {m['tier']:<16} {cost:<20} {m['gen_tps']} tok/s"
        )


@ai_app.command("usage")
def ai_usage(
    days: Annotated[int | None, typer.Option(help="last N days (default: this month)")] = None,
    as_json: AsJson = False,
) -> None:
    """Model usage and estimated cost."""
    data = call(lambda c: c.get("/ai/usage", days=days))
    if as_json:
        print_json(data)
        return
    budget = data["monthly_budget_usd"]
    typer.echo(
        f"since {data['since'][:10]}: ${data['total_cost_usd']:.4f}"
        + (f" of ${budget:.2f} monthly budget" if budget is not None else "")
    )
    for r in data["rows"]:
        typer.echo(
            f"  {r['provider']}/{r['model']} ({r['locality']}): {r['calls']} calls, "
            f"{r['input_tokens']}+{r['output_tokens']} tokens, ${r['cost_usd']:.4f}"
        )


@ai_app.command("route")
def ai_route(
    text: Annotated[str, typer.Argument()] = "hello",
    task: Annotated[str, typer.Option()] = "chat",
    complexity: Annotated[str, typer.Option()] = "medium",
    privacy: Annotated[str, typer.Option(help="LOCAL_ONLY or STANDARD")] = "LOCAL_ONLY",
    as_json: AsJson = False,
) -> None:
    """Explain which model would handle a request (does not call it)."""
    body = {"text": text, "task": task, "complexity": complexity, "privacy": privacy.upper()}
    data = call(lambda c: c.post("/ai/route", body))
    if as_json:
        print_json(data)
        return
    typer.echo(f"{data['provider']}/{data['model']} ({data['locality']})")
    for reason in data["reasons"]:
        typer.echo(f"  - {reason}")
    for item in data["rejected"]:
        typer.echo(f"  x {item['candidate']}: {item['reason']}")


@ai_app.command("set-key")
def ai_set_key(
    provider: str,
    stdin: Annotated[bool, typer.Option("--stdin", help="read the key from stdin")] = False,
) -> None:
    """Store a provider API key in the OS credential store (never on the command line)."""
    key = (
        sys.stdin.readline().strip()
        if stdin
        else typer.prompt(f"{provider} API key", hide_input=True)
    )
    call(lambda c: c.put(f"/ai/providers/{provider}/key", {"key": key}))
    typer.echo(f"Key for {provider} stored.")


@ai_app.command("clear-key")
def ai_clear_key(provider: str) -> None:
    """Remove a provider API key."""
    call(lambda c: c.delete(f"/ai/providers/{provider}/key"))
    typer.echo(f"Key for {provider} removed.")


@ai_app.command("reload")
def ai_reload() -> None:
    """Rebuild providers from configuration and re-check their health."""
    data: Any = call(lambda c: c.post("/ai/providers/reload", {}))
    typer.echo(f"{sum(1 for p in data if p['ok'])}/{len(data)} providers available.")
