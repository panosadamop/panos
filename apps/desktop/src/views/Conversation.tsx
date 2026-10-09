import { useCallback, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { streamMessage, type Schemas } from "../api/client";
import { useI18n } from "../i18n";
import { useEvent, useServices } from "../state";

interface Turn {
  id: string;
  role: "user" | "assistant" | "heard";
  text: string;
  meta?: Schemas["ReplyView"];
  route?: string;
  error?: boolean;
}

let counter = 0;
const nextId = () => `t${++counter}`;

export function ConversationView() {
  const { t } = useI18n();
  const { conn } = useServices();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  // Voice: show what was heard (AC-US003-03) and the spoken reply.
  useEvent(
    "voice.transcript",
    useCallback((e) => {
      setTurns((all) => [...all, { id: nextId(), role: "heard", text: e.payload.text }]);
    }, []),
  );
  useEvent(
    "voice.reply",
    useCallback((e) => {
      setTurns((all) => [...all, { id: nextId(), role: "assistant", text: e.payload.text }]);
    }, []),
  );

  const send = async (message: string) => {
    if (!message.trim() || busy) return;
    setText("");
    setBusy(true);
    const reply: Turn = { id: nextId(), role: "assistant", text: "" };
    setTurns((all) => [...all, { id: nextId(), role: "user", text: message }, reply]);
    const update = (patch: Partial<Turn>) =>
      setTurns((all) => all.map((turn) => (turn.id === reply.id ? { ...turn, ...patch } : turn)));
    abort.current = new AbortController();
    let streamed = "";
    try {
      for await (const event of streamMessage(
        conn,
        { text: message, conversation_id: conversationId.current },
        abort.current.signal,
      )) {
        if (event.type === "start") conversationId.current = event.conversation_id;
        else if (event.type === "route") update({ route: `${event.model} · ${event.locality}` });
        else if (event.type === "text") update({ text: (streamed += event.text) });
        else if (event.type === "reply") update({ text: event.reply.text, meta: event.reply });
        else if (event.type === "error") update({ text: event.error.detail, error: true });
      }
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        update({ text: e instanceof Error ? e.message : String(e), error: true });
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(text);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(text);
    }
  };

  return (
    <section aria-labelledby="conv-title" className="conversation">
      <h2 id="conv-title">{t("nav.conversation")}</h2>
      <ol className="turns" aria-live="polite">
        {turns.length === 0 && <li className="hint">{t("conv.empty")}</li>}
        {turns.map((turn) => (
          <li key={turn.id} className={`turn turn-${turn.role}${turn.error ? " turn-error" : ""}`}>
            <span className="who">
              {turn.role === "user" ? t("conv.you") : turn.role === "heard" ? t("conv.heard") : t("conv.panos")}
            </span>
            <p>{turn.text || (busy ? "…" : "")}</p>
            {(turn.meta || turn.route) && (
              <small className="meta">
                {turn.meta?.model ?? turn.route}
                {turn.meta?.locality && ` · ${turn.meta.locality === "cloud" ? t("conv.cloud") : t("conv.local")}`}
                {turn.meta?.privacy && ` · ${turn.meta.privacy}`}
                {turn.meta?.sources?.length ? ` · ${t("conv.sources")}: ${turn.meta.sources.join(", ")}` : ""}
              </small>
            )}
          </li>
        ))}
      </ol>
      <form onSubmit={onSubmit} className="composer">
        <label htmlFor="composer" className="sr-only">
          {t("conv.placeholder")}
        </label>
        <textarea
          id="composer"
          value={text}
          placeholder={t("conv.placeholder")}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>
            {t("conv.stop")}
          </button>
        ) : (
          <button type="submit" disabled={!text.trim()}>
            {t("conv.send")}
          </button>
        )}
      </form>
    </section>
  );
}
