/** App-wide context: node connection, typed API, live event stream. */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { createApi, type Api } from "./api/client";
import type { Connection } from "./api/connection";
import { EventStream, type StreamState } from "./api/eventStream";
import type { EventType, PanosEvent } from "./api/events";

export interface Services {
  conn: Connection;
  api: Api;
  events: EventStream;
}

const Ctx = createContext<Services | null>(null);

export function ServicesProvider({ services, children }: { services: Services; children: ReactNode }) {
  return <Ctx.Provider value={services}>{children}</Ctx.Provider>;
}

export function useServices(): Services {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useServices outside ServicesProvider");
  return ctx;
}

export function makeServices(conn: Connection, events?: EventStream): Services {
  return { conn, api: createApi(conn), events: events ?? new EventStream(conn) };
}

/** Subscribe to one event type for the lifetime of a component. */
export function useEvent<T extends EventType>(type: T | "*", handler: (e: PanosEvent<T>) => void) {
  const { events } = useServices();
  useEffect(() => events.on(type, handler), [events, type, handler]);
}

/** Re-run ``load`` on mount, on resync, and whenever one of ``types`` arrives. */
export function useLive<T>(load: () => Promise<T>, types: EventType[] = []) {
  const { events } = useServices();
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
  }, [tick]);
  useEffect(() => {
    const offs = types.map((t) => events.on(t, () => setTick((n) => n + 1)));
    offs.push(events.onResync(() => setTick((n) => n + 1)));
    return () => offs.forEach((off) => off());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, types.join(",")]);
  return { data, error, reload: () => setTick((n) => n + 1) };
}

export function useStreamState(): StreamState {
  const { events } = useServices();
  const [state, setState] = useState<StreamState>(events.state);
  useEffect(() => events.onState(setState), [events]);
  return state;
}
