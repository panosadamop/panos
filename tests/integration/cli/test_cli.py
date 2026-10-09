import json

import pytest
from typer.testing import CliRunner

from panos_cli.main import EXIT_NOT_RUNNING, app
from panos_core import __version__
from panos_core.config import PanosSettings
from panos_core.config.runtime import write_runtime_file

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


@pytest.mark.req("BR-029")
def test_config_path_lists_workspace(settings: PanosSettings) -> None:
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(settings.workspace_dir) in result.stdout
    assert str(settings.database_path) in result.stdout


def test_config_show_is_json_without_secrets(settings: PanosSettings) -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["log_level"] == "INFO"
    assert "token" not in result.stdout.lower()


def test_status_when_not_running(settings: PanosSettings) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == EXIT_NOT_RUNNING
    assert "not running" in result.stdout


def test_status_json_when_not_running(settings: PanosSettings) -> None:
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == EXIT_NOT_RUNNING
    assert json.loads(result.stdout)["running"] is False


def test_status_with_stale_runtime_file(settings: PanosSettings) -> None:
    # Port 1 on loopback is not listening: simulates a node that crashed.
    write_runtime_file(settings.runtime_file, host="127.0.0.1", port=1, token="t", version="v")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == EXIT_NOT_RUNNING
    assert "not reachable" in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "serve" in result.output and "status" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["audit", "list"],
        ["audit", "verify"],
        ["approvals", "list"],
        ["approvals", "approve", "apr_x"],
    ],
)
def test_node_commands_report_not_running(settings: PanosSettings, args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == EXIT_NOT_RUNNING
    assert "not running" in result.stdout
