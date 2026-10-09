/**
 * Where the local node is and how to authenticate (ADR-002/003).
 *
 * In the Tauri app the Rust shell reads the node's runtime file and hands over the base URL
 * and token through the `connection` command (the token never touches the WebView's
 * storage). In a plain browser (`pnpm dev`) they come from VITE_PANOS_URL / VITE_PANOS_TOKEN.
 */

export interface Connection {
  baseUrl: string; // e.g. http://127.0.0.1:48123
  token: string;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
    __PANOS_CONNECTION__?: Connection; // tests
  }
}

export const isTauri = (): boolean => typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

export async function getConnection(): Promise<Connection> {
  if (window.__PANOS_CONNECTION__) return window.__PANOS_CONNECTION__;
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<Connection>("connection");
  }
  const baseUrl = import.meta.env.VITE_PANOS_URL as string | undefined;
  const token = import.meta.env.VITE_PANOS_TOKEN as string | undefined;
  if (!baseUrl || !token) {
    throw new Error("No Panos node connection (start `panos serve`, or set VITE_PANOS_URL/TOKEN)");
  }
  return { baseUrl, token };
}
