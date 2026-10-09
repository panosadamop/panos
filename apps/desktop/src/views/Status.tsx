import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function StatusView() {
  const { t } = useI18n();
  const { api } = useServices();
  const health = useLive(async () => unwrap(await api.GET("/api/v1/health")));
  const voice = useLive(async () => unwrap(await api.GET("/api/v1/voice/status")), ["voice.state", "voice.error"]);
  const providers = useLive(async () => unwrap(await api.GET("/api/v1/ai/providers")));

  return (
    <section aria-labelledby="status-title">
      <h2 id="status-title">{t("nav.status")}</h2>
      {health.error && <p role="alert">{health.error}</p>}
      {health.data && (
        <dl>
          <dt>node</dt>
          <dd>
            {health.data.status} · {health.data.version} · {Math.round(health.data.uptime_s)} s
          </dd>
          <dt>database</dt>
          <dd>{health.data.database}</dd>
        </dl>
      )}
      <h3>AI</h3>
      <ul>
        {providers.data?.map((p) => (
          <li key={p.id}>
            {p.id}: {p.ok ? "ok" : p.detail}
          </li>
        ))}
      </ul>
      <h3>{t("settings.voice")}</h3>
      {voice.data && (
        <p>
          {voice.data.running ? voice.data.state : t("voice.off")}
          {voice.data.last_error && ` — ${voice.data.last_error}`}
        </p>
      )}
    </section>
  );
}
