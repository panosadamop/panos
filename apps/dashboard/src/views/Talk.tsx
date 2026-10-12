/** Conversation with one of your PCs (ADR-030 §3): that PC answers under its own policy. */
import { useState, type FormEvent } from "react";

import { errorText, unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type Turn = { who: "you" | "panos"; text: string; meta?: string };
type Reply = {
  conversation_id: string;
  text: string;
  privacy: string;
  intent: string;
  provider: string | null;
  model: string | null;
  locality: string | null;
};

export function TalkView() {
  const { t } = useI18n();
  const api = useApi();
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")), [], 0);
  const online = (nodes.data ?? []).filter((n) => n.status === "online");
  const [node, setNode] = useState("");
  const [project, setProject] = useState("");
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const target = online.find((n) => n.id === node) ?? (online.length === 1 ? online[0] : undefined);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!target || !text.trim()) return;
    const message = text.trim();
    setTurns((all) => [...all, { who: "you", text: message }]);
    setText("");
    setBusy(true);
    const r = await api.POST("/api/v1/converse", {
      body: { node: target.id, text: message, project: project || null, conversation_id: conversation },
    });
    setBusy(false);
    if (!r.data) {
      setTurns((all) => [...all, { who: "panos", text: errorText(r.error), meta: t("talk.failed") }]);
      return;
    }
    const reply = r.data as Reply;
    setConversation(reply.conversation_id);
    const via = reply.model ? `${reply.model} · ${reply.locality}` : t("talk.noModel");
    setTurns((all) => [...all, { who: "panos", text: reply.text, meta: `${target.name} · ${via} · ${reply.privacy}` }]);
  };

  return (
    <section aria-labelledby="talk-title" className="talk">
      <h2 id="talk-title">{t("nav.talk")}</h2>
      <div className="pickers">
        <label htmlFor="talk-node">{t("common.node")}</label>
        <select
          id="talk-node"
          value={target?.id ?? ""}
          onChange={(e) => {
            setNode(e.target.value);
            setProject("");
            setConversation(null);
          }}
        >
          <option value="">—</option>
          {online.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
        <label htmlFor="talk-project">{t("common.project")}</label>
        <select id="talk-project" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">—</option>
          {(target?.projects ?? []).map((p) => (
            <option key={String(p.id)} value={String(p.name)}>
              {String(p.name)}
            </option>
          ))}
        </select>
      </div>
      <ol className="turns" aria-live="polite">
        {turns.map((turn, i) => (
          <li key={i} className={`turn turn-${turn.who}`}>
            <strong>{turn.who === "you" ? t("talk.you") : "Panos"}</strong>
            <p>{turn.text}</p>
            {turn.meta && <small className="muted">{turn.meta}</small>}
          </li>
        ))}
        {busy && (
          <li role="status" className="muted">
            {t("talk.thinking")}
          </li>
        )}
      </ol>
      <form onSubmit={send} className="composer">
        <label htmlFor="talk-text" className="sr-only">
          {t("talk.placeholder")}
        </label>
        <input
          id="talk-text"
          value={text}
          placeholder={t("talk.placeholder")}
          autoComplete="off"
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" className="primary" disabled={!target || busy || !text.trim()}>
          {t("talk.send")}
        </button>
      </form>
    </section>
  );
}
