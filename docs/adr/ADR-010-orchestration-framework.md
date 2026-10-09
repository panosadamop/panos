# ADR-010: In-house orchestration framework

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** AGENTS, FR-022, NFR-MNT-001

## Context
Jobs, approvals, policy, checkpoints and audit have Panos-specific semantics that must be persisted and enforced uniformly.

## Decision
- An in-house orchestrator with:
  - a **table-driven job state machine** (states from FR-022, explicit allowed transitions, every transition persisted and audited);
  - an **agent role contract**: input = scoped context, approved facts, permissions, objective; output = a structured result.
- Agents never execute actions. They request tools through the orchestrator → tool runtime → policy.
- No LangGraph, LangChain or CrewAI in the core.

## Alternatives considered
LangGraph (its own checkpoint and state semantics would duplicate and compete with Panos jobs, approvals and audit); CrewAI (opinionated, less control).

## Consequences
More code to own, but every transition is testable and auditable. Frameworks can still be wrapped later behind the agent contract if needed.

## Implementation notes (M5, 2026-10-09)

- **Agent contract** (`panos_core/agents`): `AgentTask` = objective, approved facts, permissions, labelled untrusted context, handoff, role inputs. `AgentRole` = instructions + output model.
- Models answer with one JSON object. It is validated against the role's Pydantic model, with **exactly one corrective retry**; then the call fails closed.
- **Handoffs** carry requirement IDs (also extracted from the objective and the result), decisions, assumptions, open questions and provenance. They are merged across agents without duplicates.
- **Roles delivered:** Planner, Developer, Reviewer, QA. Business Analyst, Architect, DevOps and Project Planner arrive with their workflows (M6/M8).
- **Agents never act.** The coding workflow turns their results into `fs.*`, `shell.run` and `git.commit` tool calls through the tool runtime (policy, approvals, audit).
