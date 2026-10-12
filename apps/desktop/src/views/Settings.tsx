/** Language, AI providers/keys, usage vs budget, connected accounts, knowledge sources, voice
 * switches, start at login (desktop only). */
import { useEffect, useState, type FormEvent } from "react";

import { unwrap } from "../api/client";
import { Connections } from "../components/Connections";
import { Hub } from "../components/Hub";
import { Knowledge } from "../components/Knowledge";
import { getAutostart, setAutostart } from "../desktop";
import { useI18n, type Lang } from "../i18n";
import { useLive, useServices } from "../state";

export function SettingsView() {
  const { t, lang, setLang } = useI18n();
  const { api } = useServices();
  const providers = useLive(async () => unwrap(await api.GET("/api/v1/ai/providers")));
  const usage = useLive(async () => unwrap(await api.GET("/api/v1/ai/usage")));
  const voice = useLive(async () => unwrap(await api.GET("/api/v1/voice/status")), ["voice.ready"]);
  const [keyFor, setKeyFor] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [autostart, setAutostartState] = useState<boolean | null>(null);

  useEffect(() => {
    getAutostart().then(setAutostartState, () => setAutostartState(null));
  }, []);

  const toggleAutostart = async (enabled: boolean) => {
    try {
      await setAutostart(enabled);
      setAutostartState(enabled);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  const saveKey = async (e: FormEvent) => {
    e.preventDefault();
    if (!keyFor) return;
    const r = await api.PUT("/api/v1/ai/providers/{provider_id}/key", {
      params: { path: { provider_id: keyFor } },
      body: { key },
    });
    setKey("");
    setKeyFor(null);
    setMessage(r.response.ok ? t("settings.keySaved") : String((r.error as { detail?: string })?.detail));
    providers.reload();
  };

  const setVoice = async (change: Record<string, boolean>) => {
    await api.PATCH("/api/v1/voice/settings", { body: change });
    voice.reload();
  };

  return (
    <section aria-labelledby="settings-title">
      <h2 id="settings-title">{t("nav.settings")}</h2>
      {message && <p role="status">{message}</p>}

      <fieldset>
        <legend>{t("settings.language")}</legend>
        {(["el", "en"] as Lang[]).map((l) => (
          <label key={l}>
            <input type="radio" name="lang" checked={lang === l} onChange={() => setLang(l)} />
            {l === "el" ? "Ελληνικά" : "English"}
          </label>
        ))}
      </fieldset>

      <h3>{t("settings.providers")}</h3>
      <ul className="list">
        {providers.data?.map((p) => (
          <li key={p.id}>
            <span className={`dot dot-${p.ok ? "open" : "closed"}`} aria-hidden="true" /> <strong>{p.id}</strong>{" "}
            ({p.kind}, {p.locality}) {p.ok ? "" : `— ${p.detail}`}
            {p.locality === "cloud" && (
              <button type="button" onClick={() => setKeyFor(p.id)}>
                {t("settings.setKey")}
              </button>
            )}
          </li>
        ))}
      </ul>
      {keyFor && (
        <form onSubmit={saveKey}>
          <label htmlFor="api-key">
            {keyFor} API key
          </label>
          <input id="api-key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
          <button type="submit" disabled={key.length < 8}>
            {t("settings.setKey")}
          </button>
        </form>
      )}

      {usage.data && (
        <p>
          {t("settings.usage")}: <strong>${usage.data.total_cost_usd.toFixed(4)}</strong>
          {usage.data.monthly_budget_usd != null &&
            ` / ${t("settings.budget")}: $${usage.data.monthly_budget_usd.toFixed(2)}`}
        </p>
      )}

      <Connections />
      <Hub />
      <Knowledge />

      <fieldset>
        <legend>{t("settings.voice")}</legend>
        <label>
          <input
            type="checkbox"
            checked={!!voice.data?.wake_word_enabled}
            onChange={(e) => void setVoice({ wake_word: e.target.checked })}
          />
          {t("settings.wakeWord")}
        </label>
        <label>
          <input type="checkbox" checked={!!voice.data?.tts} onChange={(e) => void setVoice({ tts: e.target.checked })} />
          {t("settings.tts")}
        </label>
        <label>
          <input
            type="checkbox"
            checked={!!voice.data?.voice_notifications}
            onChange={(e) => void setVoice({ voice_notifications: e.target.checked })}
          />
          {t("settings.voiceNotifications")}
        </label>
        {voice.data?.missing_models?.length ? <p className="muted">panos voice setup</p> : null}
      </fieldset>

      {autostart !== null && (
        <label>
          <input type="checkbox" checked={autostart} onChange={(e) => void toggleAutostart(e.target.checked)} />
          {t("settings.autostart")}
        </label>
      )}
    </section>
  );
}
