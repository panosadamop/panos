# ADR-023: Prompt-injection defense

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** SECURITY, TOOLS (tool-result handling), KNOWLEDGE_MEMORY (rules), AC-US043-03, AC-US045-03

## Context
Repositories, web pages, documents and email can contain instructions aimed at the model.

## Decision
- Untrusted content (files, tool output, retrieval, web, email, transcripts of third parties) is wrapped in labeled envelopes with provenance and is never placed in system or developer roles.
- **Policy is independent of model output:** a tool request is evaluated the same way regardless of what the model claims.
- Approvals display the exact structured payload, so the user sees what will actually happen.
- Capabilities that must never happen (email send) do not exist in the runtime.
- A curated injection corpus (README, web page, document, email) runs in CI security tests.

## Alternatives considered
Relying on model-side instruction-following alone.

## Consequences
Injection can still degrade output quality, but it cannot escalate privileges beyond policy.
