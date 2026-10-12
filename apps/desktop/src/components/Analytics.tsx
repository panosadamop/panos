/** Model/cost analytics of this PC, last 30 days (M10-06). */
import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

type Row = { name: string; calls: number; cost_usd: number };
type Analytics = {
  calls: number;
  cost_usd: number;
  local_share: number | null;
  latency_ms: { p50: number; p95: number };
  by_model: Row[];
  by_project: Row[];
};

export function Analytics() {
  const { t } = useI18n();
  const { api } = useServices();
  const data = useLive(
    async () => unwrap(await api.GET("/api/v1/ai/analytics", { params: { query: { days: 30 } } })) as Analytics,
  );
  const a = data.data;
  if (!a) return null;
  const top = Math.max(0.000001, ...a.by_model.map((m) => m.cost_usd));
  return (
    <section aria-labelledby="analytics-title">
      <h3 id="analytics-title">{t("analytics.title")}</h3>
      <p>
        {a.calls} {t("analytics.calls")} · ${a.cost_usd.toFixed(4)} · {t("analytics.local")}{" "}
        {a.local_share == null ? "—" : `${Math.round(a.local_share * 100)}%`} · p50 {a.latency_ms.p50} ms · p95{" "}
        {a.latency_ms.p95} ms
      </p>
      <ul className="list">
        {a.by_model.slice(0, 5).map((m) => (
          <li key={m.name}>
            {m.name}: {m.calls} {t("analytics.calls")}, ${m.cost_usd.toFixed(4)}{" "}
            <meter min={0} max={top} value={m.cost_usd} aria-label={m.name} />
          </li>
        ))}
      </ul>
    </section>
  );
}
