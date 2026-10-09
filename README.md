# Panos AI Desktop Assistant — Specification Pack

This repository contains the implementation specification for **Panos**, a local-first, cross-platform AI workstation assistant.

## Start here

Claude Code should read documents in this order:

1. [`CLAUDE.md`](CLAUDE.md)
2. [`VISION.md`](docs/specs/VISION.md)
3. [`BRD.md`](docs/specs/BRD.md)
4. [`FRD.md`](docs/specs/FRD.md)
5. [`NFR.md`](docs/specs/NFR.md)
6. [`ARCHITECTURE.md`](docs/specs/ARCHITECTURE.md)
7. [`SECURITY.md`](docs/specs/SECURITY.md)
8. [`PERMISSIONS.md`](docs/specs/PERMISSIONS.md)
9. [`AGENTS.md`](docs/specs/AGENTS.md)
10. [`TOOLS.md`](docs/specs/TOOLS.md)
11. [`AI_ROUTING.md`](docs/specs/AI_ROUTING.md)
12. [`VOICE.md`](docs/specs/VOICE.md)
13. [`KNOWLEDGE_MEMORY.md`](docs/specs/KNOWLEDGE_MEMORY.md)
14. [`REQUIREMENTS_PIPELINE.md`](docs/specs/REQUIREMENTS_PIPELINE.md)
15. [`DATA_MODEL.md`](docs/specs/DATA_MODEL.md)
16. [`API.md`](docs/specs/API.md)
17. [`USER_STORIES.md`](docs/specs/USER_STORIES.md)
18. [`ACCEPTANCE_CRITERIA.md`](docs/specs/ACCEPTANCE_CRITERIA.md)
19. [`TEST_STRATEGY.md`](docs/specs/TEST_STRATEGY.md)
20. [`REPOSITORY_STRUCTURE.md`](docs/specs/REPOSITORY_STRUCTURE.md)
21. [`MVP.md`](docs/specs/MVP.md)
22. [`ROADMAP.md`](docs/specs/ROADMAP.md)
23. [`IMPLEMENTATION_PLAN.md`](docs/specs/IMPLEMENTATION_PLAN.md)

## Repository layout

- `docs/specs/` — specification pack (this list)
- `docs/adr/` — Architecture Decision Records ([index](docs/adr/README.md))
- `docs/planning/` — approved implementation plan, spikes and milestone reports
- `docs/traceability/` — requirement registry and generated coverage reports
- [`project-log.md`](project-log.md) — chronological log of the working conversation and decisions
- [`considerations.md`](considerations.md) — issues, risks, trade-offs, deviations and open follow-ups

The approved implementation plan is [`docs/planning/PLAN_PROPOSAL.md`](docs/planning/PLAN_PROPOSAL.md). Where it amends a spec, the spec carries an *Amendments* note.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/), Git. Rust and pnpm are needed only from M7 (desktop).

```bash
uv sync --all-packages                 # create .venv with all workspace packages
uv run panos serve                     # run the local node (foreground)
uv run panos status                    # check it from another terminal
uv run pytest --cov --req-report=docs/traceability/coverage.md
uv run ruff format --check && uv run ruff check && uv run mypy && uv run lint-imports
uv run python scripts/trace_registry.py --check
uv run python scripts/export_openapi.py --check
```

Try it on a real project (in a second terminal while `panos serve` runs):

```bash
uv run panos project add C:\path\to\repo        # registers as LOCAL_ONLY
uv run panos project analyze repo                # stack, build/test commands, Git state
uv run panos project index repo                  # local full-text index
uv run panos project search repo "payment retry"
uv run panos apps list                           # discovered IDEs, editors, browsers
uv run panos project open repo                   # opens in the preferred IDE (--dry-run to preview)
```

Talk to Panos (M4). It uses local models via [Ollama](https://ollama.com) and, for STANDARD projects, Claude if you add a key:

```bash
ollama pull qwen2.5-coder:7b; ollama pull qwen2.5:3b; ollama pull nomic-embed-text
uv run panos ai providers                        # health of ollama / anthropic
uv run panos ask -v "list projects"              # simple commands: answered without a model
uv run panos project index repo --semantic       # add local embeddings (hybrid search)
uv run panos ask -p repo "what does OrderService do?"   # LOCAL_ONLY project → local model only
uv run panos ai set-key anthropic                # optional: Claude for STANDARD projects (hidden prompt)
uv run panos ai route --privacy STANDARD "explain this design"   # which model and why
uv run panos ai usage                            # tokens and cost this month
```

Let Panos do the work (M5). Jobs run in the background. They plan, checkpoint, edit, run the project's own format/lint/test commands, repair failures a bounded number of times and review the result, and they **ask before committing**:

```bash
uv run panos job start -p repo "add input validation to the signup endpoint"
uv run panos job watch job_…                    # follow until it finishes or needs you
uv run panos approvals list                      # the commit waits for your approval
uv run panos approvals approve apr_…             # commits exactly the approved tree
uv run panos job rollback job_…                  # restore the checkpoint (your .git is untouched)
uv run panos job start -p repo --cmd python --cmd manage.py --cmd test "run the tests"
uv run panos job pause|resume|cancel job_…
```

Start a new project from a brief (M6). Panos asks questions first, drafts a traceable specification and backlog, proposes a stack (or uses the one you named), and only scaffolds after you approve it:

```bash
uv run panos new -n shop "an online shop for our handmade products"   # → clarifying questions
uv run panos interview answer itv_… q1 "card payments only"            # or --skip
uv run panos interview complete itv_…           # draft BRD/FRD/NFR/backlog/APIs/data + stack ADR
uv run panos req list -p shop                   # review; edit with `panos req edit`
uv run panos req approve -p shop BR-001 --exclude FR-002   # cascades to descendants
uv run panos req approve -p shop ADR-001        # the stack must be approved before scaffolding
uv run panos req trace -p shop                  # BR → FR → stories → ACs → tests, with gaps
uv run panos project estimate shop              # points, hours, cost, sprint plan
uv run panos project scaffold shop --open       # skeleton + docs/requirements + .claude/skills, opens the IDE
```

The `justfile` wraps the same commands (`just check`). CI runs them on Windows, macOS and Linux for Python 3.12 and 3.14 (`.github/workflows/ci.yml`).

Runtime data never lives in the repository. It is stored in per-user directories; `panos config path` shows them. The default project workspace is `~/Panos/Projects`.

## Core technology

- Core/runtime: Python 3.12+
- Local API: FastAPI
- Desktop: Tauri + React + TypeScript
- Web dashboard: React + TypeScript
- Mobile (later): React Native + TypeScript
- Local persistence: SQLite
- Control-plane persistence: PostgreSQL
- AI: pluggable local + cloud providers

## Non-negotiable safety rules

Panos never gives an LLM unrestricted shell/desktop access. All actions pass through structured tools, policy evaluation, permissions, validation and audit logging.

Email sending is forbidden. Git commits, Docker execution, Jira/Azure DevOps writes, calendar writes, production deployments and other consequential operations require explicit approval.
