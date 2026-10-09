# Repository Structure

> **Amendments (2026-10-08, ADR-001):** the originally proposed layout is refined. Python packages use a `src/` layout under `packages/` and `services/` so that each package declares its own dependencies (for example, `panos-core` cannot depend on FastAPI). Persistence and adapter infrastructure have explicit folders, and import-linter enforces the boundaries. Specs live in `docs/specs/`.

```text
panos/
├── CLAUDE.md
├── README.md
├── pyproject.toml              # uv workspace root + ruff/mypy/pytest/import-linter config
├── uv.lock
├── package.json                # pnpm workspace root scripts
├── pnpm-workspace.yaml
├── justfile                    # task runner
├── .github/workflows/          # 3-OS CI matrix, security scans
├── docs/
│   ├── specs/                  # specification pack
│   ├── adr/                    # Architecture Decision Records
│   ├── planning/               # approved plan, spikes, milestone reports
│   └── traceability/           # requirements.yaml, generated coverage
├── apps/
│   ├── desktop/                # Tauri + React/TS (src/, src-tauri/)
│   ├── web/                    # React/TS dashboard (M9)
│   └── mobile/                 # React Native (M10)
├── services/
│   ├── node-api/               # FastAPI entry point: src/panos_node_api
│   └── control-plane/          # M9
├── packages/
│   ├── panos-core/             # src/panos_core (domain + application services + adapters)
│   │   └── src/panos_core/
│   │       ├── domain/  conversation/  intent/
│   │       ├── orchestration/  agents/  jobs/  checkpoints/
│   │       ├── tools/  policy/  approvals/  audit/  secrets/  redaction/
│   │       ├── ai/  knowledge/  voice/
│   │       ├── projects/  repository/  apps/   # registry, analysis, app catalog
│   │       ├── requirements/  traceability/  estimation/  skills/  documents/
│   │       ├── notifications/  nodes/  integrations/
│   │       ├── persistence/  config/  observability/   # persistence/migrations = Alembic (ADR-004)
│   │       └── platform/       # dispatchers + windows/ posix/ (+ macos/ linux/ where they differ) fake/
│   ├── panos-cli/              # src/panos_cli (thin client to the node API)
│   └── ts-client/              # generated API client/types
├── tests/
│   ├── unit/  integration/  e2e/  security/
│   ├── contract/               # OS adapter and AI adapter contract suites
│   └── fixtures/
└── scripts/
```

## Boundary rules (enforced by import-linter)

- `panos_core.domain` imports nothing from Panos infrastructure, FastAPI, SQLAlchemy or provider SDKs.
- `panos_core.platform.{windows,posix,macos,linux}` are imported only by the platform package (dispatchers/factories).
- `panos_core.ai.adapters.*`, `knowledge.adapters.*` and `voice.adapters.*` are imported only by composition roots.
- `panos_node_api` and `panos_cli` depend on `panos_core`, never the reverse.

Keep domain code independent of FastAPI, Tauri and provider SDKs.
