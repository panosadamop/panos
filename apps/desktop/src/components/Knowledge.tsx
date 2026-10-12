/** Knowledge collections outside projects (FR-019/020, M8-07): folders, websites, cloud drives. */
import { useState, type FormEvent } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

type AddKind = "folder" | "website" | "google_drive" | "onedrive";

function detail(error: unknown): string {
  return String((error as { detail?: string })?.detail ?? "error");
}

export function Knowledge() {
  const { t } = useI18n();
  const { api } = useServices();
  const collections = useLive(async () => unwrap(await api.GET("/api/v1/knowledge/collections")));
  const [message, setMessage] = useState<string | null>(null);
  const [kind, setKind] = useState<AddKind>("folder");
  const [name, setName] = useState("");
  const [where, setWhere] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const location = (): Record<string, unknown> => {
    if (kind === "folder") return { path: where.trim() };
    if (kind === "website") return { urls: where.split(/\s+/).filter(Boolean) };
    if (kind === "google_drive") return { folder_id: where.trim() };
    return { drive_id: where.trim() };
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.POST("/api/v1/knowledge/collections", {
      body: { name, kind, location: location(), privacy_mode: "LOCAL_ONLY" },
    });
    setMessage(r.data ? `✓ ${r.data.name}` : detail(r.error));
    if (r.data) {
      setName("");
      setWhere("");
    }
    collections.reload();
  };
  const index = async (ref: string) => {
    setBusy(ref);
    const r = await api.POST("/api/v1/knowledge/collections/{ref}/index", { params: { path: { ref } } });
    setBusy(null);
    setMessage(
      r.data
        ? `✓ ${ref}: ${r.data.indexed} ${t("knowledge.indexed")}, ${r.data.skipped.length} ${t("knowledge.skipped")}`
        : detail(r.error),
    );
    collections.reload();
  };
  const remove = async (ref: string) => {
    if (!window.confirm(`${t("knowledge.removeConfirm")} ${ref}?`)) return;
    await api.DELETE("/api/v1/knowledge/collections/{ref}", { params: { path: { ref } } });
    collections.reload();
  };

  return (
    <section aria-labelledby="knowledge-title">
      <h3 id="knowledge-title">{t("knowledge.title")}</h3>
      {message && <p role="status">{message}</p>}
      {collections.data?.length === 0 && <p className="muted">{t("knowledge.empty")}</p>}
      <ul className="list">
        {collections.data?.map((c) => (
          <li key={c.id}>
            <strong>{c.name}</strong> ({t(`knowledge.kind.${c.kind as AddKind}`)}){" "}
            <span className={`privacy privacy-${c.privacy_mode}`}>{c.privacy_mode}</span>{" "}
            <span className="muted">{c.last_indexed_at ? c.last_indexed_at.slice(0, 16) : t("knowledge.never")}</span>
            <button type="button" disabled={busy === c.name} onClick={() => void index(c.name)}>
              {busy === c.name ? t("common.loading") : t("projects.index")}
            </button>
            <button type="button" onClick={() => void remove(c.name)}>
              {t("conn.remove")}
            </button>
          </li>
        ))}
      </ul>
      <form onSubmit={add} aria-label={t("knowledge.add")}>
        <label htmlFor="kc-kind">{t("knowledge.source")}</label>
        <select id="kc-kind" value={kind} onChange={(e) => setKind(e.target.value as AddKind)}>
          {(["folder", "website", "google_drive", "onedrive"] as AddKind[]).map((k) => (
            <option key={k} value={k}>
              {t(`knowledge.kind.${k}`)}
            </option>
          ))}
        </select>
        <label htmlFor="kc-name">{t("knowledge.name")}</label>
        <input id="kc-name" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="kc-where">{t(`knowledge.where.${kind}`)}</label>
        <input id="kc-where" value={where} onChange={(e) => setWhere(e.target.value)} />
        <button type="submit" disabled={!name || !where.trim()}>
          {t("knowledge.add")}
        </button>
      </form>
    </section>
  );
}
