# Task runner (optional; each recipe is a plain command). See ADR-001.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

default: check

sync:
    uv sync --all-packages

fmt:
    uv run ruff format
    uv run ruff check --fix

lint:
    uv run ruff format --check
    uv run ruff check
    uv run mypy
    uv run lint-imports

test *args:
    uv run pytest --cov --req-report=docs/traceability/coverage.md {{args}}

trace:
    uv run python scripts/trace_registry.py --check

openapi:
    uv run python scripts/export_openapi.py
    uv run python scripts/export_hub_openapi.py

check: lint trace test
    uv run python scripts/export_openapi.py --check
    uv run python scripts/export_hub_openapi.py --check

serve:
    uv run panos serve
