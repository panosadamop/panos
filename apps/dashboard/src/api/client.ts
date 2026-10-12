/** Typed client for the hub API (generated `schema.d.ts`). Same origin; cookie session + CSRF header. */
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

let csrf = "";
export function setCsrf(token: string) {
  csrf = token;
}

const withCsrf: Middleware = {
  onRequest({ request }) {
    if (csrf && request.method !== "GET") request.headers.set("X-CSRF-Token", csrf);
    return request;
  },
};

export function createApi(baseUrl = "") {
  const client = createClient<paths>({ baseUrl, credentials: "include" });
  client.use(withCsrf);
  return client;
}

export type Api = ReturnType<typeof createApi>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined && result.response.ok) return result.data;
  const detail = (result.error as { detail?: unknown } | undefined)?.detail;
  throw new ApiError(result.response.status, typeof detail === "string" ? detail : result.response.statusText);
}

export function errorText(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : "error";
}
