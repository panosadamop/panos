/** Requirement traceability of a project on a PC (live, FR-027). */
import { useState, type FormEvent } from "react";

import { errorText, unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useApi, usePoll } from "../state";

type TraceNode = { key: string; title: string; status?: string; children?: TraceNode[] };

export function TraceabilityView() {
  const { t } = useI18n();
  const api = useApi();
  const nodes = usePoll(async () => unwrap(await api.GET("/api/v1/nodes")), [], 0);
  const [pick, setPick] = useState("");
  const [tree, setTree] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const options = (nodes.data ?? [])
    .filter((n) => n.status === "online")
    .flatMap((n) =>
      n.projects.map((p) => ({ value: `${n.id}|${String(p.name)}`, label: `${n.name} · ${String(p.name)}` })),
    );

  const load = async (e: FormEvent) => {
    e.preventDefault();
    const [ref, project] = pick.split("|");
    const r = await api.GET("/api/v1/nodes/{ref}/{view}", {
      params: { path: { ref, view: "traceability" }, query: { project } },
    });
    if (r.data) setTree(r.data);
    else setMessage(errorText(r.error));
  };

  return (
    <section aria-labelledby="trace-title">
      <h2 id="trace-title">{t("nav.traceability")}</h2>
      {message && <p role="alert">{message}</p>}
      <form onSubmit={load}>
        <label htmlFor="trace-pick">{t("common.project")}</label>
        <select id="trace-pick" value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">—</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="submit" disabled={!pick}>
          {t("trace.load")}
        </button>
      </form>
      {tree !== null && <Tree value={tree} />}
    </section>
  );
}

function Tree({ value }: { value: unknown }) {
  const v = value as { roots?: TraceNode[]; items?: TraceNode[] } | TraceNode[];
  const items = Array.isArray(v) ? v : (v.roots ?? v.items ?? []);
  if (!items.length) return <pre>{JSON.stringify(value, null, 2)}</pre>;
  return (
    <ul className="tree">
      {items.map((n) => (
        <li key={n.key}>
          <strong>{n.key}</strong> {n.title} {n.status && <span className="muted">({n.status})</span>}
          {n.children?.length ? <Tree value={n.children} /> : null}
        </li>
      ))}
    </ul>
  );
}
