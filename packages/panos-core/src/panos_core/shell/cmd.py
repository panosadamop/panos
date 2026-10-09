"""Conservative analysis of ``cmd.exe /c`` command lines.

cmd's quoting rules are notoriously irregular, so anything involving variable
expansion (``%``/``!``), escapes (``^``) or parentheses is treated as dynamic.
"""

import re
import shlex
from typing import Final

from panos_core.shell.parsed import ParsedScript, unparseable

_SEPARATORS: Final = re.compile(r"&&|\|\||&|\|")
_DYNAMIC_CHARS: Final = frozenset("%!^()")


def parse_cmd(line: str) -> ParsedScript:
    if not line.strip():
        return unparseable("empty command line")
    if any(c in _DYNAMIC_CHARS for c in line) or "\n" in line:
        return unparseable("cmd expansion/escape characters")
    writes = ">" in line
    commands: list[list[str]] = []
    for segment in _SEPARATORS.split(line):
        segment = re.sub(r"\d?>>?\s*\S+|<\s*\S+", " ", segment).strip()
        if not segment:
            continue
        try:
            words = [w.strip('"') for w in shlex.split(segment, posix=False)]
        except ValueError:
            return unparseable("unbalanced quotes")
        if words:
            commands.append(words)
    return ParsedScript(commands=commands, writes_files=writes)
