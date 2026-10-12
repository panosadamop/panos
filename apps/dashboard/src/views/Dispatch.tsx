/** Send a job to a PC; the target is shown before sending (AC-US061-02). */
import { useState, type FormEvent } from "react";

import { errorText, unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Kind = "coding" | "command" | "research";

export function DispatchView() {
  const { t } = useI18n();
  const api = useApi();
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")), [], 0);
  const online = (nodes.data ?? []).filter((n) => n.status === "online");
  const [node, setNode] = useState("");
  const [project, setProject] = useState("");
  const [kind, setKind] = useState<Kind>("coding");
  const [objective, setObjective] = useState("");
  const [command, setCommand] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const target = online.find((n) => n.id === node);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.POST("/api/v1/dispatch", {
      body: {
        node,
        objective,
        project: project || null,
        kind,
        argv: kind === "command" ? command.split(/\s+/).filter(Boolean) : null,
        urls: null,
      },
    });
    if (!r.data) {
      setMessage(errorText(r.error));
      return;
    }
    setMessage(
      r.data.state === "accepted"
        ? `${t("dispatch.accepted")} ${r.data.job_id}`
        : `${t("dispatch.refused")}: ${r.data.reason ?? ""}`,
    );
  };

  return (
    <section aria-labelledby="dispatch-title">
      <h2 id="dispatch-title">{t("nav.dispatch")}</h2>
      {message && <p role="status">{message}</p>}
      <form onSubmit={send}>
        <label htmlFor="d-node">{t("common.node")}</label>
        <select
          id="d-node"
          value={node}
          onChange={(e) => {
            setNode(e.target.value);
            setProject("");
          }}
        >
          <option value="">—</option>
          {online.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name} ({n.platform})
            </option>
          ))}
        </select>
        <label htmlFor="d-kind">{t("dispatch.kind")}</label>
        <select id="d-kind" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {(["coding", "command", "research"] as Kind[]).map((k) => (
            <option key={k} value={k}>
              {t(`dispatch.kind.${k}`)}
            </option>
          ))}
        </select>
        <label htmlFor="d-project">{t("common.project")}</label>
        <select id="d-project" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">—</option>
          {(target?.projects ?? []).map((p) => (
            <option key={String(p.id)} value={String(p.name)}>
              {String(p.name)}
            </option>
          ))}
        </select>
        <label htmlFor="d-objective">{t("dispatch.objective")}</label>
        <textarea id="d-objective" value={objective} onChange={(e) => setObjective(e.target.value)} />
        {kind === "command" && (
          <>
            <label htmlFor="d-command">{t("dispatch.command")}</label>
            <input id="d-command" value={command} onChange={(e) => setCommand(e.target.value)} />
          </>
        )}
        {target && (
          <p className="target">
            {t("dispatch.target")} <strong>{target.name}</strong> ({target.platform}). {t("dispatch.policy")}
          </p>
        )}
        <button type="submit" className="primary" disabled={!target || objective.trim().length < 3}>
          {t("dispatch.send")}
        </button>
      </form>
    </section>
  );
}
