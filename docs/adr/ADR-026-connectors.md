# ADR-026: External connectors (mail, calendar, drives, trackers)

- **Status:** Accepted (by the owner, 2026-10-13; Proposed at M8)
- **Date:** 2026-10-12
- **Traces:** BR-012, BR-016, BR-019, BR-020, FR-017, FR-018, FR-019, NFR-SEC-002, NFR-SEC-004, NFR-PRV-003, US-042, US-043, US-044

## Context
M8 connects Panos to Gmail, Google Calendar, Google Drive, OneDrive/SharePoint, Jira Cloud and Azure DevOps (owner decisions of 2026-10-09). These are the first integrations that hold long-lived credentials and that can change things outside the machine (calendar events, work items, mail drafts). BR-019 forbids sending email at all.

## Decision
1. **Accounts.** A `connector_account` row (migration 0007) holds only non-secret facts: kind, label, base URL, granted features, privacy label, status. Tokens, API tokens and PATs live in the OS keyring under `connector:<id>`. The owner's OAuth client registrations live under `connector-client:<kind>`. No secret appears in the database, logs, audit or API responses.
2. **Authentication.**
   - Google: installed-app OAuth with a loopback redirect and PKCE (S256), using the owner's own desktop client.
   - Microsoft: the device-code flow with the owner's public client.
   - Jira Cloud: e-mail and API token (HTTP Basic).
   - Azure DevOps: a personal access token.

   Refresh tokens are rotated and persisted. A failed refresh marks the account `NEEDS_REAUTH`.
3. **Endpoint allowlist.** Each connector has an allowlist of method + host + path patterns, checked in `ConnectorHttp` **before** a request is sent. Gmail's send, trash, delete, modify, import and settings endpoints are not on it, and a draft id can never be `send`. Drive and Graph are read-only. Jira and ADO have no delete endpoint. Redirects are not followed, except Graph's `/content` download redirect: it is followed once, over HTTPS, **without** the Authorization header.
4. **Capabilities and policy.** Reads are P0 (`email.read`, `calendar.read`, `pm.read`). A draft is `email.draft` (P1). Calendar writes and work-item change sets are P3. They are prepared into the exact request(s), and the approval is bound to that exact payload (M2/M5 approval binding). The registry refuses any tool declaring `email.send`.
5. **Privacy.** Content read through a connector is untrusted data (ADR-023) and carries the account's privacy label, which is `LOCAL_ONLY` until the owner relaxes it per account. Chat replies built from mail or calendar keep the stricter label.
6. **Knowledge collections.** Folders, selected websites, Google Drive folders and OneDrive/SharePoint folders are indexed as *collections* outside projects. They share the knowledge index (the collection id is the scope) and are `LOCAL_ONLY` by default. Retrieval follows FR-020: the current project first, then company knowledge (folders, drives), then external sources (websites). The tier also sets the prompt-context scope, so context assembly keeps that order.

## Alternatives considered
- **Panos-owned OAuth clients:** these need Google verification for restricted scopes and would make Panos a data processor. Rejected for a personal, local-first tool.
- **IMAP/SMTP for mail:** SMTP can send, and app passwords are weaker than OAuth. Rejected.
- **Trusting scopes alone to block sending:** Gmail has no draft-only scope (`gmail.compose` can send, R-12). The allowlist plus the absence of a send tool enforce it in code.

## Consequences
- The owner must register their own Google desktop client and Microsoft app once (`docs/planning/connectors-setup.md`).
- A forbidden endpoint cannot be reached through Panos even by a bug or a manipulated plan. New features need an explicit allowlist change, which shows up in review.
- Microsoft 365 mail/calendar, Outlook COM and Thunderbird are deferred.

## Implementation notes (M8, 2026-10-12)
- Code: `panos_core/connectors/` (service, oauth, http, allowlists, gmail, gcalendar, workitems), and the tools `email.*`, `calendar.*`, `workitem.*` in `panos_core/tools/builtin/`.
- Collections: `panos_core/knowledge/collections.py` and `sources.py`.
- Surfaces: `/connectors`, `/knowledge/collections`, `/knowledge/search`; `panos connect …`, `panos knowledge …`; desktop Settings → Connected accounts / Knowledge sources.
- Work-item changes record the created item as `workitem:<system>:<key>` evidence on the linked requirement (AC-US042-02).
- Collections are indexed lexically only. Embeddings for collections are a follow-up (see `considerations.md`).
