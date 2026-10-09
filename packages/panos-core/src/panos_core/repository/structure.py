"""File/module map, conventions and documentation inventory (FR-005).

Symbol outlines use Python's own ``ast`` for Python and lightweight regexes for other
languages. They are names only, no bodies, which keeps snapshots small and avoids
storing source content. A full multi-language parser (tree-sitter) is deferred until
the coding agent needs it.
"""

import ast
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Final

from panos_core.repository.walk import WalkedFile

MAX_OUTLINE_FILES: Final = 300
MAX_OUTLINE_BYTES: Final = 300_000

CONVENTION_FILES: Final = {
    ".editorconfig": "EditorConfig",
    "ruff.toml": "Ruff",
    ".flake8": "Flake8",
    "mypy.ini": "mypy",
    ".pylintrc": "Pylint",
    ".prettierrc": "Prettier",
    ".prettierrc.json": "Prettier",
    "prettier.config.js": "Prettier",
    ".eslintrc": "ESLint",
    ".eslintrc.json": "ESLint",
    ".eslintrc.js": "ESLint",
    "eslint.config.js": "ESLint",
    "eslint.config.mjs": "ESLint",
    "biome.json": "Biome",
    "tsconfig.json": "TypeScript config",
    "checkstyle.xml": "Checkstyle",
    ".pre-commit-config.yaml": "pre-commit",
    "phpcs.xml": "PHP_CodeSniffer",
    "pint.json": "Pint",
    ".golangci.yml": "golangci-lint",
    "rustfmt.toml": "rustfmt",
    ".stylelintrc": "Stylelint",
    ".gitattributes": "gitattributes",
}

_JS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|interface|type|enum)"
    r"\s+([A-Za-z_$][\w$]*)",
    re.M,
)
_JAVA_DECL = re.compile(
    r"^\s*(?:public\s+|protected\s+|private\s+)?(?:abstract\s+|final\s+|sealed\s+)?"
    r"(?:class|interface|record|enum)\s+([A-Za-z_]\w*)",
    re.M,
)
_PHP_DECL = re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+(\w+)", re.M)
_GO_DECL = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)", re.M)


def _python_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    return [
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]


def outline(files: list[WalkedFile]) -> dict[str, list[str]]:
    regexes = {
        ".js": _JS_EXPORT,
        ".jsx": _JS_EXPORT,
        ".ts": _JS_EXPORT,
        ".tsx": _JS_EXPORT,
        ".mjs": _JS_EXPORT,
        ".java": _JAVA_DECL,
        ".kt": _JAVA_DECL,
        ".php": _PHP_DECL,
        ".go": _GO_DECL,
    }
    result: dict[str, list[str]] = {}
    for f in files:
        if len(result) >= MAX_OUTLINE_FILES:
            break
        suffix = f.path.suffix.lower()
        if f.size > MAX_OUTLINE_BYTES or (suffix != ".py" and suffix not in regexes):
            continue
        try:
            text = f.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        symbols = _python_symbols(text) if suffix == ".py" else regexes[suffix].findall(text)
        if symbols:
            result[f.rel] = symbols[:50]
    return result


def tree_summary(files: list[WalkedFile]) -> dict[str, Any]:
    top: Counter[str] = Counter()
    for f in files:
        parts = PurePosixPath(f.rel).parts
        top[parts[0] + ("/" if len(parts) > 1 else "")] += 1
    largest = sorted(files, key=lambda f: f.size, reverse=True)[:5]
    return {
        "total_files": len(files),
        "total_bytes": sum(f.size for f in files),
        "top_level": dict(top.most_common(40)),
        "largest_files": [{"path": f.rel, "bytes": f.size} for f in largest],
    }


def python_packages(files: list[WalkedFile]) -> list[str]:
    return sorted(
        {str(PurePosixPath(f.rel).parent) for f in files if f.path.name == "__init__.py"}
    )[:200]


def conventions(files: list[WalkedFile]) -> list[str]:
    found = {
        CONVENTION_FILES[PurePosixPath(f.rel).name]
        for f in files
        if "/" not in f.rel and PurePosixPath(f.rel).name in CONVENTION_FILES
    }
    return sorted(found)


def documentation(files: list[WalkedFile]) -> dict[str, Any]:
    roots = {PurePosixPath(f.rel).name.lower(): f.rel for f in files if "/" not in f.rel}
    readme = next((v for k, v in roots.items() if k.startswith("readme")), None)
    markdown = [f.rel for f in files if f.path.suffix.lower() in (".md", ".rst", ".adoc")]
    adrs = [
        m
        for m in markdown
        if re.search(r"(^|/)(adr|decisions?)(/|$)", m.lower())
        or re.search(r"(^|/)adr-\d+", m.lower())
    ]
    return {
        "readme": readme,
        "changelog": next((v for k, v in roots.items() if k.startswith("changelog")), None),
        "contributing": next((v for k, v in roots.items() if k.startswith("contributing")), None),
        "license": next(
            (v for k, v in roots.items() if k.startswith(("license", "copying"))), None
        ),
        "markdown_files": len(markdown),
        "docs_dir": any(f.rel.startswith("docs/") for f in files),
        "adr_count": len(adrs),
    }
