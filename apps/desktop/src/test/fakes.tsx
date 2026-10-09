/** Test doubles: a routing fake fetch (the node API) and a controllable fake WebSocket. */
import { render } from "@testing-library/react";
import type { ReactNode } from "react";

import { App, type ViewId } from "../App";
import type { Connection } from "../api/connection";
import { EventStream, type SocketLike } from "../api/eventStream";
import type { EventType, PanosEvent } from "../api/events";
import { I18nProvider, type Lang } from "../i18n";
import { makeServices, ServicesProvider, type Services } from "../state";

export const CONN: Connection = { baseUrl: "http://127.0.0.1:48123", token: "t0ken" };

type Handler = (body: unknown, url: URL) => unknown;
export interface FakeApi {
  routes: Map<string, Handler>;
  calls: { method: string; path: string; body: unknown; auth: string | null }[];
  on(method: string, path: string, handler: Handler | unknown): void;
}

export function fakeApi(): FakeApi {
  const api: FakeApi = {
    routes: new Map(),
    calls: [],
    on(method, path, handler) {
      api.routes.set(`${method} ${path}`, typeof handler === "function" ? (handler as Handler) : () => handler);
    },
  };
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(request.url);
    const text = request.method === "GET" ? "" : await request.clone().text();
    const body = text ? JSON.parse(text) : undefined;
    api.calls.push({ method: request.method, path: url.pathname, body, auth: request.headers.get("Authorization") });
    const handler = api.routes.get(`${request.method} ${url.pathname}`);
    if (!handler) return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    const result = handler(body, url);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result ?? {}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  globalThis.fetch = fetchImpl as typeof fetch;
  // Defaults most views need.
  api.on("GET", "/api/v1/approvals", { approvals: [] });
  api.on("GET", "/api/v1/voice/status", {
    enabled: false, running: false, state: "stopped", wake_word: null, wake_word_enabled: false,
    tts: true, voice_notifications: true, language: "el", last_transcript: null, last_error: null, restarts: 0, missing_models: [],
  });
  return api;
}

export class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  sent: unknown[] = [];
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.closed = true;
    this.onclose?.({});
  }

  open() {
    this.onopen?.({});
  }

  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

let seq = 0;
export function event<T extends EventType>(type: T, payload: PanosEvent<T>["payload"]): PanosEvent<T> {
  seq += 1;
  return { v: 1, seq, type, id: `e${seq}`, ts: new Date().toISOString(), correlation_id: null, job_id: null, payload };
}

export function services(): { services: Services; socket: () => FakeSocket } {
  FakeSocket.all = [];
  const stream = new EventStream(CONN, (url) => new FakeSocket(url), () => 10);
  const s = makeServices(CONN, stream);
  stream.start();
  return { services: s, socket: () => FakeSocket.all[FakeSocket.all.length - 1] };
}

export function renderWith(ui: ReactNode, s: Services, lang: Lang = "en") {
  return render(
    <I18nProvider lang={lang}>
      <ServicesProvider services={s}>{ui}</ServicesProvider>
    </I18nProvider>,
  );
}

export function renderApp(view: ViewId, s: Services, lang: Lang = "en") {
  return renderWith(<App initial={view} />, s, lang);
}

export function ndjson(events: unknown[]): Response {
  const body = events.map((e) => JSON.stringify(e)).join("\n") + "\n";
  // Split mid-line to prove the reader reassembles chunks.
  const middle = Math.floor(body.length / 2);
  const stream = new ReadableStream({
    start(controller) {
      const enc = new TextEncoder();
      controller.enqueue(enc.encode(body.slice(0, middle)));
      controller.enqueue(enc.encode(body.slice(middle)));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
}
