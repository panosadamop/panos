/** Jobs across PCs; the detail is fetched live from the PC that runs it (FR-027). */
import { useState } from "react";

import { errorText, unwrap, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Step = { sequence: number; agent?: string | null; status: string; summary?: string | null };
type Detail = { live: { steps?: Step[] } | null; live_error: string | null };
type Action = "pause" | "resume" | "cancel";

export function JobsView() {
  const { t } = useI18n();
  const api = useApi();
  const jobs = usePoll(async () => unwrap(await api.GET("/api/v1/jobs", { params: { query: { limit: 100 } } })));
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")));
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const names = new Map((nodes.data ?? []).map((n) => [n.id, n.name]));

  const control = async (job: Schemas["JobView"], action: Action) => {
    const r = await api.POST("/api/v1/jobs/{job_id}/{action}", { params: { path: { job_id: job.job_id, action } } });
    setMessage(r.data ? `${job.job_id}: ${String((r.data as { status?: string }).status)}` : errorText(r.error));
    jobs.reload();
  };

  return (
    <section aria-labelledby="jobs-title">
      <h2 id="jobs-title">{t("nav.jobs")}</h2>
      {message && <p role="status">{message}</p>}
      {jobs.error && <p role="alert">{jobs.error}</p>}
      {jobs.data?.length === 0 && <p>{t("common.none")}</p>}
      <ul className="list">
        {jobs.data?.map((j) => (
          <li key={j.job_id}>
            <button
              type="button"
              aria-expanded={open === j.job_id}
              onClick={() => setOpen(open === j.job_id ? null : j.job_id)}
            >
              {j.objective}
            </button>{" "}
            <span className={`state state-${j.status.toLowerCase()}`}>{j.status}</span>{" "}
            <span className="muted">
              {names.get(j.node_id) ?? j.node_id}
              {j.project_name ? ` · ${j.project_name}` : ""} · {j.created_at.slice(0, 16).replace("T", " ")}
            </span>
            {open === j.job_id && <JobDetail job={j} onControl={(a) => void control(j, a)} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

function JobDetail({ job, onControl }: { job: Schemas["JobView"]; onControl: (a: Action) => void }) {
  const { t } = useI18n();
  const api = useApi();
  const detail = usePoll(
    async () =>
      unwrap(await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: job.job_id } } })) as Detail,
    [job.job_id],
  );
  const d = detail.data;
  const steps: Step[] = d?.live?.steps ?? (job.steps as Step[]);
  return (
    <div className="detail">
      {d?.live_error && <p className="muted">{t("jobs.offline")}</p>}
      {d?.live && job.privacy_mode === "LOCAL_ONLY" && <p className="muted">{t("jobs.liveOnly")}</p>}
      <h3>{t("jobs.steps")}</h3>
      <ol>
        {steps.map((s) => (
          <li key={s.sequence}>
            <span className={`state state-${s.status.toLowerCase()}`}>{s.status}</span> {s.agent ?? ""}{" "}
            {s.summary ?? ""}
          </li>
        ))}
      </ol>
      <div className="actions">
        <button type="button" onClick={() => onControl("pause")}>
          {t("jobs.pause")}
        </button>
        <button type="button" onClick={() => onControl("resume")}>
          {t("jobs.resume")}
        </button>
        <button type="button" onClick={() => onControl("cancel")}>
          {t("jobs.cancel")}
        </button>
      </div>
    </div>
  );
}
