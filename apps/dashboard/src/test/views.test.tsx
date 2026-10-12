import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createApi, setCsrf } from "../api/client";
import { createPasskey } from "../api/webauthn";
import { App, type ViewId } from "../App";
import { I18nProvider, MESSAGES } from "../i18n";
import { ApiProvider } from "../state";

type Handler = (body: unknown, url: URL, headers: Headers) => unknown;
let routes: Map<string, Handler>;
let calls: { method: string; path: string; body: unknown; csrf: string | null }[];

function on(method: string, path: string, handler: Handler | unknown) {
  routes.set(`${method} ${path}`, typeof handler === "function" ? (handler as Handler) : () => handler);
}

const NODE = {
  id: "nod_a", name: "laptop", platform: "linux", version: "0.1.0", status: "online",
  enrolled_at: "2026-10-12T10:00:00Z", last_seen_at: "2026-10-12T10:05:00Z", revoked_at: null,
  running_jobs: 1, accepts_remote_approvals: true, tools: [], features: [],
  projects: [{ id: "prj_1", name: "shop", privacy_mode: "LOCAL_ONLY" }],
};

beforeEach(() => {
  routes = new Map();
  calls = [];
  setCsrf("");
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(request.url, "https://hub.local");
    const text = request.method === "GET" ? "" : await request.clone().text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method: request.method, path: url.pathname, body, csrf: request.headers.get("X-CSRF-Token") });
    const handler = routes.get(`${request.method} ${url.pathname}`);
    if (!handler) return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    const result = handler(body, url, request.headers);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result ?? {}), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  on("GET", "/api/v1/auth/me", { name: "panos", csrf: "csrf-1" });
  on("GET", "/api/v1/summary", { nodes: { online: 1, total: 2 }, running: 1, pending_approvals: 1, jobs_last_day: 3 });
  on("GET", "/api/v1/nodes", [NODE]);
  on("GET", "/api/v1/jobs", [{ job_id: "job_1", node_id: "nod_a", kind: "coding", objective: "Add checkout",
    status: "RUNNING", status_reason: null, phase: null, project_id: "prj_1", project_name: "shop",
    privacy_mode: "LOCAL_ONLY", dispatch_id: null, created_at: "2026-10-12T10:00:00Z", completed_at: null,
    updated_at: "2026-10-12T10:01:00Z", steps: [{ sequence: 1, status: "SUCCEEDED", summary: null }] }]);
  on("GET", "/api/v1/jobs/job_1", { live: { steps: [{ sequence: 1, status: "SUCCEEDED", agent: "agent:dev",
    summary: "edited checkout.py" }] }, live_error: null });
  on("GET", "/api/v1/approvals", [{ approval_id: "apr_1", node_id: "nod_a", job_id: "job_1", project_id: "prj_1",
    action_type: "git.commit", target: "C:/p/shop", payload_hash: "a".repeat(64), summary: null,
    status: "PENDING", privacy_mode: "LOCAL_ONLY", requested_at: "2026-10-12T10:00:00Z", expires_at: null }]);
  on("GET", "/api/v1/approvals/apr_1", { live: { summary: "Commit 1 file: checkout.py" }, live_error: null });
  on("GET", "/api/v1/audit", [{ seq: 1, created_at: "2026-10-12T10:00:00Z", actor: "hub", event_type: "hub.started",
    node_id: null, details: {} }]);
  on("GET", "/api/v1/nodes/nod_a/usage", { total_cost_usd: 0.5, rows: [] });
});

function renderApp(view: ViewId = "overview", lang: "el" | "en" = "en") {
  return render(
    <I18nProvider lang={lang}>
      <ApiProvider api={createApi("https://hub.local")}>
        <App initial={view} />
      </ApiProvider>
    </I18nProvider>,
  );
}

