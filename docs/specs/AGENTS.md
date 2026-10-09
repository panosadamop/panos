# Agent Architecture

The user interacts only with Panos. Specialist agents are internal roles coordinated by the orchestrator.

## Core agents

### General Assistant
Answers general questions and routes actionable requests.

### Business Analyst
Clarifies briefs; creates BRD/FRD/business rules/use cases/backlog/acceptance criteria.

### Solution Architect
Creates architecture, technology decisions, APIs, integration design, ERD/data model, NFR mapping and ADRs.

### Developer
Implements requested features using repository conventions and approved architecture.

### QA Agent
Creates tests from acceptance criteria, executes applicable tests and reports evidence.

### DevOps Agent
Creates CI/CD, containers, Kubernetes and cloud infrastructure proposals/configuration. Execution follows approval policy.

### Reviewer
Independently reviews code for correctness, security, maintainability, architecture and requirement coverage.

### Project Planner
Creates estimates, dependencies, sprint plans and roadmap; compares estimated/actual effort.

## Agent contract
Each agent receives scoped context, approved facts, permissions and task objective and returns structured results. Agents do not execute OS actions directly; they request tools through the orchestrator.

## Handoffs
Handoffs must preserve requirement IDs, decisions, unresolved questions, assumptions and provenance.
