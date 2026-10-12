import { useState } from "react";

import { unwrap } from "../api/client";
import { useI18n } from "../i18n";
import { useLive, useServices } from "../state";

export function ProjectsView() {
  const { t } = useI18n();
  const { api } = useServices();
  const [message, setMessage] = useState<string | null>(null);
  const projects = useLive(async () => unwrap(await api.GET("/api/v1/projects")).projects);

  const open = async (ref: string) => {
    const r = await api.POST("/api/v1/projects/{ref}/open", {
      params: { path: { ref } },
      body: { dry_run: false },
    });
    setMessage(r.response.ok ? `✓ ${ref}` : String((r.error as { detail?: string })?.detail));
  };
  const index = async (ref: string) => {
    const r = await api.POST("/api/v1/projects/{ref}/index", { params: { path: { ref }, query: {} } });
    setMessage(r.data ? `✓ ${r.data.chunks} chunks` : String((r.error as { detail?: string })?.detail));
  };

  const exportDocs = async (ref: string) => {
    const r = await api.POST("/api/v1/projects/{ref}/exports", { params: { path: { ref } }, body: {} });
    setMessage(
      r.data ? `✓ ${t("projects.exported")}: ${(r.data.files as string[]).join(", ")}` : String((r.error as { detail?: string })?.detail),
    );
  };

  return (
    <section aria-labelledby="proj-title">
      <h2 id="proj-title">{t("nav.projects")}</h2>
      {message && <p role="status">{message}</p>}
      {projects.data?.length === 0 && <p>{t("projects.empty")}</p>}
      <table>
        <thead>
          <tr>
            <th scope="col">{t("nav.projects")}</th>
            <th scope="col">{t("projects.privacy")}</th>
            <th scope="col">
              <span className="sr-only">actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {projects.data?.map((p) => (
            <tr key={p.id}>
              <td>
                <strong>{p.name}</strong>
                <div className="muted">{p.path}</div>
              </td>
              <td>
                <span className={`privacy privacy-${p.privacy_mode}`}>{p.privacy_mode}</span>
              </td>
              <td className="actions">
                <button type="button" onClick={() => void open(p.name)}>
                  {t("projects.open")}
                </button>
                <button type="button" onClick={() => void index(p.name)}>
                  {t("projects.index")}
                </button>
                <button type="button" onClick={() => void exportDocs(p.name)}>
                  {t("projects.export")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
