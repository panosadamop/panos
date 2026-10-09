/** Typed client for the node API (generated `schema.d.ts`, ADR-018, M7-02). */
import createClient from "openapi-fetch";

import type { Connection } from "./connection";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Api = ReturnType<typeof createApi>;

export function createApi(conn: Connection) {
  return createClient<paths>({
    baseUrl: conn.baseUrl,
    headers: { Authorization: `Bearer ${conn.token}` },
  });
}

/** Raise a readable error for an openapi-fetch result. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined && result.response.ok) return result.data;
  const err = result.error as { detail?: unknown } | undefined;
  const detail = typeof err?.detail === "string" ? err.detail : result.response.statusText;
  throw new ApiError(result.response.status, detail, result.error);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
  }
}

export type StreamEvent =
  | { type: "start"; conversation_id: string; intent: string; privacy: string }
  | { type: "route"; provider: string; model: string; locality: string; reasons: string[] }
  | { type: "text"; text: string }
  | { type: "reply"; reply: Schemas["ReplyView"] }
  | { type: "error"; error: { detail: string; reason?: string; rejected?: unknown[] } };

/** POST /conversation/messages with `stream: true` and yield NDJSON events as they arrive. */
export async function* streamMessage(
  conn: Connection,
  body: { text: string; conversation_id?: string | null; project?: string | null },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${conn.baseUrl}/api/v1/conversation/messages`, {
    method: "POST",
    headers: { Authorization: `Bearer ${conn.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) yield JSON.parse(line) as StreamEvent;
    }
    if (done) break;
  }
  if (buffer.trim()) yield JSON.parse(buffer) as StreamEvent;
}
