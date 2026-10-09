# ADR-018: API and event contracts

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** API.md, NFR-MNT-001

## Context
The desktop, CLI and later web/mobile clients need typed, versioned contracts that cannot drift from the server.

## Decision
- REST under `/api/v1`. FastAPI generates OpenAPI 3.1.
- **Events:** WebSocket messages use the envelope `{v, type, id, ts, correlation_id, job_id?, payload}`. Payloads are Pydantic discriminated unions included in the OpenAPI components.
- **TS client:** generated with `openapi-typescript` + `openapi-fetch` into `packages/ts-client`. WebSocket messages are validated at runtime with zod at the client boundary.
- **Drift check:** CI regenerates the client and fails on any diff.
- Errors use RFC 9457 `application/problem+json`.

## Alternatives considered
Hand-written TS types (drift); GraphQL (unneeded complexity).

## Consequences
Schema changes are visible in review via generated diffs.

## Implementation notes (M4, 2026-10-09)

- **Event envelope:** `{v: 1, type, id, ts, correlation_id, job_id?, payload}`. In-process `EventBus` with bounded per-subscriber queues; a slow subscriber loses its oldest events.
- **`/api/v1/ws/events`:** Host/Origin checks run in the middleware before the handshake. Then the client must send `{"type":"auth","token":…}` within 5 s; otherwise the socket closes with 1008. Event types so far: `conversation.message`, `conversation.delta`.
- **`POST /conversation/messages`:** with `stream: true`, returns NDJSON (`start`, `route`, `text`…, `reply` | `error`). Without it, returns one JSON reply. Privacy refusals are `503` problems with `reason: "no_route"` and the rejected candidates.
- **(M5)** More event types: `job.status` (every transition, with reason) and `notification` (approvals, blockers, questions, completion, failure only). Jobs API: `POST/GET /jobs`, `GET /jobs/{id}` (steps, checkpoints, report), `pause|resume|cancel|rollback`, `GET /jobs/{id}/events` (the job's audit trail).
- **(M7)** Every event has a per-node `seq`, and the bus keeps the last 2000 events. The WebSocket `auth` message may carry `since` and `epoch`. If the epoch matches the node's and `since` is still in the buffer, the missed events are replayed in order. Otherwise the server sends `{"type":"resync"}` and the client reloads its state over REST. `auth.ok` returns the current `epoch` and `seq`. The client drops duplicates by `seq` and reconnects with backoff.
- **(M7)** The event catalogue (`panos_core/event_catalog.py`: conversation, job, notification and `voice.*` events, plus job statuses) is the source for the generated `apps/desktop/src/api/events.ts`. The REST client types are generated from `docs/api/openapi.v1.json`. `pnpm check:gen` and a pytest test fail on drift.
- **(M7)** New endpoints: `/voice/{status,start,stop,listen,listen/stop,speak,stop-talking,utterance}`, `PATCH /voice/settings`, `POST /screen/analyze`.
