/** Hub dashboard shell: session check, navigation, views (BR-025, FR-027, US-062). */
import { useEffect, useState, type JSX } from "react";

import { setCsrf } from "./api/client";
import { useI18n, type Lang, type MessageKey } from "./i18n";
import { SignIn } from "./SignIn";
import { useApi, usePoll } from "./state";
import { ApprovalsView } from "./views/Approvals";
import { AuditView } from "./views/Audit";
import { DispatchView } from "./views/Dispatch";
import { EffortView } from "./views/Effort";
import { JobsView } from "./views/Jobs";
import { NodesView } from "./views/Nodes";
import { OverviewView } from "./views/Overview";
import { TalkView } from "./views/Talk";
import { TraceabilityView } from "./views/Traceability";
import { UsageView } from "./views/Usage";

export type ViewId =
  | "talk"
  | "overview"
  | "nodes"
  | "jobs"
  | "approvals"
  | "dispatch"
  | "usage"
  | "effort"
  | "traceability"
  | "audit";

const VIEWS: { id: ViewId; label: MessageKey; render: () => JSX.Element }[] = [
  { id: "talk", label: "nav.talk", render: () => <TalkView /> },
  { id: "overview", label: "nav.overview", render: () => <OverviewView /> },
  { id: "nodes", label: "nav.nodes", render: () => <NodesView /> },
  { id: "jobs", label: "nav.jobs", render: () => <JobsView /> },
  { id: "approvals", label: "nav.approvals", render: () => <ApprovalsView /> },
  { id: "dispatch", label: "nav.dispatch", render: () => <DispatchView /> },
  { id: "usage", label: "nav.usage", render: () => <UsageView /> },
  { id: "effort", label: "nav.effort", render: () => <EffortView /> },
  { id: "traceability", label: "nav.traceability", render: () => <TraceabilityView /> },
  { id: "audit", label: "nav.audit", render: () => <AuditView /> },
];

/** Installed as an app (manifest start_url) the companion opens on the conversation. */
function startView(): ViewId {
  try {
    return new URLSearchParams(window.location.search).get("app") === "companion" ? "talk" : "overview";
  } catch {
    return "overview";
  }
}

export function App({ initial }: { initial?: ViewId }) {
  const { t, lang, setLang } = useI18n();
  const api = useApi();
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [view, setView] = useState<ViewId>(initial ?? startView());
  const pending = usePoll(
    async () => {
      if (!user) return 0;
      const r = await api.GET("/api/v1/summary");
      return (r.data as { pending_approvals?: number } | undefined)?.pending_approvals ?? 0;
    },
    [user],
    15000,
  );
  const count = pending.data ?? 0;
  useEffect(() => {
    // Badge on the installed app icon where supported (no push service involved).
    const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void> };
    void nav.setAppBadge?.(count).catch(() => undefined);
    document.title = count ? `(${count}) ${t("appName")}` : t("appName");
  }, [count, t]);

  useEffect(() => {
    api.GET("/api/v1/auth/me").then(
      (r) => {
        if (r.data) {
          setCsrf(r.data.csrf);
          setUser(r.data.name);
        } else setUser(null);
      },
      () => setUser(null),
    );
  }, [api]);

  if (user === undefined) return <p role="status">{t("common.loading")}</p>;
  if (user === null) return <SignIn onSignedIn={setUser} />;

  const signOut = async () => {
    await api.POST("/api/v1/auth/logout");
    setCsrf("");
    setUser(null);
  };
  const current = VIEWS.find((v) => v.id === view) ?? VIEWS[0];
  return (
    <div className="shell">
      <header>
        <h1>
          <span className="reactor" aria-hidden="true" />
          {t("appName")}
        </h1>
        <nav aria-label="main">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              aria-current={v.id === view ? "page" : undefined}
              onClick={() => setView(v.id)}
            >
              {t(v.label)}
              {v.id === "approvals" && count > 0 && <span className="badge"> {count}</span>}
            </button>
          ))}
        </nav>
        <div className="actions">
          <label>
            <span className="sr-only">language</span>
            <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
              <option value="el">Ελληνικά</option>
              <option value="en">English</option>
            </select>
          </label>
          <span>{user}</span>
          <button type="button" onClick={() => void signOut()}>
            {t("auth.signOut")}
          </button>
        </div>
      </header>
      <main>{current.render()}</main>
    </div>
  );
}
