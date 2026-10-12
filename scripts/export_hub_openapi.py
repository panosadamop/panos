"""Export the hub API OpenAPI schema for the web dashboard client (ADR-018, ADR-028).

Usage:
    python scripts/export_hub_openapi.py [--out docs/api/hub.openapi.v1.json] [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "api" / "hub.openapi.v1.json"


def build_schema() -> dict[str, Any]:
    from panos_control_plane.server import build_app

    # Routes do not depend on runtime state; no database or keys are touched.
    app = build_app(None, None, "schema-export")  # type: ignore[arg-type]
    schema: dict[str, Any] = app.openapi()
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date: run scripts/export_hub_openapi.py", file=sys.stderr)
            return 1
        print(f"OK: {args.out} is up to date")
        return 0
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
