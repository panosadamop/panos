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
