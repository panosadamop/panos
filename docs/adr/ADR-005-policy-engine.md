# ADR-005: Policy engine

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** NFR-SEC-001, NFR-SEC-002, FR-011, PERMISSIONS, BR-017, BR-018, BR-019, BR-022

## Context
Every tool action must be evaluated against permission levels P0–P4, project scope and privacy before execution. The evaluator is security-critical and must be exhaustively testable.

## Decision
- An in-house, **pure-function** evaluator: `evaluate(request: PolicyRequest) -> PolicyDecision`.
- **Inputs:** action class, permission level, target (path, repo, host, node), project policy, privacy taint, actor (user, agent role) and job context.
- **Output:** `ALLOW`, `REQUIRE_APPROVAL` or `DENY`, plus reasons, matched rule IDs and the policy version.
- **Default deny:** an unknown action class is DENY.
- The default policy is YAML shipped as package data and mirrors PERMISSIONS.md.
- **Project overrides** may tighten any rule. Loosening is limited to listed P2 classes, by explicit audited user action. P3 and P4 can never be loosened by configuration.
- Every decision is audited (ADR-020).

## Alternatives considered
OPA/Rego (an extra runtime binary and policy language for a single-user desktop app); Casbin (RBAC-oriented, poor fit for action classes plus taint).

## Consequences
Policy is plain Python plus YAML, unit- and property-tested to ≥ 95% branch coverage (NFR-MNT-002). Policy changes require tests.
