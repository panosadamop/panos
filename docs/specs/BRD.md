# Business Requirements Document

## BR-001 Unified assistant
The system shall expose a single assistant identity named Panos across CLI, voice, desktop, web and future mobile interfaces.

## BR-002 Cross-platform
Panos shall operate on Windows, macOS and Linux using a common core and platform-specific adapters.

## BR-003 Hybrid AI
Panos shall support local and cloud AI providers and automatically route requests based on privacy, capability, cost, latency, availability and complexity.

## BR-004 Local-only privacy
Users shall be able to mark projects/folders local-only. Protected content and sensitive derived context shall not be transmitted to cloud AI services.

## BR-005 Voice
Panos shall support local wake-word activation, a global hotkey, bilingual Greek/English speech and mixed technical language.

## BR-006 Desktop productivity
Panos shall launch and interact with approved installed applications including JetBrains IDEs, VS Code, Sublime Text and installed browsers.

## BR-007 Development assistant
Panos shall act as an experienced full-stack developer capable of repository analysis, implementation, debugging, testing and code review.

## BR-008 Technology breadth
Panos shall support requested stacks including Python/FastAPI/Django, Java/Spring Boot/Vaadin, React/TypeScript, Node/NestJS, PHP/Laravel, PostgreSQL, MongoDB, Docker, Kubernetes and GCP/Azure/AWS.

## BR-009 Requirements lifecycle
Panos shall support Brief → clarification → BRD → FRD → NFR → architecture → APIs/OpenAPI → ERD → Epics/Features/Stories → acceptance criteria → tests → estimates → sprint plan → implementation.

## BR-010 Approval hierarchy
Requirements shall support hierarchical approval. Approval of a parent may approve descendants unless explicitly excluded.

## BR-011 Existing projects
Before materially modifying an existing repository, Panos shall analyze its stack, architecture, dependencies, conventions, documentation, Git history and test/build mechanisms.

## BR-012 Knowledge
Panos shall prioritize current-project and company knowledge, while allowing approved external/cloud resources when local information is insufficient and privacy policy permits.

## BR-013 Claude skills
Each full project shall generate and maintain project-specific Claude skills based on approved project information.

## BR-014 Estimation
Panos shall estimate work using story points, hours and person-days. Configurable role rates shall enable cost calculations.

## BR-015 Learning
Panos shall retain approved estimated-versus-actual project metrics to improve future estimation.

## BR-016 Project management integrations
Panos shall prepare Jira/Azure DevOps work-item changes but require explicit approval before external writes.

## BR-017 Git governance
Panos may inspect and modify working trees autonomously. Git commit/push/merge/shared-history changes and destructive branch/tag actions require approval.

## BR-018 Docker governance
Execution of Docker operations requires approval. Inspection and proposal generation do not.

## BR-019 Email boundary
Panos may read, search, summarize and draft email for supported accounts/clients, but shall never send email.

## BR-020 Calendar
Panos may read calendars and identify conflicts. Calendar writes/responses require approval.

## BR-021 Screen understanding
On command, Panos shall capture and analyze the active screen/window, subject to privacy and retention policies.

## BR-022 Browser boundary
Panos may use browsers for reading, navigation and research. It shall not autonomously perform consequential submissions, transactional form completion, or uploads.

## BR-023 Long-running tasks
Panos shall execute long-running autonomous tasks, interrupting the user primarily for approvals, blockers or business decisions.

## BR-024 Multi-PC
Panos shall support installation on multiple computers and secure remote task dispatch between authorized nodes.

## BR-025 Dashboard
A React web dashboard shall show nodes, projects, jobs, agents, approvals, costs, logs and requirement traceability.

## BR-026 Mobile
A future mobile client shall provide conversation, task monitoring and approval/rejection capabilities.

## BR-027 Offline operation
Core voice, coding, requirements, repository analysis and PC-control capabilities shall remain usable without internet when suitable local models are available.

## BR-028 Documents
Panos shall generate professional Markdown, Word, Excel, PowerPoint and PDF artifacts.

## BR-029 Workspace
Default project workspace shall be `~/Panos/Projects`, while projects anywhere on accessible disks may be registered.
