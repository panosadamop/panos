import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function AppsView() {
  const { t } = useI18n();
  const { api } = useServices();
  const [message, setMessage] = useState<string | null>(null);
  const apps = useLive(async () => unwrap(await api.GET("/api/v1/apps")).apps);

  const launch = async (key: string) => {
    const r = await api.POST("/api/v1/apps/{ref}/launch", {
      params: { path: { ref: key } },
      body: { target: "none" },
    });
    setMessage(r.response.ok ? `✓ ${key}` : String((r.error as { detail?: string })?.detail));
  };

  return (
    <section aria-labelledby="apps-title">
      <h2 id="apps-title">{t("nav.apps")}</h2>
      {message && <p role="status">{message}</p>}
      {apps.data?.length === 0 && <p>{t("apps.empty")}</p>}
      <ul className="cards">
        {apps.data?.map((a) => (
          <li key={a.key} className="card">
            <h3>{a.name}</h3>
            <p className="muted">
              {a.kind} {a.version ?? ""}
            </p>
            <button type="button" onClick={() => void launch(a.key)}>
              {t("apps.launch")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
