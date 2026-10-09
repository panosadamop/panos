"""``panos voice``: set up models, control the voice worker, push-to-talk from the terminal."""

from pathlib import Path
from typing import Annotated

import typer

from panos_cli.common import call, print_json
from panos_core.config import load_settings

voice_app = typer.Typer(
    help="Voice: models, worker, push-to-talk and speech.", no_args_is_help=True
)


@voice_app.command("setup")
def voice_setup(
    source: Annotated[Path | None, typer.Option("--from", help="copy models from a folder")] = None,
    whisper: Annotated[str, typer.Option(help="Whisper size")] = "small",
) -> None:
    """Download (or copy) the local voice models into the Panos data directory."""
    from panos_core.voice.models import missing, provision, voice_dir

    settings = load_settings()
    target = settings.voice.model_dir or voice_dir(settings.data_dir)
    added = provision(target, whisper_size=whisper, source=source)
    typer.echo(
        f"Models in {target}: added {len(added)} file(s)"
        + (": " + ", ".join(added) if added else "")
    )
    left = missing(target, whisper)
    typer.echo("Ready." if not left else "Still missing: " + ", ".join(left))


@voice_app.command("status")
def voice_status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    data = call(lambda c: c.get("/voice/status"))
    if as_json:
        print_json(data)
        return
    state = data["state"] if data["running"] else "not running"
    typer.echo(
        f"voice: {state}; wake word: "
        + (
            f"{data['wake_word']} (on)"
            if data["wake_word_enabled"] and data["wake_word"]
            else "off"
        )
        + f"; speech output: {'on' if data['tts'] else 'off'}"
    )
    if data["missing_models"]:
        typer.echo(
            "missing models: " + ", ".join(data["missing_models"]) + " → run `panos voice setup`"
        )
    if data["last_transcript"]:
        typer.echo(f"last heard: {data['last_transcript']}")
    if data["last_error"]:
        typer.echo(f"last error: {data['last_error']}")


@voice_app.command("start")
def voice_start() -> None:
    voice_status_after(call(lambda c: c.post("/voice/start", {})))


@voice_app.command("stop")
def voice_stop() -> None:
    call(lambda c: c.post("/voice/stop", {}))
    typer.echo("voice stopped")


def voice_status_after(data: dict[str, object]) -> None:
    if data.get("missing_models"):
        typer.echo("missing models → run `panos voice setup`")
    else:
        typer.echo(f"voice: {data['state']}")


@voice_app.command("listen")
def voice_listen() -> None:
    """Push-to-talk: start listening now (stops at the end of what you say)."""
    call(lambda c: c.post("/voice/listen", {}))
    typer.echo("listening…")


@voice_app.command("say")
def voice_say(text: Annotated[list[str], typer.Argument()]) -> None:
    call(lambda c: c.post("/voice/speak", {"text": " ".join(text)}))


@voice_app.command("wake")
def voice_wake(state: Annotated[str, typer.Argument(help="on or off")]) -> None:
    """Turn the always-listening wake word on or off (push-to-talk always works)."""
    data = call(lambda c: c.patch("/voice/settings", {"wake_word": state.lower() == "on"}))
    typer.echo(f"wake word {'on' if data['wake_word_enabled'] else 'off'}")


@voice_app.command("mute")
def voice_mute() -> None:
    call(lambda c: c.patch("/voice/settings", {"muted": True}))
    typer.echo("microphone muted (wake word ignored)")


@voice_app.command("unmute")
def voice_unmute() -> None:
    call(lambda c: c.patch("/voice/settings", {"muted": False}))
    typer.echo("microphone unmuted")
