# Test Strategy

## Unit tests
Prioritize policy engine, permission decisions, approval binding, AI routing, privacy filtering, state machines, traceability and tool validation.

## Integration tests
- local API ↔ orchestrator
- orchestrator ↔ tool runtime
- repository analyzer against fixture repos
- model provider adapters with mocks
- STT/TTS adapters
- Git sandbox repositories
- calendar/email read-only adapters

## End-to-end
- activate via CLI → open project → analyze → implement fixture change → test → request commit approval
- create project from brief through requirements approval and scaffolding
- local-only project attempts cloud route and is blocked
- Docker execution waits for approval
- email draft succeeds while send capability is unavailable
- pause/resume/cancel long job

## Security tests
- prompt injection in README/web page/document
- path traversal
- shell argument injection
- secret redaction
- forged/replayed approval
- unauthorized remote node
- cloud exfiltration attempt from local-only project

## Cross-platform CI
Run common tests on Windows, macOS and Linux. OS adapter contract tests must exist for each platform.
