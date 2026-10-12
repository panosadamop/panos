/** Connected accounts (ADR-026, M8-07): Google, Microsoft, Jira, Azure DevOps. Secrets go to the node once. */
import { useEffect, useState, type FormEvent } from "react";

import { unwrap, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

type Flow = Schemas["FlowView"];
type Kind = "google" | "microsoft" | "jira" | "azure-devops";
const FEATURES = ["mail", "calendar", "drive"] as const;

function detail(error: unknown): string {
  return String((error as { detail?: string })?.detail ?? "error");
}

export function Connections() {
  const { t } = useI18n();
  const { api } = useServices();
  const accounts = useLive(async () => unwrap(await api.GET("/api/v1/connectors")));
  const [adding, setAdding] = useState<Kind | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);

  // Poll a running sign-in flow until it is connected or failed.
  useEffect(() => {
    if (!flow || flow.status !== "pending") return;
    const timer = setInterval(async () => {
      const r = await api.GET("/api/v1/connectors/flows/{flow_id}", { params: { path: { flow_id: flow.id } } });
      if (r.data && r.data.status !== "pending") {
        setFlow(r.data);
        setMessage(r.data.status === "connected" ? t("conn.connected") : `${t("conn.failed")}: ${r.data.error}`);
        accounts.reload();
      }
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow?.id, flow?.status]);

  const setPrivacy = async (id: string, privacy_mode: "LOCAL_ONLY" | "STANDARD") => {
    const r = await api.PATCH("/api/v1/connectors/{ref}", { params: { path: { ref: id } }, body: { privacy_mode } });
    if (!r.response.ok) setMessage(detail(r.error));
    accounts.reload();
  };
  const remove = async (id: string, label: string) => {
    if (!window.confirm(`${t("conn.removeConfirm")} ${label}?`)) return;
    const r = await api.DELETE("/api/v1/connectors/{ref}", { params: { path: { ref: id } } });
    if (!r.response.ok) setMessage(detail(r.error));
    accounts.reload();
  };

  return (
    <section aria-labelledby="conn-title">
      <h3 id="conn-title">{t("conn.title")}</h3>
      {message && <p role="status">{message}</p>}
      {accounts.data?.length === 0 && <p className="muted">{t("conn.empty")}</p>}
      <ul className="list">
        {accounts.data?.map((a) => (
          <li key={a.id}>
            <span className={`dot dot-${a.status === "CONNECTED" ? "open" : "closed"}`} aria-hidden="true" />{" "}
            <strong>{a.label}</strong> ({a.kind}) {a.status !== "CONNECTED" && `— ${a.status}`}
            <label>
              <input
                type="checkbox"
                checked={a.privacy_mode === "STANDARD"}
                onChange={(e) => void setPrivacy(a.id, e.target.checked ? "STANDARD" : "LOCAL_ONLY")}
              />
              {t("conn.allowCloud")}
            </label>
            <button type="button" onClick={() => void remove(a.id, a.label)}>
              {t("conn.remove")}
            </button>
          </li>
        ))}
      </ul>
      <div className="actions" role="group" aria-label={t("conn.add")}>
        {(["google", "microsoft", "jira", "azure-devops"] as Kind[]).map((k) => (
          <button key={k} type="button" aria-pressed={adding === k} onClick={() => setAdding(adding === k ? null : k)}>
            {t(`conn.kind.${k}`)}
          </button>
        ))}
      </div>
      {flow?.status === "pending" && (
        <p role="status">
          {flow.user_code ? (
            <>
              {t("conn.deviceCode")} <a href={flow.verification_uri ?? "#"}>{flow.verification_uri}</a>:{" "}
              <strong>{flow.user_code}</strong>
            </>
          ) : (
            <>
              {t("conn.signInBrowser")} <a href={flow.auth_url ?? "#"}>{t("conn.openLink")}</a>
            </>
          )}
        </p>
      )}
      {adding === "google" || adding === "microsoft" ? (
        <OAuthForm
          kind={adding}
          onFlow={(f) => {
            setFlow(f);
            setAdding(null);
          }}
          onError={setMessage}
        />
      ) : null}
      {adding === "jira" || adding === "azure-devops" ? (
        <TokenForm
          kind={adding}
          onDone={(text) => {
            setMessage(text);
            setAdding(null);
            accounts.reload();
          }}
        />
      ) : null}
    </section>
  );
}

function OAuthForm({
  kind,
  onFlow,
  onError,
}: {
  kind: "google" | "microsoft";
  onFlow: (f: Flow) => void;
  onError: (m: string) => void;
}) {
  const { t } = useI18n();
  const { api } = useServices();
  const client = useLive(async () => unwrap(await api.GET("/api/v1/connectors/clients/{kind}", { params: { path: { kind } } })));
  const [features, setFeatures] = useState<string[]>(["mail", "calendar"]);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");

  const register = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.PUT("/api/v1/connectors/clients/{kind}", {
      params: { path: { kind } },
      body: { client_id: clientId, client_secret: kind === "google" ? secret : null },
    });
    setSecret("");
    if (!r.response.ok) onError(detail(r.error));
    client.reload();
  };
  const connect = async (e: FormEvent) => {
    e.preventDefault();
    const r =
      kind === "google"
        ? await api.POST("/api/v1/connectors/google", {
            body: { features: features as ("mail" | "calendar" | "drive")[], open_browser: true },
          })
        : await api.POST("/api/v1/connectors/microsoft");
    if (r.data) onFlow(r.data);
    else onError(detail(r.error));
  };

  if (client.data && !client.data.registered) {
    return (
      <form onSubmit={register} aria-label={t("conn.registerClient")}>
        <p className="muted">{t("conn.clientHelp")}</p>
        <label htmlFor="client-id">{t("conn.clientId")}</label>
        <input id="client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        {kind === "google" && (
          <>
            <label htmlFor="client-secret">{t("conn.clientSecret")}</label>
            <input
              id="client-secret"
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </>
        )}
        <button type="submit" disabled={clientId.length < 4 || (kind === "google" && !secret)}>
          {t("conn.save")}
        </button>
      </form>
    );
  }
  return (
    <form onSubmit={connect} aria-label={t(`conn.kind.${kind}`)}>
      {kind === "google" && (
        <fieldset>
          <legend>{t("conn.features")}</legend>
          {FEATURES.map((f) => (
            <label key={f}>
              <input
                type="checkbox"
                checked={features.includes(f)}
                onChange={(e) => setFeatures(e.target.checked ? [...features, f] : features.filter((x) => x !== f))}
              />
              {t(`conn.feature.${f}`)}
            </label>
          ))}
        </fieldset>
      )}
      <button type="submit" disabled={kind === "google" && features.length === 0}>
        {t("conn.signIn")}
      </button>
    </form>
  );
}

