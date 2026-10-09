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
