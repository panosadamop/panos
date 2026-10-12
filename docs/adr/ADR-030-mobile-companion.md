# ADR-030: Mobile companion (PWA first)

- **Status:** Proposed (for acceptance at the M10 exit review)
- **Date:** 2026-10-12
- **Traces:** BR-026, US-062, ADR-028, ADR-029

## Context
BR-026 asks for a mobile client with conversation, task monitoring and approval/rejection. The spec sketched React Native. Owner decisions (2026-10-12): both, **PWA first**, text only. The hub is LAN-only and its dashboard already signs in with passkeys (ADR-028).

## Decision
1. **A PWA served by the hub.** `apps/dashboard` gets a phone layout, a web app manifest and a service worker. On a phone it becomes an installable app showing the companion views:
   - conversation;
   - jobs;
   - approvals (the exact action, approve/reject).

   The desktop browser keeps the full dashboard.
2. **The service worker caches only the app shell** (HTML, JS, CSS, icons). API responses are never cached, so job details or local-only summaries cannot linger on the phone.
3. **Conversation through the hub.**
   - The phone picks a PC; the hub sends an allowlisted `converse` message (text, optional project, conversation id).
   - That PC's conversation service answers under its own policy, routing and privacy labels. A LOCAL_ONLY project stays on local models on that PC.
   - Rule-based intents such as "list projects" work as usual. Work requests that would start a job need that PC's normal approvals.
   - The hub stores conversation metadata only (who, when, which PC), not the text.
4. **No third-party push.** Web Push goes through browser vendors' internet services, which conflicts with LAN-only. The PWA polls while open and shows a badge count of pending approvals.
5. **Sign-in** uses the same passkeys (Face ID, fingerprint). The phone must trust the hub CA (guide per OS) and reach the hub by host name.
6. **Native app later.** The versioned hub OpenAPI plus the conversation endpoint is the contract. Native passkeys need an associated domain (a public DNS name with `apple-app-site-association` / `assetlinks.json`), which a LAN-only hub does not have: a native app would need either a domain or another sign-in method. That decision is deferred.

## Alternatives considered
- **React Native now:** more tooling (Xcode, Android SDK, signing, sideloading) for the same features. Deferred.
- **Web Push for approvals:** better latency, but it needs internet push services. Rejected for now.

## Consequences
- Approvals on the phone are noticed when the app is open (badge plus polling), not through system notifications.
- Using the companion away from home needs a VPN to the LAN (out of scope).

## Implementation notes (M10, 2026-10-12)

**Code:**
- `apps/dashboard`: `public/manifest.webmanifest` (start URL `/?app=companion`, which opens on Talk), `public/sw.js` (shell cache, `/api/` never cached), phone layout, Talk view, approvals badge (`setAppBadge` where supported, plus the title count).
- Protocol `converse` (hub → node) is answered by `NodeRemoteHandler.converse` through the node's `ConversationService`.
- The hub's `POST /converse` has a 10-minute wait for local models; its audit records `conversation.relayed` (metadata only).
- The hub serves `.webmanifest` as `application/manifest+json`. The CSP is unchanged: bars are `<meter>` elements, with no inline styles.

**Tests:**
- Vitest + axe on all 10 dashboard views.
- A real node answers a relayed message ("list projects", rule-based) through the hub.
- Real Chromium at 390×844: passkey sign-in, opens on Talk, manifest content type, service worker active and controlling, no horizontal scroll, no CSP violation.

**Contract:** `docs/api/mobile-contract.md`. **Owner guide:** `docs/planning/mobile-and-estimates.md`.