function TokenForm({ kind, onDone }: { kind: "jira" | "azure-devops"; onDone: (m: string) => void }) {
  const { t } = useI18n();
  const { api } = useServices();
  const [site, setSite] = useState("");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const r =
      kind === "jira"
        ? await api.POST("/api/v1/connectors/jira", { body: { site_url: site, email, api_token: token } })
        : await api.POST("/api/v1/connectors/azure-devops", { body: { org_url: site, pat: token } });
    setToken("");
    onDone(r.data ? `${t("conn.connected")}: ${r.data.label}` : detail(r.error));
  };

  return (
    <form onSubmit={submit} aria-label={t(`conn.kind.${kind}`)}>
      <label htmlFor="tracker-site">{kind === "jira" ? t("conn.jiraSite") : t("conn.adoOrg")}</label>
      <input id="tracker-site" value={site} onChange={(e) => setSite(e.target.value)} />
      {kind === "jira" && (
        <>
          <label htmlFor="tracker-email">{t("conn.email")}</label>
          <input id="tracker-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </>
      )}
      <label htmlFor="tracker-token">{kind === "jira" ? t("conn.apiToken") : t("conn.pat")}</label>
      <input id="tracker-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
      <button type="submit" disabled={site.length < 3 || token.length < 8 || (kind === "jira" && !email)}>
        {t("conn.connect")}
      </button>
    </form>
  );
}