describe("[US-062] [AC-US062-01] dashboard", () => {
  it("asks for a passkey when not signed in", async () => {
    on("GET", "/api/v1/auth/me", new Response(JSON.stringify({ detail: "sign in" }), { status: 401 }));
    renderApp();
    expect(await screen.findByRole("button", { name: "Sign in with a passkey" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Setup code/)).toBeInTheDocument();
  });

  it("shows the overview and PCs", async () => {
    renderApp();
    expect(await screen.findByText("1 / 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "PCs" }));
    expect(await screen.findByText("laptop")).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
    expect(screen.getByText("shop")).toHaveClass("privacy-LOCAL_ONLY");
  });

  it("[AC-US061-02] shows the target before sending and sends with the CSRF token", async () => {
    on("POST", "/api/v1/dispatch", { id: "dsp_1", node_id: "nod_a", project: "shop", kind: "coding",
      objective: "Run tests", requested_by: "user:panos", created_at: "", state: "accepted", job_id: "job_9", reason: null });
    renderApp("dispatch");
    await screen.findByRole("option", { name: "laptop (linux)" });
    await userEvent.selectOptions(screen.getByLabelText("PC"), "nod_a");
    expect(screen.getByText(/It will run on/)).toHaveTextContent("laptop (linux)");
    await userEvent.selectOptions(screen.getByLabelText("Project"), "shop");
    await userEvent.type(screen.getByLabelText("What should it do?"), "Run tests");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Accepted as job job_9")).toBeInTheDocument();
    const sent = calls.find((c) => c.path === "/api/v1/dispatch");
    expect(sent?.body).toMatchObject({ node: "nod_a", project: "shop", kind: "coding", objective: "Run tests" });
    expect(sent?.csrf).toBe("csrf-1");
  });

  it("[AC-US061-03] shows a local-only approval live and approves it", async () => {
    on("POST", "/api/v1/approvals/apr_1/decision", { approval_id: "apr_1", status: "APPROVED" });
    renderApp("approvals");
    const card = (await screen.findByText("git.commit")).closest("li")!;
    await userEvent.click(within(card).getByRole("button", { name: "Show the exact action" }));
    expect(await within(card).findByText("Commit 1 file: checkout.py")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("git.commit: APPROVED")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({ decision: "approve", comment: "" });
  });

  it("shows live job steps and revokes a PC after confirmation", async () => {
    renderApp("jobs");
    await userEvent.click(await screen.findByRole("button", { name: "Add checkout" }));
    expect(await screen.findByText(/edited checkout.py/)).toBeInTheDocument();
    expect(screen.getByText(/local-only project: not stored on the hub/)).toBeInTheDocument();

    on("POST", "/api/v1/nodes/nod_a/revoke", { ...NODE, status: "revoked" });
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(screen.getByRole("button", { name: "PCs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(calls.some((c) => c.path.endsWith("/revoke"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/revoke"))).toBe(true));
  });
});

describe("[NFR-ACC-001] languages and [NFR-ACC-002] accessibility", () => {
  it("has the same keys in Greek and English", () => {
    expect(Object.keys(MESSAGES.el).sort()).toEqual(Object.keys(MESSAGES.en).sort());
  });

  const views: ViewId[] = ["overview", "nodes", "jobs", "approvals", "dispatch", "usage", "traceability", "audit"];
  it.each(views)("%s has no axe violations", async (view) => {
    const { container } = renderApp(view, "el");
    await screen.findByRole("heading", { level: 2 });
    await new Promise((r) => setTimeout(r, 30));
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});

describe("passkey encoding", () => {
  it("converts creation options and the attestation to base64url JSON", async () => {
    const create = vi.fn(async (opts: CredentialCreationOptions) => {
      const pk = opts.publicKey!;
      expect(new Uint8Array(pk.challenge as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]));
      return {
        id: "cred", rawId: new Uint8Array([9, 9]).buffer, type: "public-key",
        response: { clientDataJSON: new Uint8Array([123]).buffer, attestationObject: new Uint8Array([1]).buffer,
          getTransports: () => ["internal"] },
        getClientExtensionResults: () => ({}),
      };
    });
    Object.defineProperty(navigator, "credentials", { value: { create }, configurable: true });
    const out = await createPasskey(JSON.stringify({ challenge: "AQID", user: { id: "dQ", name: "u", displayName: "u" },
      rp: { id: "hub", name: "Panos hub" }, pubKeyCredParams: [] }));
    expect(out).toMatchObject({ id: "cred", rawId: "CQk", response: { clientDataJSON: "ew", attestationObject: "AQ" } });
  });
});
