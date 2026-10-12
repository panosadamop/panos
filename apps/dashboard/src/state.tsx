/** API context and a polling hook (the dashboard refreshes every few seconds). */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { Api } from "./api/client";

const Ctx = createContext<Api | null>(null);

export function ApiProvider({ api, children }: { api: Api; children: ReactNode }) {
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}

export function useApi(): Api {
  const api = useContext(Ctx);
  if (!api) throw new Error("useApi outside ApiProvider");
  return api;
}

export const POLL_MS = 5000;

export function usePoll<T>(load: () => Promise<T>, deps: unknown[] = [], every = POLL_MS) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    load()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);
  useEffect(() => {
    if (!every) return;
    const timer = setInterval(() => setTick((n) => n + 1), every);
    return () => clearInterval(timer);
  }, [every]);
  return { data, error, reload: () => setTick((n) => n + 1) };
}
