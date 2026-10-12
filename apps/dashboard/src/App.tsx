/** Hub dashboard shell: session check, navigation, views (BR-025, FR-027, US-062). */
import { useEffect, useState, type JSX } from "react";

import { setCsrf } from "./api/client";
import { useI18n, type Lang, type MessageKey } from "./i18n";
import { SignIn } from "./SignIn";
import { useApi } from "./state";
import { ApprovalsView } from "./views/Approvals";
import { AuditView } from "./views/Audit";
import { DispatchView } from "./views/Dispatch";
import { JobsView } from "./views/Jobs";
import { NodesView } from "./views/Nodes";
import { OverviewView } from "./views/Overview";
import { TraceabilityView } from "./views/Traceability";
import { UsageView } from "./views/Usage";

export type ViewId = "overview" | "nodes" | "jobs" | "approvals" | "dispatch" | "usage" | "traceability" | "audit";

const VIEWS: { id: ViewId; label: MessageKey; render: () => JSX.Element }[] = [
  { id: "overview", label: "nav.overview", render: () => <OverviewView /> },
  { id: "nodes", label: "nav.nodes", render: () => <NodesView /> },
  { id: "jobs", label: "nav.jobs", render: () => <JobsView /> },
  { id: "approvals", label: "nav.approvals", render: () => <ApprovalsView /> },
  { id: "dispatch", label: "nav.dispatch", render: () => <DispatchView /> },
  { id: "usage", label: "nav.usage", render: () => <UsageView /> },
  { id: "traceability", label: "nav.traceability", render: () => <TraceabilityView /> },
  { id: "audit", label: "nav.audit", render: () => <AuditView /> },
];

export function App({ initial = "overview" }: { initial?: ViewId }) {
  const { t, lang, setLang } = useI18n();
  const api = useApi();
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [view, setView] = useState<ViewId>(initial);

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
        <h1>{t("appName")}</h1>
        <nav aria-label="main">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              aria-current={v.id === view ? "page" : undefined}
              onClick={() => setView(v.id)}
            >
              {t(v.label)}
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
