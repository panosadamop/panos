# System Architecture

> **Amendments (2026-10-08, ADR-002/ADR-015):** the Python node runs as a per-user daemon; the desktop shell starts or attaches to it. When the desktop is running, the Tauri shell owns the global hotkey, tray and desktop notifications; the Python OS adapter keeps a notification port with a console implementation for headless/CLI use. Windows is the first production adapter (ADR-022).


## Components

```text
Interfaces: Voice | CLI | Desktop | Web | Mobile(later)
                         |
                    Panos Gateway
                         |
                 Intent/Task Manager
                         |
                    Orchestrator
     +-------------------+--------------------+
     |                   |                    |
 Agent Runtime      Policy/Approval      Knowledge/Memory
     |                   |                    |
     +-------------------+--------------------+
                         |
                     Tool Runtime
                         |
 OS | IDE | Files | Shell | Git | Browser | Email | Calendar | PM
```

## Core services

### Local Panos Node
Runs on every PC and contains:
- local API/WebSocket server;
- orchestrator;
- agent runtime;
- policy/permission engine;
- tool registry/runtime;
- local knowledge index;
- voice pipeline;
- OS adapter;
- job/checkpoint manager;
- local SQLite state;
- secure node identity.

### Control Plane
Introduced after single-node MVP. Provides:
- node registry/presence;
- remote dispatch;
- centralized dashboard API;
- shared approvals;
- optional synchronized non-sensitive preferences/metadata;
- PostgreSQL persistence.

Execution remains on the target node. Do not automatically copy project files between nodes.

## Desktop UI
Tauri + React + TypeScript. Talks to the local node via authenticated loopback API/IPC. Shows conversation, current task, steps, approvals, terminal summaries, projects and settings.

## Web UI
React + TypeScript against control-plane APIs. Must not directly access node files or execute arbitrary commands.

## Domain boundaries
- conversation
- projects
- requirements
- agents
- tools
- policy/permissions
- approvals
- jobs
- knowledge
- AI routing
- voice
- integrations
- audit
- nodes
- notifications
- documents

## OS adapter interface
Provide contracts such as:
- list_installed_apps
- launch_app
- focus_window
- active_window_metadata
- capture_active_window
- global_hotkey
- notification
- open_path

Implement separate Windows/macOS/Linux adapters.

## Preferred automation order
1. Official/native API
2. Application CLI/protocol
3. Plugin/extension
4. OS accessibility/UI automation
5. Keyboard/mouse automation as last resort
