"""POSIX shell script analysis via bashlex (ADR-007)."""

from typing import Any, Final

import bashlex
import bashlex.ast
import bashlex.errors

from panos_core.shell.parsed import DYNAMIC, ParsedScript, unparseable

_DYNAMIC_BUILTINS: Final = frozenset({"eval", "source", ".", "exec", "builtin"})


class _Visitor(bashlex.ast.nodevisitor):  # type: ignore[misc]
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.dynamic_reasons: list[str] = []
        self.writes = False

    def visitcommand(self, node: Any, parts: Any) -> bool:
        words: list[str] = []
        for part in node.parts:
            if part.kind == "word":
                nested = [p.kind for p in getattr(part, "parts", [])]
                if any(k in ("commandsubstitution", "processsubstitution") for k in nested):
                    self.dynamic_reasons.append("command/process substitution")
                    words.append(DYNAMIC)
                elif "parameter" in nested and not words:
                    self.dynamic_reasons.append("variable in command position")
                    words.append(DYNAMIC)
                elif "parameter" in nested:
                    words.append(DYNAMIC)
                else:
                    words.append(part.word)
            elif part.kind == "redirect" and part.type in (">", ">>", ">|", "&>", ">&"):
                target = getattr(part, "output", None)
                # 2>&1 style fd duplication is not a file write.
                if not (isinstance(target, int) or getattr(target, "kind", "") == "number"):
                    self.writes = True
        if words:
            if words[0] in _DYNAMIC_BUILTINS:
                self.dynamic_reasons.append(f"dynamic builtin {words[0]!r}")
            self.commands.append(words)
        return True

    def visitfunction(self, node: Any, name: Any, body: Any, parts: Any) -> bool:
        self.dynamic_reasons.append("function definition")
        return True


def parse_bash(script: str) -> ParsedScript:
    if not script.strip():
        return unparseable("empty script")
    try:
        trees = bashlex.parse(script)
    except (bashlex.errors.ParsingError, NotImplementedError, IndexError, AttributeError) as exc:
        return unparseable(f"bash parse error: {type(exc).__name__}")
    visitor = _Visitor()
    for tree in trees:
        visitor.visit(tree)
    return ParsedScript(
        commands=visitor.commands,
        dynamic=bool(visitor.dynamic_reasons),
        writes_files=visitor.writes,
        reasons=visitor.dynamic_reasons,
    )
