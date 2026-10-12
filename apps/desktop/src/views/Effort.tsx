/** Estimated versus actual effort and estimate calibration (BR-014/015, ADR-031, M10-02/05). */
import { useState, type FormEvent } from "react";

import { unwrap, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

type Report = {
  logged_hours: number;
  pending: number;
  baselines: number;
  finished: { key: string; estimated_hours: number; actual_hours: number; ratio: number }[];
  machine: { jobs: number; runtime_s: number; tokens: number; cost_usd: number };
};

function detail(error: unknown): string {
  return String((error as { detail?: string })?.detail ?? "error");
}

export function EffortView() {
  const { t } = useI18n();
  const { api } = useServices();
  const projects = useLive(async () => unwrap(await api.GET("/api/v1/projects")).projects);
  const [project, setProject] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const chosen = project || projects.data?.[0]?.name || "";
  const actuals = useLive(async () =>
    chosen ? unwrap(await api.GET("/api/v1/projects/{ref}/effort", { params: { path: { ref: chosen }, query: {} } })) : [],
  );
  const report = useLive(async () =>
    chosen
      ? (unwrap(await api.GET("/api/v1/projects/{ref}/effort/report", { params: { path: { ref: chosen } } })) as Report)
      : null,
  );
  const calibrations = useLive(async () => unwrap(await api.GET("/api/v1/effort/calibrations", { params: { query: {} } })));
  const [key, setKey] = useState("");
  const [hours, setHours] = useState("");
  const [done, setDone] = useState(false);

  const reloadAll = () => {
    actuals.reload();
    report.reload();
    calibrations.reload();
  };
  const log = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.POST("/api/v1/projects/{ref}/effort", {
      params: { path: { ref: chosen } },
      body: { requirement_key: key, hours: Number(hours), role: "developer", work_date: null, note: "", completes: done },
    });
    setMessage(r.data ? `✓ ${r.data.requirement_key} ${r.data.hours} h` : detail(r.error));
    if (r.data) {
      setKey("");
      setHours("");
      setDone(false);
    }
    reloadAll();
  };
  const done_ = (r: { response: Response; error?: unknown }, ok: string) => {
    setMessage(r.response.ok ? ok : detail(r.error));
    reloadAll();
  };
  const baseline = async () =>
    done_(await api.POST("/api/v1/projects/{ref}/estimate/baseline", { params: { path: { ref: chosen } } }), t("effort.baselineSaved"));
  const importTracker = async () =>
    done_(await api.POST("/api/v1/projects/{ref}/effort/import", { params: { path: { ref: chosen } } }), t("effort.imported"));
  const suggest = async () => done_(await api.POST("/api/v1/effort/calibrations/suggest"), t("effort.suggested"));
  const decideActual = async (a: Schemas["ActualView"], decision: "approve" | "reject") => {
    await api.POST("/api/v1/effort/{actual_id}/decision", { params: { path: { actual_id: a.id } }, body: { decision } });
    reloadAll();
  };
  const decideCalibration = async (c: Schemas["CalibrationView"], decision: "approve" | "reject") => {
    await api.POST("/api/v1/effort/calibrations/{calibration_id}/decision", {
      params: { path: { calibration_id: c.id } },
      body: { decision },
    });
    reloadAll();
  };

  const pending = (actuals.data ?? []).filter((a) => a.status === "PENDING");
  const suggestions = (calibrations.data ?? []).filter((c) => c.status === "SUGGESTED");
  const active = (calibrations.data ?? []).filter((c) => c.status === "APPROVED");
  const r = report.data;
  return (
    <section aria-labelledby="effort-title">
      <h2 id="effort-title">{t("nav.effort")}</h2>
      {message && <p role="status">{message}</p>}
      <label htmlFor="effort-project">{t("nav.projects")}</label>
      <select id="effort-project" value={chosen} onChange={(e) => setProject(e.target.value)}>
        {projects.data?.map((p) => (
          <option key={p.id} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>

      <form onSubmit={log} aria-label={t("effort.log")}>
        <label htmlFor="effort-key">{t("effort.key")}</label>
        <input id="effort-key" value={key} placeholder="US-003" onChange={(e) => setKey(e.target.value.toUpperCase())} />
        <label htmlFor="effort-hours">{t("effort.hours")}</label>
        <input id="effort-hours" type="number" min="0.25" step="0.25" value={hours} onChange={(e) => setHours(e.target.value)} />
        <label>
          <input type="checkbox" checked={done} onChange={(e) => setDone(e.target.checked)} />
          {t("effort.done")}
        </label>
        <button type="submit" disabled={!chosen || !key || !(Number(hours) > 0)}>
          {t("effort.log")}
        </button>
      </form>
      <div className="actions">
        <button type="button" disabled={!chosen} onClick={() => void baseline()}>
          {t("effort.baseline")}
        </button>
        <button type="button" disabled={!chosen} onClick={() => void importTracker()}>
          {t("effort.import")}
        </button>
        <button type="button" onClick={() => void suggest()}>
          {t("effort.suggest")}
        </button>
      </div>

      {r && (
        <p>
          {r.logged_hours} h · {r.pending} {t("effort.pendingCount")} · {r.baselines} {t("effort.baselines")} ·{" "}
          {t("effort.machine")}: {r.machine.jobs} · ${r.machine.cost_usd.toFixed(4)}
        </p>
      )}
      {r && r.finished.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">{t("effort.key")}</th>
              <th scope="col">{t("effort.estimated")}</th>
              <th scope="col">{t("effort.actual")}</th>
              <th scope="col">×</th>
            </tr>
          </thead>
          <tbody>
            {r.finished.map((f) => (
              <tr key={f.key}>
                <td>{f.key}</td>
                <td>{f.estimated_hours} h</td>
                <td>{f.actual_hours} h</td>
                <td>{f.ratio.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {pending.length > 0 && (
        <>
          <h3>{t("effort.review")}</h3>
          <ul className="list">
            {pending.map((a) => (
              <li key={a.id}>
                {a.work_date} {a.requirement_key} {a.hours} h <span className="muted">{a.note}</span>
                <button type="button" onClick={() => void decideActual(a, "approve")}>
                  {t("approvals.approve")}
                </button>
                <button type="button" onClick={() => void decideActual(a, "reject")}>
                  {t("approvals.reject")}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>{t("effort.calibration")}</h3>
      {active.map((c) => (
        <p key={c.id}>
          ✓ {c.scope}: {c.hours_per_point} {t("effort.perPoint")} ({c.stories} {t("effort.stories")})
        </p>
      ))}
      {suggestions.length === 0 && active.length === 0 && <p className="muted">{t("effort.noCalibration")}</p>}
      <ul className="list">
        {suggestions.map((c) => (
          <li key={c.id}>
            {c.scope}: {c.base_hours_per_point} → <strong>{c.hours_per_point}</strong> {t("effort.perPoint")} · {c.stories}{" "}
            {t("effort.stories")} · bias {Math.round(c.bias * 100)}%
            <button type="button" onClick={() => void decideCalibration(c, "approve")}>
              {t("approvals.approve")}
            </button>
            <button type="button" onClick={() => void decideCalibration(c, "reject")}>
              {t("approvals.reject")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
