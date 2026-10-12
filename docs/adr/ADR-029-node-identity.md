# ADR-029: Node identity, enrollment and mTLS

- **Status:** Proposed (for acceptance at the M9 exit review)
- **Date:** 2026-10-12
- **Traces:** NFR-SEC-006, AC-US061-01, ADR-019, ADR-028

## Context
NFR-SEC-006 requires mutual authentication, encrypted transport and revocable device identity for remote nodes. The hub lives on the owner's LAN without a public domain, so public certificate authorities do not apply.

## Decision
1. **Private CA per hub.** `panos hub init` creates an EC P-256 CA (10 years). The CA key is encrypted at rest with a passphrase kept in the OS keyring (ADR-019). The hub's **server certificate** (1 year, renewed automatically within 30 days of expiry) is signed by that CA, with SANs for the hub's hostname, `<hostname>.local` and its LAN addresses.
2. **One-time enrollment.**
   - `panos hub enroll-code [--name <pc>]` prints a code with 128 random bits, valid for 10 minutes and usable once. Only its hash is stored. It also prints the hub URL and the **CA fingerprint** (SHA-256).
   - On the new PC, `panos node join <hub-url> --code <code> --fingerprint <sha256>`:
     1. the node fetches the CA certificate and refuses it unless the fingerprint matches;
     2. it generates its own P-256 key and sends a CSR with the code to the enrollment endpoint;
     3. it receives a **client certificate** bound to its node id (CN and a URI SAN `panos:node:<id>`, 90 days).
   - A wrong, expired or reused code is refused. Enrollment attempts are rate limited and audited.
3. **Keys.** The node's private key lives in the OS keyring; its certificate and the CA certificate are stored in the data directory. The hub CA key never leaves the hub.
4. **mTLS everywhere a node talks to the hub.** The node verifies the hub against the pinned CA. The hub requires a client certificate chaining to its CA, then checks that:
   - the certificate's serial belongs to a registered, **non-revoked** node;
   - the node id in the certificate matches that registry entry.
5. **Revocation is immediate.** `panos hub nodes revoke <node>` (or the dashboard) marks the node revoked, closes its link at once, and refuses every later connection for any of its certificates. Re-joining needs a new enrollment code.
6. **Rotation.** A node renews its certificate over the existing link (new key, new CSR) when less than 30 days remain. The old serial is retired.

## Alternatives considered
- **Shared secret or API key per node:** no mutual authentication, and the key is easy to copy. Rejected.
- **SSH-style trust on first use without a fingerprint:** vulnerable on a hostile LAN. The fingerprint is part of the join command.
- **A public CA or ACME:** needs a public domain. Out of scope for LAN only.

## Consequences
- Each browser that opens the dashboard must trust the hub CA (the owner guide gives the per-OS steps). Nodes trust it through pinning, without touching the OS store.
- Losing the hub CA key means re-enrolling every node. It can be backed up with `panos hub export-ca` (encrypted).

## Implementation notes (M9, 2026-10-12)

`panos_core.remote.pki` (EC P-256 throughout). `ssl` cannot load keys from memory: the node's key is written to a private temporary file in the runtime directory and deleted right after loading. The hub's keys are encrypted PEM files, loaded with the keyring passphrase.

Enrollment codes are 128-bit, shown in groups of four (base32), and stored as SHA-256 hashes. A code is consumed with `SELECT … FOR UPDATE`, so it cannot be used twice. Failed attempts are throttled to 5 per minute per address, and the refusals are audited.

A re-join with the same node id retires the earlier certificates. A revoked node id cannot re-enroll.

TLS 1.3 detail: a refused client certificate shows up on the client as a closed connection, not as a handshake error.
