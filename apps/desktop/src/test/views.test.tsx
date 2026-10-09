import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { beforeEach, describe, expect, it } from "vitest";

import type { ViewId } from "../App";
import { MESSAGES } from "../i18n";
import { event, fakeApi, ndjson, renderApp, services, type FakeApi } from "./fakes";

let api: FakeApi;

beforeEach(() => {
  api = fakeApi();
  api.on("GET", "/api/v1/jobs", {
    jobs: [{ id: "job_1", kind: "coding", objective: "Add multiply", status: "RUNNING", status_reason: "started",
      project_id: "prj_1", phase: "implement", pending_approval: null, created_at: "2026-10-09T10:00:00Z",
      started_at: null, completed_at: null, running: true, budget: {} }],
  });
  api.on("GET", "/api/v1/jobs/job_1", {
    id: "job_1", kind: "coding", objective: "Add multiply", status: "RUNNING", status_reason: "started",
    project_id: "prj_1", phase: "implement", pending_approval: null, created_at: "2026-10-09T10:00:00Z",
    started_at: null, completed_at: null, running: true, budget: {},
    result: { summary: "Add multiply", changed_files: [{ status: "M", path: "calc.py" }], unresolved: [] },
    steps: [{ sequence: 1, agent: "agent:planner", tool: null, status: "SUCCEEDED", input_summary: "Plan the implementation",
      output_summary: "two steps", started_at: null, completed_at: null }],
    checkpoints: [{ id: "chk_1", reference: "abc", created_at: "2026-10-09T10:00:00Z" }],
  });
  api.on("POST", "/api/v1/jobs/job_1/pause", (_b: unknown) => ({ id: "job_1" }));
  api.on("GET", "/api/v1/projects", { projects: [{ id: "prj_1", name: "orders", path: "C:/p/orders",
    node_id: "nod_1", privacy_mode: "LOCAL_ONLY", preferred_ide: null, status: "ACTIVE", knowledge_enabled: true,
    policy_overrides: {}, created_at: "2026-10-09T10:00:00Z" }] });
  api.on("GET", "/api/v1/apps", { apps: [{ key: "vscode", name: "Visual Studio Code", kind: "editor",
    executable: "code.exe", version: "1.95", source: "registry", aliases: [] }] });
  api.on("GET", "/api/v1/ai/providers", [{ id: "ollama", kind: "ollama", locality: "local", ok: true, detail: "",
    models: [], has_key: false }, { id: "anthropic", kind: "anthropic", locality: "cloud", ok: false,
    detail: "no API key configured", models: [], has_key: false }]);
  api.on("GET", "/api/v1/ai/usage", { since: "2026-10-01", total_cost_usd: 0.12, monthly_budget_usd: 20, rows: [] });
  api.on("GET", "/api/v1/audit", { events: [{ id: "aud_1", created_at: "2026-10-09T10:00:00Z", actor: "user",
    event_type: "job.created", action: "job.create", policy_decision: null, project_id: null, job_id: "job_1",
    metadata: {}, hash: "h" }] });
  api.on("GET", "/api/v1/health", { status: "ok", version: "0.1.0", api_version: "v1", node_id: "nod_1",
    uptime_s: 12, database: "ok" });
});

