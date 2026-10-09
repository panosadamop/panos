"""PowerShell script analysis using PowerShell's own AST parser (ADR-007).

A parser-only PowerShell process receives, on stdin, a fixed one-line program with the
script embedded as a base64 literal. The program calls ``[Parser]::ParseInput`` — it
never executes the analysed script — and prints every ``CommandAst`` with its static
arguments as JSON. Anything static analysis cannot see through (parse errors, .NET
method calls, dynamic command names such as ``& $x``) marks the script dynamic, which
classifies it as ``shell.unknown``.

The command line is deliberately plain (``-Command -``): passing programs via
``-EncodedCommand`` is a common malware signal and is flagged by endpoint protection.
"""

import base64
import json
import shutil
import subprocess
from functools import cache
from typing import Final

from panos_core.shell.parsed import DYNAMIC, ParsedScript, unparseable

# Statements joined into a single line: `-Command -` executes stdin line by line.
# PowerShell variable names are case-insensitive, so every name here is distinct.
_PARSER_STATEMENTS: Final = (
    "$ErrorActionPreference = 'Stop'",
    "$src = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))",
    "$tokens = $null",
    "$parseErrors = $null",
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput("
    "$src, [ref]$tokens, [ref]$parseErrors)",
    "$tStr = [System.Management.Automation.Language.StringConstantExpressionAst]",
    "$tParam = [System.Management.Automation.Language.CommandParameterAst]",
    "$tConst = [System.Management.Automation.Language.ConstantExpressionAst]",
    "$reasons = New-Object System.Collections.ArrayList",
    "if ($parseErrors.Count -gt 0) { [void]$reasons.Add('parse errors') }",
    "$cmdList = New-Object System.Collections.ArrayList",
    "$found = $ast.FindAll({ param($n) "
    "$n -is [System.Management.Automation.Language.CommandAst] }, $true)",
    "foreach ($cmdAst in $found) { "
    "$els = New-Object System.Collections.ArrayList; "
    "foreach ($el in $cmdAst.CommandElements) { "
    "if ($el -is $tStr) { [void]$els.Add([string]$el.Value) } "
    "elseif ($el -is $tParam) { [void]$els.Add('-' + $el.ParameterName); "
    "if ($el.Argument) { if ($el.Argument -is $tStr) "
    "{ [void]$els.Add([string]$el.Argument.Value) } "
    "else { [void]$els.Add('<dynamic>') } } } "
    "elseif ($el -is $tConst) { [void]$els.Add([string]$el.Value) } "
    "else { [void]$els.Add('<dynamic>') } }; "
    "if ($cmdAst.CommandElements.Count -gt 0 -and -not ($cmdAst.CommandElements[0] -is $tStr)) "
    "{ [void]$reasons.Add('dynamic command name') }; "
    "[void]$cmdList.Add([pscustomobject]@{ argv = [object[]]$els.ToArray() }) }",
    "if ($ast.Find({ param($n) "
    "$n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) "
    "{ [void]$reasons.Add('.NET method invocation') }",
    "$redir = $ast.FindAll({ param($n) "
    "$n -is [System.Management.Automation.Language.FileRedirectionAst] }, $true).Count",
    "[pscustomobject]@{ commands = [object[]]$cmdList.ToArray(); "
    "reasons = [object[]]$reasons.ToArray(); redirections = $redir } "
    "| ConvertTo-Json -Depth 6 -Compress",
)

_TIMEOUT_S: Final = 30


@cache
def _powershell_exe() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _program(script: str) -> str:
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")  # [A-Za-z0-9+/=] only
    return "; ".join((f"$b64 = '{b64}'", *_PARSER_STATEMENTS)) + "\n"


def parse_powershell(script: str) -> ParsedScript:
    """Statically parse a PowerShell script. Never executes ``script``."""
    if not script.strip():
        return unparseable("empty script")
    exe = _powershell_exe()
    if exe is None:
        return unparseable("PowerShell parser not available")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed parser program, analysed script as data
            [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=_program(script),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unparseable(f"PowerShell parser failed: {type(exc).__name__}")
    output = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not output:
        return unparseable("PowerShell parser returned an error")
    try:
        data = json.loads(output[-1])
    except json.JSONDecodeError:
        return unparseable("PowerShell parser output was not JSON")

    commands: list[list[str]] = []
    for item in _as_list(data.get("commands")):
        raw = item.get("argv") if isinstance(item, dict) else None
        argv = [str(v) if v is not None else DYNAMIC for v in _as_list(raw)]
        if argv:
            commands.append(argv)
    reasons = [str(r) for r in _as_list(data.get("reasons"))]
    return ParsedScript(
        commands=commands,
        dynamic=bool(reasons),
        writes_files=int(data.get("redirections") or 0) > 0,
        reasons=reasons,
    )
