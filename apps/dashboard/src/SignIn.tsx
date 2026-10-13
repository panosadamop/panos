/** Passkey sign-in, and first-time passkey setup with a one-time code from the hub PC. */
import { useState, type FormEvent } from "react";

import { errorText, setCsrf } from "./api/client";
import { createPasskey, getPasskey, supported } from "./api/webauthn";
import { useI18n } from "./i18n";
import { useApi } from "./state";

export function SignIn({ onSignedIn }: { onSignedIn: (name: string) => void }) {
  const { t } = useI18n();
  const api = useApi();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const finish = (data: { name: string; csrf: string } | undefined, error: unknown) => {
    if (data) {
      setCsrf(data.csrf);
      onSignedIn(data.name);
    } else setMessage(errorText(error));
  };

  const signIn = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const opts = await api.POST("/api/v1/auth/login/options");
      if (!opts.data) return setMessage(errorText(opts.error));
      const { ceremony, options } = opts.data as { ceremony: string; options: string };
      const credential = await getPasskey(options);
      const r = await api.POST("/api/v1/auth/login/verify", { body: { ceremony, credential } });
      finish(r.data, r.error);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const setup = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const opts = await api.POST("/api/v1/auth/register/options", {
        body: { code, name: name || "owner", label: navigator.platform || "browser" },
      });
      if (!opts.data) return setMessage(errorText(opts.error));
      const { ceremony, options } = opts.data as { ceremony: string; options: string };
      const credential = await createPasskey(options);
      const r = await api.POST("/api/v1/auth/register/verify", { body: { ceremony, credential } });
      finish(r.data, r.error);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="signin" aria-labelledby="signin-title">
      <span className="reactor" aria-hidden="true" />
      <h1 id="signin-title">{t("auth.title")}</h1>
      {!supported() && <p role="alert">{t("auth.unsupported")}</p>}
      {message && <p role="alert">{message}</p>}
      <button type="button" className="primary" disabled={busy} onClick={() => void signIn()}>
        {t("auth.signIn")}
      </button>
      <form onSubmit={setup} aria-label={t("auth.setup")}>
        <h2>{t("auth.setup")}</h2>
        <label htmlFor="setup-code">{t("auth.code")}</label>
        <input id="setup-code" value={code} autoComplete="off" onChange={(e) => setCode(e.target.value)} />
        <label htmlFor="setup-name">{t("auth.name")}</label>
        <input id="setup-name" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit" disabled={busy || code.length < 8}>
          {t("auth.setup")}
        </button>
      </form>
    </main>
  );
}
