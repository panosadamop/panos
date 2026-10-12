/** Authorized PCs with platform, status, last-seen time and projects (US-060, AC-US060-01). */
import { useState } from "react";

import { errorText, unwrap, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

export function NodesView() {
  const { t } = useI18n();
  const api = useApi();
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")));
  const [message, setMessage] = useState<string | null>(null);
  const [join, setJoin] = useState<Schemas["CodeOut"] | null>(null);

  const revoke = async (n: Schemas["NodeView"]) => {
    if (!window.confirm(`${n.name}: ${t("nodes.revokeConfirm")}`)) return;
    const r = await api.POST("/api/v1/nodes/{ref}/revoke", { params: { path: { ref: n.id } } });
    setMessage(r.data ? `${n.name}: ${r.data.status}` : errorText(r.error));
    nodes.reload();
  };
  const addPc = async () => {
    const r = await api.POST("/api/v1/codes", { body: { purpose: "enroll", label: "" } });
    if (r.data) setJoin(r.data);
    else setMessage(errorText(r.error));
  };

  return (
    <section aria-labelledby="nodes-title">
      <h2 id="nodes-title">{t("nav.nodes")}</h2>
      {message && <p role="status">{message}</p>}
      {nodes.error && <p role="alert">{nodes.error}</p>}
      <button type="button" onClick={() => void addPc()}>
        {t("nodes.addPc")}
      </button>
      {join && (
        <div role="status" className="code">
          <p>{t("nodes.addHelp")}</p>
          <code>
            panos node join {join.hub_url} --code {join.code} --fingerprint {join.ca_fingerprint}
          </code>
        </div>
      )}
      {nodes.data?.length === 0 && <p>{t("common.none")}</p>}
      <table>
        <thead>
          <tr>
            <th scope="col">{t("common.node")}</th>
            <th scope="col">{t("nodes.platform")}</th>
            <th scope="col">{t("common.status")}</th>
            <th scope="col">{t("nodes.lastSeen")}</th>
            <th scope="col">{t("nodes.projects")}</th>
            <th scope="col">
              <span className="sr-only">actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {nodes.data?.map((n) => (
            <tr key={n.id}>
              <td>
                <strong>{n.name}</strong>
                <div className="muted">{n.id}</div>
              </td>
              <td>
                {n.platform} {n.version}
              </td>
              <td>
                <span className={`state state-${n.status}`}>{n.status}</span>
                {n.status === "online" && n.running_jobs > 0 && ` · ${n.running_jobs}`}
              </td>
              <td>{n.last_seen_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
              <td>
                {n.projects.map((p) => (
                  <span key={String(p.id)} className={`privacy privacy-${String(p.privacy_mode)}`}>
                    {String(p.name)}
                  </span>
                ))}
              </td>
              <td>
                {n.status !== "revoked" && (
                  <button type="button" onClick={() => void revoke(n)}>
                    {t("nodes.revoke")}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