describe("[FR-002] conversation", () => {
  it("streams the answer and shows model, locality and privacy", async () => {
    api.on("POST", "/api/v1/conversation/messages", () =>
      ndjson([
        { type: "start", conversation_id: "cnv_1", intent: "chat", privacy: "LOCAL_ONLY" },
        { type: "route", provider: "ollama", model: "qwen2.5:3b", locality: "local", reasons: [] },
        { type: "text", text: "Γεια " },
        { type: "text", text: "σου" },
        { type: "reply", reply: { conversation_id: "cnv_1", message_id: "m", text: "Γεια σου", privacy: "LOCAL_ONLY",
          intent: "chat", provider: "ollama", model: "qwen2.5:3b", locality: "local", route_reasons: [], sources: ["a.py#L1-L2"],
          tool: null, latency_ms: 5 } },
      ]),
    );
    const { services: s } = services();
    renderApp("conversation", s);
    await userEvent.type(screen.getByLabelText(/Ask Panos/), "γεια{Enter}");
    expect(await screen.findByText("Γεια σου")).toBeInTheDocument();
    expect(screen.getByText(/qwen2.5:3b · local · LOCAL_ONLY · Sources: a.py#L1-L2/)).toBeInTheDocument();
    const call = api.calls.find((c) => c.path === "/api/v1/conversation/messages");
    expect(call?.body).toMatchObject({ text: "γεια", stream: true });
    expect(call?.auth).toBe("Bearer t0ken");
  });

  it("explains a privacy refusal and shows what voice heard", async () => {
    api.on("POST", "/api/v1/conversation/messages", () =>
      ndjson([{ type: "start", conversation_id: "c", intent: "chat", privacy: "LOCAL_ONLY" },
        { type: "error", error: { detail: "this request involves local-only content and no suitable local model is available" } }]),
    );
    const { services: s, socket } = services();
    renderApp("conversation", s);
    await userEvent.type(screen.getByLabelText(/Ask Panos/), "secret{Enter}");
    expect(await screen.findByText(/local-only content/)).toBeInTheDocument();
    act(() => {
      socket().open();
      socket().receive({ type: "auth.ok", v: 1, epoch: "E", seq: 0 });
      socket().receive(event("voice.transcript", { text: "άνοιξε το orders", language: "el", language_probability: 1,
        duration_s: 1, stt_s: 1, trigger: "push_to_talk" }));
    });
    expect(await screen.findByText("άνοιξε το orders")).toBeInTheDocument();
  });
});

describe("[US-052] jobs and [AC-US040-01] approvals", () => {
  it("shows the step timeline and pauses a running job", async () => {
    const { services: s } = services();
    renderApp("jobs", s);
    await userEvent.click(await screen.findByRole("button", { name: /Add multiply/ }));
    expect(await screen.findByText("Plan the implementation")).toBeInTheDocument();
    expect(screen.getByText("calc.py")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/v1/jobs/job_1/pause")).toBe(true));
  });

  it("shows the exact action and approves it", async () => {
    let pending = [{ id: "apr_1", project_id: "prj_1", job_id: "job_1", action_type: "git.commit", target: "C:/p/orders",
      payload_hash: "a".repeat(64), summary: "Commit 2 path(s): feat: multiply\n calc.py | 3 +++", status: "PENDING",
      requested_at: "2026-10-09T10:00:00Z", expires_at: "2026-10-09T10:30:00Z", decided_by: null, decided_at: null }];
    api.on("GET", "/api/v1/approvals", () => ({ approvals: pending }));
    api.on("POST", "/api/v1/approvals/apr_1/decision", () => {
      pending = [];
      return { approval: {}, token: "x" };
    });
    const { services: s } = services();
    renderApp("approvals", s);
    const card = (await screen.findByText("git.commit")).closest("li")!;
    expect(within(card).getByText(/calc.py \| 3/)).toBeInTheDocument();
    expect(within(card).getByText("a".repeat(64))).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText("Nothing waits for your approval.")).toBeInTheDocument());
    const decision = api.calls.find((c) => c.path === "/api/v1/approvals/apr_1/decision");
    expect(decision?.body).toEqual({ decision: "approve", comment: "" });
  });
});

describe("[NFR-ACC-001] i18n and keyboard access", () => {
  it("has the same keys in Greek and English and switches language", async () => {
    expect(Object.keys(MESSAGES.el).sort()).toEqual(Object.keys(MESSAGES.en).sort());
    const { services: s } = services();
    renderApp("settings", s, "el");
    expect(screen.getByRole("button", { name: "Συνομιλία" })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("English"));
    expect(screen.getByRole("button", { name: "Conversation" })).toBeInTheDocument();
  });

  it("switches views with Alt+number", async () => {
    const { services: s } = services();
    renderApp("conversation", s);
    fireEvent.keyDown(window, { key: "2", altKey: true });
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jobs" })).toHaveAttribute("aria-current", "page");
  });
});

describe("[US-051] voice settings", () => {
  it("shows the node's voice settings and changes them", async () => {
    api.on("GET", "/api/v1/voice/status", {
      enabled: true, running: true, state: "idle", wake_word: null, wake_word_enabled: false,
      tts: true, voice_notifications: false, language: "el", last_transcript: null, last_error: null,
      restarts: 0, missing_models: [],
    });
    api.on("PATCH", "/api/v1/voice/settings", {});
    const { services: s } = services();
    renderApp("settings", s);
    const notify = await screen.findByLabelText("Speak important notifications");
    await waitFor(() => expect(notify).not.toBeChecked());
    expect(screen.getByLabelText("Speak replies")).toBeChecked();
    expect(screen.queryByLabelText("Start Panos when I sign in")).toBeNull(); // desktop shell only
    await userEvent.click(notify);
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ voice_notifications: true });
  });
});

describe("[NFR-ACC-002] accessibility (axe)", () => {
  const views: ViewId[] = ["conversation", "jobs", "approvals", "projects", "apps", "settings", "audit", "status"];
  it.each(views)("%s has no axe violations", async (view) => {
    const { services: s } = services();
    const { container } = renderApp(view, s);
    await screen.findByRole("heading", { level: 2 });
    await new Promise((r) => setTimeout(r, 20)); // let data load
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
