# ADR-028: Control plane (hub) for multiple PCs

- **Status:** Proposed (for acceptance at the M9 exit review)
- **Date:** 2026-10-12
- **Traces:** BR-024, BR-025, FR-026, FR-027, NFR-SEC-006, NFR-PRV-001, US-060..062, AC-US060-01, AC-US061-01..03, AC-US062-01/02, ADR-004, ADR-029

## Context
The owner wants to see and use Panos across their own PCs (Windows plus macOS/Linux) and to monitor jobs and approvals in a web dashboard. Owner decisions (2026-10-12):
- the control plane runs on one of the owner's PCs;
- it is reachable on the LAN only;
- dashboard sign-in is by passkey.

ARCHITECTURE requires that execution stays on the target node and that project files are never copied between nodes. SECURITY lists a compromised remote node or control plane as a threat.

## Decision
1. **Hub role.** The control plane is a separate service (`services/control-plane`, started with `panos hub serve`) on the owner's chosen PC. That PC also runs an ordinary node, which joins its own hub like any other. Persistence is PostgreSQL (ADR-004), in Docker Compose next to the hub, with its own migrations.
2. **Two listeners in one process**, both TLS with the hub certificate (ADR-029), bound to the LAN interfaces only:
   - **Node link** (default port 48701): a WebSocket server that **requires a client certificate** (mTLS) issued by the hub CA. The node identity comes from the verified certificate and is checked against the node registry (revocation) on every connection.
   - **Dashboard and API** (default port 48700): HTTPS for the browser. Sign-in is a passkey (WebAuthn). The session is an HttpOnly, SameSite=Strict cookie with a CSRF token. This port also serves the one-time enrollment endpoint.
3. **Nodes dial out.** Each node keeps one outbound WebSocket to the hub. Nodes open no new inbound ports, and the hub reaches a node only over that link.
4. **Allowlisted message protocol.** Every message is `{v, id, type, payload}`, validated with pydantic on both sides; unknown types are rejected and logged.
   - **Node → hub:** `hello` (capabilities, platform, version), `heartbeat`, `inventory` (projects: name, privacy, technologies), `job` (status and step list), `approval` (pending/decided), `reply` (to a query).
   - **Hub → node:** `dispatch` (objective, project, kind, budget), `control` (pause/resume/cancel a job), `decision` (approve/reject an approval), `query` (read-only views: job detail, audit page, AI usage, traceability, approval detail).

   There is **no** message for files, shell, tools or settings (AC-US062-02).
5. **The target decides.** A dispatched job is created by the target node's runner under the target's own policy, project overrides and approvals (AC-US061-03). The target can refuse (unknown project, offline, policy) and says why. Only enrolled, non-revoked, online nodes can be targeted (AC-US061-01). The dispatch request names the target node, and the confirmation shows it (AC-US061-02).
6. **Shared approvals.** Nodes mirror pending approvals to the hub. A decision from the dashboard (or from another node through the hub) is sent to the target, which re-checks the approval and issues the single-use token **itself**. The hub never holds tokens. A node can refuse remote decisions (`hub.accept_remote_approvals`, default on); its audit records `decided_by=user@hub` and the hub audit records the passkey user.
7. **Privacy.** The hub is the owner's own device, not a cloud provider, but it stores as little as possible:
   - **Persisted:** node registry, inventories, job and approval metadata (ids, project, kind, status, timestamps, objective, action type, target, payload hash), and the hub audit.
   - **LOCAL_ONLY projects:** step summaries, results, diffs, tool output and approval summaries are **not persisted**. The dashboard fetches them live through a `query` and shows them without storing them.
   - **Never sent:** file contents.
8. **Hub audit.** Hash-chained like the node audit (ADR-020). It records sign-in, enrollment, revocation, dispatch, control and decisions.

## Alternatives considered
- **Hub on a cloud VM:** reachable anywhere, but job metadata leaves the owner's machines. Rejected for now (owner decision).
- **The hub calls each node's loopback API:** would need inbound ports and remote use of the loopback token. Rejected: nodes dial out.
- **Generic RPC (proxying the node REST API):** too broad; the hub could reach file and shell tools. Rejected for an allowlisted protocol.

## Consequences
- Remote dispatch only works while the hub PC is on and the nodes are on the same LAN.
- The dashboard needs a hostname (WebAuthn does not accept IP addresses as RP ID) and a trusted hub CA in each browser's OS store; the owner guide covers both.
- A compromised hub could dispatch jobs and approve actions. These still run under each node's policy, and nodes can refuse remote decisions. This is an accepted residual risk.

## Implementation notes (M9, 2026-10-12)

**Code:**
- **Hub:** `services/control-plane` (`panos_control_plane`):
  - `material` (CA, certificates, Compose file), `nodes` (codes, enrollment, peer check, revocation, renewal);
  - `links` (mTLS WebSocket server, mirrors with the privacy filter, revocation sweep);
  - `operations` (dispatch, control, decisions, live queries), `api`, `auth` + `passkeys` (WebAuthn, sessions, CSRF);
  - `dashboard` (static files, strict CSP), `audit` (hash chain with an advisory lock).
- **Node side:** `panos_core.remote` (`protocol`, `pki`, `membership`, `link`, `handler`, `publisher`); node API `/remote`.
- **Surfaces:** CLI `panos hub …` and `panos node …`; desktop Settings → Hub; web dashboard `apps/dashboard`.

**Behaviour:**
- **Inventory:** the node resends it whenever it changes, for example after a project is registered.
- **Mirror updates:** job and approval changes are pushed on status changes, plus a 2-second sweep for step progress.
- **Interactive approvals** (without a job) are refused remotely: the token would have to reach whoever asked on that PC.
- **The hub CLI** uses `admin.token` (owner-only file, loopback only), so revocation goes through the running hub and disconnects at once. A revocation written by another process is applied by the 5-second sweep.

**Deviations:**
- **Dispatch from a non-hub PC's CLI** (`panos job start --node`) is not implemented. Dispatch is available from the dashboard and with `panos hub dispatch` on the hub PC. Forwarding requests from nodes through the hub would need new upward message types; this is a follow-up.
- **Live updates in the dashboard** use polling (every 5 s) instead of a push channel to the browser.

**Tests** (embedded PostgreSQL 16):
- real TLS on loopback (enrollment, pinning, mTLS, revocation, renewal, protocol allowlist);
- a real node app: dispatch, then the target's own approval, then a decision from the hub;
- passkeys with a software authenticator and in real Chromium with a virtual authenticator;
- an end-to-end run with real `panos` processes on both sides.
