import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function AuditView() {
  const { t } = useI18n();
  const { api } = useServices();
  const [filter, setFilter] = useState("");
  const [verified, setVerified] = useState<boolean | null>(null);
  const events = useLive(
    async () =>
      unwrap(await api.GET("/api/v1/audit", { params: { query: { limit: 200, event_type: filter || undefined } } }))
        .events,
  );

  const verify = async () => setVerified(unwrap(await api.GET("/api/v1/audit/verify")).ok);

  return (
    <section aria-labelledby="audit-title">
      <h2 id="audit-title">{t("nav.audit")}</h2>
      <div className="actions">
        <label htmlFor="audit-filter">event_type</label>
        <input id="audit-filter" value={filter} onChange={(e) => setFilter(e.target.value)} onBlur={events.reload} />
        <button type="button" onClick={events.reload}>
          {t("common.refresh")}
        </button>
        <button type="button" onClick={() => void verify()}>
          {t("audit.verify")}
        </button>
        {verified !== null && <span role="status">{verified ? t("audit.ok") : t("audit.broken")}</span>}
      </div>
      <table>
        <thead>
          <tr>
            <th scope="col">time</th>
            <th scope="col">event</th>
            <th scope="col">action</th>
            <th scope="col">actor</th>
          </tr>
        </thead>
        <tbody>
          {events.data?.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString()}</td>
              <td>
                <code>{e.event_type}</code>
              </td>
              <td>{e.action}</td>
              <td>{e.actor}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
