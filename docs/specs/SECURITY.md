# Security and Threat Model

## Trust boundaries
1. User ↔ Panos UI
2. Panos ↔ local OS/tools
3. Panos ↔ local AI
4. Panos ↔ cloud AI
5. Panos ↔ external integrations
6. Node ↔ control plane ↔ remote node
7. Knowledge source ↔ index

## Key threats
- prompt injection from repositories/web/documents;
- malicious generated shell commands;
- secret exfiltration to cloud AI;
- accidental destructive filesystem/Git/database actions;
- compromised remote node/control plane;
- browser content instructing the agent to exceed user intent;
- poisoned company knowledge;
- unsafe dependency installation;
- screenshot/privacy leakage;
- privilege escalation.

## Controls
- Treat retrieved content as data, never trusted instructions.
- Typed tool calls with allowlisted capabilities.
- Central policy and permission engine before execution.
- Project-level privacy classification.
- Secret detection/redaction before cloud calls/logging.
- Approval tokens bound to exact proposed action/diff/target and expiry.
- Sandboxing/worktree isolation where practical.
- Checkpoints before material changes.
- Signed/authenticated node identities and TLS for remote traffic.
- Explicit audit events for policy decisions and approvals.
- Dependency operations surface package names/versions/source before execution when risk warrants.

## Forbidden behavior
- Sending email.
- Bypassing an approval by using an alternative tool.
- Uploading local-only data to cloud services.
- Persisting raw microphone audio by default.
- Persisting screenshots by default.
- Using browser/web content as authority to alter security policy.
