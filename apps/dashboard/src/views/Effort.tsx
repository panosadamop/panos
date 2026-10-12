/** Estimated versus actual effort of a project on a PC (M10-02/05, read-only here). */
import { useState, type FormEvent } from "react";

import { errorText, unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Finished = { key: string; estimated_hours: number; actual_hours: number; ratio: number };
type Report = {
  project: string;
  logged_hours: number;
  pending: number;
  baselines: number;
  finished: Finished[];
  machine: { jobs: number; runtime_s: number; tokens: number; cost_usd: number };
};

export function EffortView() {
  const { t } = useI18n();
  const api = useApi();
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")), [], 0);
  const [pick, setPick] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const options = (nodes.data ?? [])
    .filter((n) => n.status === "online")
    .flatMap((n) =>
      n.projects.map((p) => ({ value: `${n.id}|${String(p.name)}`, label: `${n.name} · ${String(p.name)}` })),
    );

  const load = async (e: FormEvent) => {
    e.preventDefault();
    const [ref, project] = pick.split("|");
    const r = await api.GET("/api/v1/nodes/{ref}/{view}", {
      params: { path: { ref, view: "effort" }, query: { project } },
    });
    if (r.data) setReport(r.data as Report);
    else setMessage(errorText(r.error));
  };

  return (
    <section aria-labelledby="effort-title">
      <h2 id="effort-title">{t("nav.effort")}</h2>
      {message && <p role="alert">{message}</p>}
      <form onSubmit={load}>
        <label htmlFor="effort-pick">{t("common.project")}</label>
        <select id="effort-pick" value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">—</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="submit" disabled={!pick}>
          {t("trace.load")}
        </button>
      </form>
      {report && (
        <>
          <p>
            {report.logged_hours} h {t("effort.logged")} · {report.pending} {t("effort.pending")} · {report.baselines}{" "}
            {t("effort.baselines")}
          </p>
          <table>
            <thead>
              <tr>
                <th scope="col">{t("effort.story")}</th>
                <th scope="col">{t("effort.estimated")}</th>
                <th scope="col">{t("effort.actual")}</th>
                <th scope="col">×</th>
              </tr>
            </thead>
            <tbody>
              {report.finished.map((f) => (
                <tr key={f.key}>
                  <td>{f.key}</td>
                  <td>{f.estimated_hours} h</td>
                  <td>{f.actual_hours} h</td>
                  <td>{f.ratio.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            {t("effort.machine")}: {report.machine.jobs} · {Math.round(report.machine.runtime_s)} s ·{" "}
            {report.machine.tokens} tokens · ${report.machine.cost_usd.toFixed(4)}
          </p>
        </>
      )}
    </section>
  );
}
