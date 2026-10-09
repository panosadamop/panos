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

## Implementation notes (M4, 2026-10-09)

- **Role separation:** trusted text (system prompt, the user's words) and untrusted text (retrieval, tool output) never share a message.
- **Envelopes:** untrusted text is wrapped in `<untrusted kind source privacy>` envelopes. Any `<untrusted` / `</untrusted` sequence inside the content is escaped, so an envelope cannot be closed or forged from inside. The system prompt states the data-not-instructions rule.
- **Routing is decided before the model runs and from labels only.** Content that tells the model to "use the cloud" cannot change the route (`test_prompt_injection_in_retrieved_content_cannot_reroute`).
