"""Export the node API OpenAPI schema (ADR-018).

The live ``/openapi.json`` endpoint is disabled (it would sit outside authentication),
so the schema is generated offline here and committed for client generation.

Usage:
    python scripts/export_openapi.py [--out docs/api/openapi.v1.json] [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "api" / "openapi.v1.json"


def build_schema() -> dict[str, Any]:
    from panos_core.config import PanosSettings
    from panos_node_api import create_app
    from panos_node_api.security import AuthConfig

    # The schema does not depend on runtime values; placeholders never leave this process.
    app = create_app(
        PanosSettings(),
        AuthConfig(token="schema-export", allowed_hosts=frozenset()),  # noqa: S106
    )
    schema: dict[str, Any] = app.openapi()
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args()

    content = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    out: Path = args.out
    if args.check:
        if not out.exists() or out.read_text(encoding="utf-8") != content:
            print(f"ERROR: {out} is out of date; run scripts/export_openapi.py", file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
