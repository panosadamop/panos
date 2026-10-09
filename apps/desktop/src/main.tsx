import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { getConnection } from "./api/connection";
import { setupDesktopIntegration } from "./desktop";
import { I18nProvider } from "./i18n";
import { makeServices, ServicesProvider, type Services } from "./state";
import "./styles.css";

function Root() {
  const [services, setServices] = useState<Services | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getConnection()
      .then((conn) => {
        const s = makeServices(conn);
        s.events.start();
        void setupDesktopIntegration(s);
        setServices(s);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  if (error) return <p role="alert" className="fatal">{error}</p>;
  if (!services) return <p className="fatal">…</p>;
  return (
    <ServicesProvider services={services}>
      <App />
    </ServicesProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <Root />
    </I18nProvider>
  </StrictMode>,
);
