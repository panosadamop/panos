import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Summary = {
  nodes: { online: number; total: number };
  running: number;
  pending_approvals: number;
  jobs_last_day: number;
};

export function OverviewView() {
  const { t } = useI18n();
  const api = useApi();
  const summary = usePoll(async () => unwrap(await api.GET("/api/v1/summary")) as Summary);
  const s = summary.data;
  return (
    <section aria-labelledby="overview-title">
      <h2 id="overview-title">{t("nav.overview")}</h2>
      {summary.error && <p role="alert">{summary.error}</p>}
      {s && (
        <dl className="tiles">
          <div>
            <dt>{t("overview.online")}</dt>
            <dd>
              {s.nodes.online} / {s.nodes.total}
            </dd>
          </div>
          <div>
            <dt>{t("overview.running")}</dt>
            <dd>{s.running}</dd>
          </div>
          <div>
            <dt>{t("overview.pending")}</dt>
            <dd>{s.pending_approvals}</dd>
          </div>
          <div>
            <dt>{t("overview.day")}</dt>
            <dd>{s.jobs_last_day}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}
