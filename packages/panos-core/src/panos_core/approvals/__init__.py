"""Action approvals bound to an exact proposed action (PERMISSIONS.md, ADR-006)."""

from panos_core.approvals.service import (
    ApprovalDecision,
    ApprovalError,
    ApprovalService,
    action_hash,
)

__all__ = ["ApprovalDecision", "ApprovalError", "ApprovalService", "action_hash"]
