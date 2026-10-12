/** This PC's hub membership (ADR-028/029, M9): link state, join, leave. */
import { useState, type FormEvent } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function Hub() {
  const { t } = useI18n();
  const { api } = useServices();
  const status = useLive(async () => unwrap(await api.GET("/api/v1/remote")));
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const join = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.POST("/api/v1/remote/join", { body: { hub_url: url, code, fingerprint, name: null } });
    setMessage(r.data ? `${t("hub.joined")}: ${r.data.hub_url}` : String((r.error as { detail?: string })?.detail));
    setCode("");
    status.reload();
  };
  const leave = async () => {
    if (!window.confirm(t("hub.leaveConfirm"))) return;
    await api.POST("/api/v1/remote/leave");
    status.reload();
  };

  const s = status.data;
  return (
    <section aria-labelledby="hub-title">
      <h3 id="hub-title">{t("hub.title")}</h3>
      {message && <p role="status">{message}</p>}
      {s?.joined ? (
        <p>
          <span className={`dot dot-${s.link === "connected" ? "open" : "closed"}`} aria-hidden="true" />{" "}
          {s.name} → {s.hub_url} · {t(`hub.link.${s.link as "connected"}`)}
          {s.last_error && s.link !== "connected" ? ` — ${s.last_error}` : ""}{" "}
          <button type="button" onClick={() => void leave()}>
            {t("hub.leave")}
          </button>
        </p>
      ) : (
        <form onSubmit={join} aria-label={t("hub.join")}>
          <p className="muted">{t("hub.help")}</p>
          <label htmlFor="hub-url">{t("hub.url")}</label>
          <input id="hub-url" value={url} onChange={(e) => setUrl(e.target.value)} />
          <label htmlFor="hub-code">{t("hub.code")}</label>
          <input id="hub-code" value={code} autoComplete="off" onChange={(e) => setCode(e.target.value)} />
          <label htmlFor="hub-fp">{t("hub.fingerprint")}</label>
          <input id="hub-fp" value={fingerprint} onChange={(e) => setFingerprint(e.target.value)} />
          <button type="submit" disabled={!url || code.length < 8 || fingerprint.length < 64}>
            {t("hub.join")}
          </button>
        </form>
      )}
    </section>
  );
}
