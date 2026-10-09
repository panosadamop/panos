# ADR-006: Approval binding and tokens

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-010, PERMISSIONS (Approval object), AC-US040-02, AC-US041-02, AC-US041-03, SECURITY

## Context
An approval must authorize exactly one proposed action. Changing the action, replaying an approval or forging one must fail.

## Decision
- The action payload (action type, target, parameters, plus a diff/tree hash where relevant) is canonicalized with **RFC 8785 (JCS)** and hashed with SHA-256.
- An `ActionApproval` stores the hash, a human-readable summary, the target, the requesting job, the expiry and its status. Statuses: PENDING, APPROVED, REJECTED, EXPIRED, INVALIDATED, CONSUMED.
- **Token:** on approval, the node issues a token `HMAC-SHA256(node_key, approval_id ‖ payload_hash ‖ expiry)`. The node key is stored in the OS keyring (ADR-019).
- **Verification at execution time:** the runtime rebuilds the payload, re-hashes it and verifies the token, then atomically marks the approval CONSUMED (**single use**).
- **Failure:** a mismatch, expiry or prior consumption aborts execution and creates an audit event.
- **Git commits:** the payload includes the staged tree hash and the commit message. If the index changes, the approval is invalid.

## Alternatives considered
A boolean `approved` flag in the database (replayable, not bound to the payload); comparing payloads without canonicalization (fragile ordering issues).

## Consequences
Security tests cover forged, replayed, expired and mutated-payload cases. The UI shows the exact structured payload being approved.
