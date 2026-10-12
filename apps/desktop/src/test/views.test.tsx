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
  api.on("GET", "/api/v1/connectors", [{ id: "con_1", kind: "jira", label: "owner@example.com @ acme.atlassian.net",
    base_url: "https://acme.atlassian.net", scopes: ["workitems"], privacy_mode: "LOCAL_ONLY", status: "CONNECTED",
    created_at: "2026-10-09T10:00:00Z", last_used_at: null }]);
  api.on("GET", "/api/v1/knowledge/collections", [{ id: "col_1", name: "company", kind: "folder",
    location: { path: "C:/company" }, connector_id: null, privacy_mode: "LOCAL_ONLY", enabled: true,
    created_at: "2026-10-09T10:00:00Z", last_indexed_at: null }]);
  api.on("GET", "/api/v1/remote", { joined: true, hub_url: "https://hubpc:48700", node_id: "nod_1", name: "laptop",
    link: "connected", last_error: null, certificate_expires: "2027-01-10T00:00:00Z", ca_fingerprint: "SHA256:aa",
    accept_remote_approvals: true });
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

describe("[BR-001] [FR-002] connections, knowledge and exports (M8-07)", () => {
  it("connects Jira with a hidden token and relaxes an account's privacy", async () => {
    api.on("POST", "/api/v1/connectors/jira", (body: unknown) => ({ id: "con_2", kind: "jira",
      label: `${(body as { email: string }).email} @ acme.atlassian.net`, base_url: null, scopes: [],
      privacy_mode: "LOCAL_ONLY", status: "CONNECTED", created_at: "2026-10-09T10:00:00Z", last_used_at: null }));
    api.on("PATCH", "/api/v1/connectors/con_1", { id: "con_1" });
    const { services: s } = services();
    renderApp("settings", s);
    expect(await screen.findByText("owner@example.com @ acme.atlassian.net")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("May be sent to cloud models"));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ privacy_mode: "STANDARD" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Jira" }));
    await userEvent.type(screen.getByLabelText(/Jira site/), "acme.atlassian.net");
    await userEvent.type(screen.getByLabelText("Atlassian e-mail"), "me@example.com");
    const token = screen.getByLabelText("API token");
    expect(token).toHaveAttribute("type", "password");
    await userEvent.type(token, "secret-token-123");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Connected: me@example.com @ acme.atlassian.net")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/api/v1/connectors/jira")?.body).toEqual({
      site_url: "acme.atlassian.net", email: "me@example.com", api_token: "secret-token-123" });
  });

  it("asks for the OAuth client first, then shows the Microsoft device code", async () => {
    let registered = false;
    api.on("GET", "/api/v1/connectors/clients/microsoft", () => ({ kind: "microsoft", registered,
      client_id: registered ? "app-1" : null, tenant: "common" }));
    api.on("PUT", "/api/v1/connectors/clients/microsoft", () => {
      registered = true;
      return { kind: "microsoft", registered: true, client_id: "app-1", tenant: "common" };
    });
    api.on("POST", "/api/v1/connectors/microsoft", { id: "flow_1", kind: "microsoft", status: "pending",
      auth_url: null, user_code: "ABCD-EFGH", verification_uri: "https://microsoft.com/devicelogin",
      account_id: null, error: null });
    api.on("GET", "/api/v1/connectors/flows/flow_1", { id: "flow_1", kind: "microsoft", status: "pending",
      auth_url: null, user_code: "ABCD-EFGH", verification_uri: "https://microsoft.com/devicelogin",
      account_id: null, error: null });
    const { services: s } = services();
    renderApp("settings", s);
    await userEvent.click(await screen.findByRole("button", { name: "Microsoft 365" }));
    await userEvent.type(await screen.findByLabelText("Client ID"), "app-1");
    expect(screen.queryByLabelText("Client secret")).toBeNull(); // public client: device code
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
  });

  it("adds and indexes a knowledge folder", async () => {
    api.on("POST", "/api/v1/knowledge/collections", (body: unknown) => ({ id: "col_2",
      ...(body as object), connector_id: null, enabled: true, created_at: "2026-10-09T10:00:00Z", last_indexed_at: null }));
    api.on("POST", "/api/v1/knowledge/collections/company/index", { files_seen: 3, indexed: 3, unchanged: 0,
      removed: 0, chunks: 9, skipped: [] });
    const { services: s } = services();
    renderApp("settings", s);
    await userEvent.click(await screen.findByRole("button", { name: "Index" }));
    expect(await screen.findByText("✓ company: 3 indexed, 0 skipped")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "wiki");
    await userEvent.type(screen.getByLabelText("Folder path"), "C:/wiki");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "/api/v1/knowledge/collections" && c.method === "POST")?.body).toEqual({
        name: "wiki", kind: "folder", location: { path: "C:/wiki" }, privacy_mode: "LOCAL_ONLY" }),
    );
  });

  it("exports a project's documents", async () => {
    api.on("POST", "/api/v1/projects/orders/exports", { files: ["docs/requirements/exports/requirements.docx"] });
    const { services: s } = services();
    renderApp("projects", s);
    await userEvent.click(await screen.findByRole("button", { name: "Export documents" }));
    expect(await screen.findByText(/Exported: docs\/requirements\/exports\/requirements.docx/)).toBeInTheDocument();
  });
});

describe("[US-060] hub membership", () => {
  it("shows the link and joins with code and fingerprint", async () => {
    api.on("GET", "/api/v1/remote", { joined: false, hub_url: null, node_id: "nod_1", name: null, link: "disabled",
      last_error: null, certificate_expires: null, ca_fingerprint: null, accept_remote_approvals: true });
    api.on("POST", "/api/v1/remote/join", { joined: true, hub_url: "https://hubpc:48700", node_id: "nod_1",
      name: "laptop", link: "connecting", last_error: null, certificate_expires: null, ca_fingerprint: "x",
      accept_remote_approvals: true });
    const { services: s } = services();
    renderApp("settings", s);
    await userEvent.type(await screen.findByLabelText(/Hub address/), "https://hubpc:48700");
    await userEvent.type(screen.getByLabelText("One-time code"), "ABCD-EFGH-IJKL");
    await userEvent.type(screen.getByLabelText(/CA fingerprint/), "SHA256:" + "a".repeat(64));
    await userEvent.click(screen.getByRole("button", { name: "Join the hub" }));
    expect(await screen.findByText("Joined: https://hubpc:48700")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/api/v1/remote/join")?.body).toMatchObject({
      hub_url: "https://hubpc:48700", code: "ABCD-EFGH-IJKL" });
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
