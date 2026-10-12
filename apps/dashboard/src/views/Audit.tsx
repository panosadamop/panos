/** The hub's own hash-chained log (ADR-028 §8). */
import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Event = { seq: number; created_at: string; actor: string; event_type: string; node_id: string | null };

export function AuditView() {
  const { t } = useI18n();
  const api = useApi();
  const events = usePoll(
    async () => unwrap(await api.GET("/api/v1/audit", { params: { query: { limit: 200 } } })) as Event[],
  );
  const [check, setCheck] = useState<string | null>(null);
  const verify = async () => {
    const r = unwrap(await api.GET("/api/v1/audit/verify")) as { ok: boolean; first_broken_seq: number | null };
    setCheck(r.ok ? t("audit.ok") : `${t("audit.broken")} #${r.first_broken_seq}`);
  };
  return (
    <section aria-labelledby="audit-title">
      <h2 id="audit-title">{t("nav.audit")}</h2>
      <button type="button" onClick={() => void verify()}>
        {t("audit.verify")}
      </button>
      {check && <p role="status">{check}</p>}
      <table>
        <thead>
          <tr>
            <th scope="col">{t("common.when")}</th>
            <th scope="col">Actor</th>
            <th scope="col">Event</th>
            <th scope="col">{t("common.node")}</th>
          </tr>
        </thead>
        <tbody>
          {events.data?.map((e) => (
            <tr key={e.seq}>
              <td>{e.created_at.slice(0, 19).replace("T", " ")}</td>
              <td>{e.actor}</td>
              <td>{e.event_type}</td>
              <td>{e.node_id ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
