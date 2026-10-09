# ADR-019: Secret storage

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** NFR-SEC-003, NFR-SEC-004

## Context
API keys, OAuth tokens and the node HMAC key must be encrypted at rest.

## Decision
- Use `keyring`: Windows Credential Manager, macOS Keychain, Linux Secret Service.
- Secrets are referenced by name in config and never stored in SQLite, config files, logs or prompts.
- On headless Linux without a backend, Panos refuses to store secrets unless the user opts in to an encrypted file backend protected by a passphrase.

## Alternatives considered
Plaintext `.env` or config (unacceptable); a custom encrypted vault only (reinvents OS facilities).

## Consequences
Tests use an in-memory keyring backend.
