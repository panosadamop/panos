/** AI usage and cost per PC (live from each online PC, FR-027). */
import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Row = { provider: string; model: string; locality: string; calls: number; cost_usd: number };
type Usage = { total_cost_usd: number; rows: Row[] };

export function UsageView() {
  const { t } = useI18n();
  const api = useApi();
  const usage = usePoll(
    async () => {
      const nodes = unwrap(await api.GET("/api/v1/nodes")).filter((n) => n.status === "online");
      return Promise.all(
        nodes.map(async (n) => {
          const r = await api.GET("/api/v1/nodes/{ref}/{view}", {
            params: { path: { ref: n.id, view: "usage" }, query: { days: 30 } },
          });
          return { node: n.name, usage: (r.data ?? null) as Usage | null };
        }),
      );
    },
    [],
    30000,
  );
  return (
    <section aria-labelledby="usage-title">
      <h2 id="usage-title">
        {t("nav.usage")} · {t("usage.days")}
      </h2>
      {usage.error && <p role="alert">{usage.error}</p>}
      {usage.data?.length === 0 && <p>{t("common.none")}</p>}
      {usage.data?.map(({ node, usage: u }) => (
        <div key={node}>
          <h3>
            {node}: ${(u?.total_cost_usd ?? 0).toFixed(4)}
          </h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">{t("usage.calls")}</th>
                <th scope="col">{t("usage.cost")}</th>
              </tr>
            </thead>
            <tbody>
              {u?.rows.map((r) => (
                <tr key={`${r.provider}/${r.model}`}>
                  <td>
                    {r.provider}/{r.model} ({r.locality})
                  </td>
                  <td>{r.calls}</td>
                  <td>${r.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
