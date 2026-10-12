/** Shared approvals: decided here, checked and executed by the PC that asked (ADR-028 §6). */
import { useState } from "react";

import { errorText, unwrap, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

export function ApprovalsView() {
  const { t } = useI18n();
  const api = useApi();
  const approvals = usePoll(async () => unwrap(await api.GET("/api/v1/approvals")));
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")));
  const [message, setMessage] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, string>>({});
  const names = new Map((nodes.data ?? []).map((n) => [n.id, n.name]));

  const show = async (a: Schemas["ApprovalView"]) => {
    const r = await api.GET("/api/v1/approvals/{approval_id}", {
      params: { path: { approval_id: a.approval_id } },
    });
    const live = (r.data as { live?: { summary?: string } | null } | undefined)?.live;
    setDetails({ ...details, [a.approval_id]: live?.summary ?? a.summary ?? t("jobs.offline") });
  };
  const decide = async (a: Schemas["ApprovalView"], decision: "approve" | "reject") => {
    const r = await api.POST("/api/v1/approvals/{approval_id}/decision", {
      params: { path: { approval_id: a.approval_id } },
      body: { decision, comment: "" },
    });
    setMessage(r.data ? `${a.action_type}: ${String((r.data as { status?: string }).status)}` : errorText(r.error));
    approvals.reload();
  };

  return (
    <section aria-labelledby="approvals-title">
      <h2 id="approvals-title">{t("nav.approvals")}</h2>
      {message && <p role="status">{message}</p>}
      {approvals.error && <p role="alert">{approvals.error}</p>}
      {approvals.data?.length === 0 && <p>{t("common.none")}</p>}
      <ul className="list">
        {approvals.data?.map((a) => (
          <li key={a.approval_id} className="card">
            <h3>{a.action_type}</h3>
            <dl>
              <dt>{t("common.node")}</dt>
              <dd>{names.get(a.node_id) ?? a.node_id}</dd>
              <dt>{t("approvals.target")}</dt>
              <dd>{a.target}</dd>
              <dt>{t("approvals.hash")}</dt>
              <dd>
                <code>{a.payload_hash}</code>
              </dd>
            </dl>
            {a.summary && <pre>{a.summary}</pre>}
            {details[a.approval_id] && <pre>{details[a.approval_id]}</pre>}
            <div className="actions">
              {!a.summary && !details[a.approval_id] && (
                <button type="button" onClick={() => void show(a)}>
                  {t("approvals.details")}
                </button>
              )}
              <button type="button" className="primary" onClick={() => void decide(a, "approve")}>
                {t("approvals.approve")}
              </button>
              <button type="button" onClick={() => void decide(a, "reject")}>
                {t("approvals.reject")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
