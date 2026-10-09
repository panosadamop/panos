# API Contract Outline

> **Amendments (2026-10-08, ADR-003/ADR-018):** loopback authentication uses a per-user bearer token plus Host/Origin validation. WebSocket events use the envelope `{v, type, id, ts, correlation_id, job_id?, payload}`. Additional endpoints (apps, tools, checkpoints/rollback, AI providers/budgets, knowledge, skills, estimates) are added per milestone; see `docs/planning/PLAN_PROPOSAL.md`.


All APIs are versioned under `/api/v1`. Generate OpenAPI from FastAPI models.

## Local Node
- GET `/health`
- GET `/capabilities`
- GET/POST `/projects`
- GET `/projects/{id}`
- POST `/projects/{id}/analyze`
- POST `/conversation/messages`
- POST `/jobs`
- GET `/jobs/{id}`
- POST `/jobs/{id}/pause`
- POST `/jobs/{id}/resume`
- POST `/jobs/{id}/cancel`
- GET `/jobs/{id}/events`
- GET `/approvals`
- POST `/approvals/{id}/decision`
- GET `/audit`
- POST `/screen/analyze-active`
- GET/PUT `/settings`

## Requirements
- GET/POST `/projects/{id}/requirements`
- PATCH `/requirements/{id}`
- POST `/requirements/{id}/approval`
- GET `/projects/{id}/traceability`

## WebSocket
`/ws/events` for conversation tokens, job progress, tool output summaries, approvals, notifications and node status.

## Control plane (later)
- node registration/presence
- secure remote job dispatch
- shared approval queue
- dashboard aggregates

## Security
Loopback API requires local session authentication. Remote APIs require strong user auth plus mutually authenticated node channels. Never expose arbitrary shell endpoints.
