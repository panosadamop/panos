/** Model and cost analytics across your PCs (M10-06). Bars are <meter> elements (strict CSP). */
import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Row = { name: string; calls: number; cost_usd: number; input_tokens: number; output_tokens: number };
type NodeAnalytics = {
  node_id: string;
  name: string;
  calls: number;
  cost_usd: number;
  local_share: number | null;
  latency_ms: { p50: number; p95: number };
  budget: { monthly_usd: number | null; month_cost_usd: number; used_share: number | null };
  by_project: Row[];
};
type Analytics = {
  days: number;
  combined: { calls: number; cost_usd: number; local_share: number | null; by_model: Row[] };
  nodes: NodeAnalytics[];
};

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export function UsageView() {
  const { t } = useI18n();
  const api = useApi();
  const [days, setDays] = useState(30);
  const data = usePoll(
    async () => unwrap(await api.GET("/api/v1/analytics", { params: { query: { days } } })) as Analytics,
    [days],
    30000,
  );
  const a = data.data;
  const top = Math.max(0.000001, ...(a?.combined.by_model.map((m) => m.cost_usd) ?? [0]));
  return (
    <section aria-labelledby="usage-title">
      <h2 id="usage-title">{t("nav.usage")}</h2>
      <label htmlFor="usage-days">{t("usage.window")}</label>
      <select id="usage-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[7, 30, 90].map((d) => (
          <option key={d} value={d}>
            {d} {t("usage.daysUnit")}
          </option>
        ))}
      </select>
      {data.error && <p role="alert">{data.error}</p>}
      {a && (
        <>
          <dl className="tiles">
            <div>
              <dt>{t("usage.cost")}</dt>
              <dd>${a.combined.cost_usd.toFixed(2)}</dd>
            </div>
            <div>
              <dt>{t("usage.calls")}</dt>
              <dd>{a.combined.calls}</dd>
            </div>
            <div>
              <dt>{t("usage.local")}</dt>
              <dd>{pct(a.combined.local_share)}</dd>
            </div>
          </dl>
          <h3>{t("usage.models")}</h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">{t("usage.calls")}</th>
                <th scope="col">{t("usage.cost")}</th>
              </tr>
            </thead>
            <tbody>
              {a.combined.by_model.map((m) => (
                <tr key={m.name}>
                  <td>{m.name}</td>
                  <td>{m.calls}</td>
                  <td>
                    <meter min={0} max={top} value={m.cost_usd} aria-label={`${m.name} ${t("usage.cost")}`} /> $
                    {m.cost_usd.toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>{t("nav.nodes")}</h3>
          <ul className="list">
            {a.nodes.map((n) => (
              <li key={n.node_id} className="card">
                <strong>{n.name}</strong>: ${n.cost_usd.toFixed(4)} · {n.calls} {t("usage.calls")} · {t("usage.local")}{" "}
                {pct(n.local_share)} · p95 {n.latency_ms.p95} ms
                {n.budget.monthly_usd != null && (
                  <div>
                    <label htmlFor={`budget-${n.node_id}`}>{t("usage.budget")}</label>{" "}
                    <meter id={`budget-${n.node_id}`} min={0} max={1} high={0.8} value={n.budget.used_share ?? 0} />{" "}
                    {pct(n.budget.used_share)} (${n.budget.month_cost_usd.toFixed(2)} / ${n.budget.monthly_usd.toFixed(2)})
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
