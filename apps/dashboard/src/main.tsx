import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createApi } from "./api/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { ApiProvider } from "./state";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <ApiProvider api={createApi()}>
        <App />
      </ApiProvider>
    </I18nProvider>
  </StrictMode>,
);

// ADR-030: installable companion. The worker caches the app shell only, never API data.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
}
