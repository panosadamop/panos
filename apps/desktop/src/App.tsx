import { useEffect, useState, type ReactNode } from "react";

import { useI18n, type MessageKey } from "./i18n";
import { useLive, useServices, useStreamState } from "./state";
import { ApprovalsView } from "./views/Approvals";
import { AppsView } from "./views/Apps";
import { AuditView } from "./views/Audit";
import { ConversationView } from "./views/Conversation";
import { EffortView } from "./views/Effort";
import { JobsView } from "./views/Jobs";
import { ProjectsView } from "./views/Projects";
import { SettingsView } from "./views/Settings";
import { StatusView } from "./views/Status";
import { VoiceButton } from "./components/VoiceButton";

export type ViewId =
  | "conversation"
  | "jobs"
  | "approvals"
  | "projects"
  | "effort"
  | "apps"
  | "settings"
  | "audit"
  | "status";

const VIEWS: { id: ViewId; label: MessageKey; render: () => ReactNode }[] = [
  { id: "conversation", label: "nav.conversation", render: () => <ConversationView /> },
  { id: "jobs", label: "nav.jobs", render: () => <JobsView /> },
  { id: "approvals", label: "nav.approvals", render: () => <ApprovalsView /> },
  { id: "projects", label: "nav.projects", render: () => <ProjectsView /> },
  { id: "effort", label: "nav.effort", render: () => <EffortView /> },
  { id: "apps", label: "nav.apps", render: () => <AppsView /> },
  { id: "settings", label: "nav.settings", render: () => <SettingsView /> },
  { id: "audit", label: "nav.audit", render: () => <AuditView /> },
  { id: "status", label: "nav.status", render: () => <StatusView /> },
];

export function App({ initial = "conversation" }: { initial?: ViewId }) {
  const { t } = useI18n();
  const { api } = useServices();
  const [view, setView] = useState<ViewId>(initial);
  const stream = useStreamState();
  const pending = useLive(
    async () => (await api.GET("/api/v1/approvals")).data?.approvals.length ?? 0,
    ["notification", "job.status"],
  );

  // Alt+1…9 switch views (keyboard access, NFR-ACC-001).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey) return;
      const index = Number(e.key) - 1;
      if (index >= 0 && index < VIEWS.length) {
        e.preventDefault();
        setView(VIEWS[index].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const statusKey: MessageKey =
    stream === "open" ? "status.connected" : stream === "connecting" ? "status.connecting" : "status.offline";

  return (
    <div className="app">
      <header className="topbar">
        <span className={`reactor reactor-${stream}`} aria-hidden="true" />
        <h1>{t("appName")}</h1>
        <HudClock />
        <span className={`dot dot-${stream}`} role="status" aria-label={t(statusKey)}>
          {t(statusKey)}
        </span>
        <VoiceButton />
      </header>
      <nav aria-label="Panos">
        <ul>
          {VIEWS.map((v, i) => (
            <li key={v.id}>
              <button
                type="button"
                aria-current={view === v.id ? "page" : undefined}
                onClick={() => setView(v.id)}
                title={`Alt+${i + 1}`}
              >
                {t(v.label)}
                {v.id === "approvals" && (pending.data ?? 0) > 0 && (
                  <span className="badge" aria-label={`${pending.data}`}>
                    {pending.data}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main">{VIEWS.find((v) => v.id === view)?.render()}</main>
    </div>
  );
}

/** A HUD readout of the local time (decorative: the OS already shows the time). */
function HudClock() {
  const { lang } = useI18n();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="hud-clock" aria-hidden="true">
      {now.toLocaleTimeString(lang, { hour12: false })}
    </span>
  );
}
