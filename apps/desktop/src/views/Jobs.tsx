/** Jobs list + step timeline with pause/resume/cancel/rollback (US-050/052, FR-024). */
import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

const ACTIVE = new Set(["QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_TOOL"]);
const RESUMABLE = new Set(["PAUSED", "BLOCKED", "WAITING_FOR_USER", "WAITING_FOR_APPROVAL"]);

export function JobsView() {
  const { t } = useI18n();
  const { api } = useServices();
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const jobs = useLive(async () => unwrap(await api.GET("/api/v1/jobs")).jobs, ["job.status"]);
  const detail = useLive(
    async () =>
      selected
        ? unwrap(await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: selected } } }))
        : null,
    ["job.status"],
  );
  // Reload the detail when the selection changes.
  const select = (id: string) => {
    setSelected(id);
    setMessage(null);
    setTimeout(detail.reload, 0);
  };

  const control = async (action: "pause" | "resume" | "cancel") => {
    if (!selected) return;
    const path = { params: { path: { job_id: selected } } };
    const result =
      action === "pause"
        ? await api.POST("/api/v1/jobs/{job_id}/pause", path)
        : action === "resume"
          ? await api.POST("/api/v1/jobs/{job_id}/resume", path)
          : await api.POST("/api/v1/jobs/{job_id}/cancel", path);
    setMessage(result.response.ok ? null : String((result.error as { detail?: string })?.detail));
    jobs.reload();
    detail.reload();
  };

  const rollback = async () => {
    if (!selected || !window.confirm(t("jobs.rollbackConfirm"))) return;
    const r = await api.POST("/api/v1/jobs/{job_id}/rollback", {
      params: { path: { job_id: selected } },
      body: {},
    });
    setMessage(
      r.data
        ? `✓ ${r.data.restored.length} restored, ${r.data.deleted.length} removed`
        : String((r.error as { detail?: string })?.detail),
    );
  };

  const job = detail.data;
  const result = (job?.result ?? null) as Record<string, unknown> | null;

  return (
    <section aria-labelledby="jobs-title" className="split">
      <div>
        <h2 id="jobs-title">{t("nav.jobs")}</h2>
        {jobs.error && <p role="alert">{jobs.error}</p>}
        {jobs.data?.length === 0 && <p>{t("jobs.empty")}</p>}
        <ul className="list">
          {jobs.data?.map((j) => (
            <li key={j.id}>
              <button type="button" aria-current={selected === j.id || undefined} onClick={() => select(j.id)}>
                <span className={`status status-${j.status}`}>{j.status}</span> {j.objective}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {job && (
        <article aria-label={job.objective} className="detail">
          <h3>{job.objective}</h3>
          <p>
            <span className={`status status-${job.status}`}>{job.status}</span>
            {job.status_reason && ` — ${job.status_reason}`}
          </p>
          <div className="actions">
            {ACTIVE.has(job.status) && (
              <button type="button" onClick={() => void control("pause")}>
                {t("jobs.pause")}
              </button>
            )}
            {RESUMABLE.has(job.status) && (
              <button type="button" onClick={() => void control("resume")}>
                {t("jobs.resume")}
              </button>
            )}
            {!["COMPLETED", "FAILED", "CANCELLED"].includes(job.status) && (
              <button type="button" onClick={() => void control("cancel")}>
                {t("jobs.cancel")}
              </button>
            )}
            {job.checkpoints.length > 0 && !job.running && (
              <button type="button" onClick={() => void rollback()}>
                {t("jobs.rollback")}
              </button>
            )}
          </div>
          {message && <p role="status">{message}</p>}
          <h4>{t("jobs.steps")}</h4>
          <ol className="timeline">
            {job.steps.map((s) => (
              <li key={s.sequence} className={`step step-${s.status}`}>
                <span className="status">{s.status}</span> <strong>{s.agent}</strong> {s.input_summary}
                {s.output_summary && <div className="out">→ {s.output_summary}</div>}
              </li>
            ))}
          </ol>
          {result && (
            <>
              <h4>{t("jobs.result")}</h4>
              {"summary" in result && <p>{String(result.summary)}</p>}
              {Array.isArray(result.changed_files) && (
                <>
                  <h5>{t("jobs.changed")}</h5>
                  <ul>
                    {(result.changed_files as { status: string; path: string }[]).map((c) => (
                      <li key={c.path}>
                        <code>{c.status}</code> {c.path}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {Array.isArray(result.unresolved) && result.unresolved.length > 0 && (
                <>
                  <h5>{t("jobs.unresolved")}</h5>
                  <ul>
                    {(result.unresolved as string[]).map((u) => (
                      <li key={u}>{u}</li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </article>
      )}
    </section>
  );
}
