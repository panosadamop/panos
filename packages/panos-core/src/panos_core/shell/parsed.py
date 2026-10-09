"""Result of parsing a shell script into individual commands."""

from dataclasses import dataclass, field

DYNAMIC = "<dynamic>"  # placeholder for a value only known at run time


@dataclass(frozen=True)
class ParsedScript:
    """Commands found in a script. ``dynamic`` means static analysis is not reliable
    (parse errors, command substitution, eval, .NET calls, variable command names...)."""

    commands: list[list[str]] = field(default_factory=list)
    dynamic: bool = False
    writes_files: bool = False  # output redirection to a file
    reasons: list[str] = field(default_factory=list)


def unparseable(reason: str) -> ParsedScript:
    return ParsedScript(dynamic=True, reasons=[reason])
