# Logical Data Model

> **Amendments (2026-10-08, ADR-006/ADR-014):** `Approval` is split into `ActionApproval` (tool actions: action_type, target, payload_hash, summary, status PENDING/APPROVED/REJECTED/EXPIRED/INVALIDATED/CONSUMED, decided_by, requested_at, decided_at, expires_at, consumed_at) and `RequirementApproval` (requirement_id, requirement_version, decision, decided_by, comments, cascaded_from_id, excluded_descendant_ids, decided_at). `Requirement.status` and `approval_state` are unified into one lifecycle plus a `stale` flag (ADR-014). Further entities (Conversation/Message, ToolInvocation, Application/Alias, ProjectPolicy, GeneratedSkill, AIUsage, Notification, Interview) are added by the milestones that need them.


Core entities:

## UserPreference
id, key, value, scope, updated_at

## Node
id, name, platform, capabilities, public_identity, status, last_seen_at

## Project
id, name, path, node_id, privacy_mode, preferred_ide, status, created_at

## RepositorySnapshot
id, project_id, commit_hash, stack_json, architecture_json, indexed_at

## Requirement
id, project_id, requirement_key, type, parent_id, version, title, body, status, approval_state

## TraceLink
id, project_id, source_type, source_id, target_type, target_id, relation

## Approval
id, project_id, job_id, action_type, payload_hash, summary, status, requested_at, decided_at, expires_at

## Job
id, project_id, node_id, objective, status, budget_json, started_at, completed_at

## JobStep
id, job_id, sequence, agent_type, tool_name, status, input_summary, output_summary, started_at, completed_at

## Checkpoint
id, project_id, job_id, type, reference, created_at

## AuditEvent
id, node_id, project_id, job_id, actor, event_type, action, policy_decision, metadata_json, created_at

## KnowledgeSource
id, scope, project_id, provider, uri, privacy_mode, content_hash, version, indexed_at

## KnowledgeChunk
id, source_id, locator, text_hash, embedding_ref, metadata_json

## ModelProvider / Model
capabilities, privacy eligibility, context size, cost metadata, health

## Estimate
id, project_id, item_id, story_points, hours, person_days, role, rate, cost, confidence

## ActualEffort
id, project_id, item_id, role, hours, captured_at

Use SQLite locally; PostgreSQL for control-plane/shared metadata. Keep domain repositories abstracted from database implementation.
