/** Pending approvals with the exact action (AC-US040-01): summary, target, payload hash. */
import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function ApprovalsView() {
  const { t } = useI18n();
  const { api } = useServices();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const approvals = useLive(async () => unwrap(await api.GET("/api/v1/approvals")).approvals, [
    "notification",
    "job.status",
  ]);

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusy(id);
    const r = await api.POST("/api/v1/approvals/{approval_id}/decision", {
      params: { path: { approval_id: id } },
      body: { decision, comment: "" },
    });
    setError(r.response.ok ? null : String((r.error as { detail?: string })?.detail));
    setBusy(null);
    approvals.reload();
  };

  return (
    <section aria-labelledby="appr-title">
      <h2 id="appr-title">{t("nav.approvals")}</h2>
      {error && <p role="alert">{error}</p>}
      {approvals.data?.length === 0 && <p>{t("approvals.empty")}</p>}
      <ul className="cards">
        {approvals.data?.map((a) => (
          <li key={a.id} className="card">
            <h3>
              <code>{a.action_type}</code>
            </h3>
            <pre className="summary">{a.summary}</pre>
            <details>
              <summary>{t("approvals.details")}</summary>
              <dl>
                <dt>target</dt>
                <dd>
                  <code>{a.target}</code>
                </dd>
                <dt>payload sha-256</dt>
                <dd>
                  <code>{a.payload_hash}</code>
                </dd>
                {a.job_id && (
                  <>
                    <dt>job</dt>
                    <dd>
                      <code>{a.job_id}</code>
                    </dd>
                  </>
                )}
                <dt>{t("approvals.expires")}</dt>
                <dd>{new Date(a.expires_at).toLocaleString()}</dd>
              </dl>
            </details>
            <div className="actions">
              <button type="button" disabled={busy === a.id} onClick={() => void decide(a.id, "approve")}>
                {t("approvals.approve")}
              </button>
              <button type="button" disabled={busy === a.id} onClick={() => void decide(a.id, "reject")}>
                {t("approvals.reject")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
