# ADR-013: Panos-native coding executor

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** NFR-SEC-002, FR-006, BR-007, CLAUDE.md principle 3

## Context
External coding agents (Claude Code CLI, Aider, etc.) bring their own shell and file tools, which would bypass Panos policy, approvals and audit.

## Decision
- The Developer/QA/Reviewer roles run in a Panos-native agent loop that can only use Panos tools:
  - **structured edit:** search/replace patch with a preimage check, or whole-file write for new files;
  - filesystem, controlled shell, Git read and analysis tools.
- External coding agents are not used in the MVP. They may be reconsidered later only as a sandboxed adapter whose actions are proxied through the Panos tool runtime.

## Alternatives considered
Delegating implementation to Claude Code or Aider.

## Consequences
Panos owns its edit/test/repair loop and prompt engineering. Quality depends on the chosen models, especially for local-only projects.
