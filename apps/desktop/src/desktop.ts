/**
 * Tauri-only integration (ADR-015): global push-to-talk hotkey and desktop notifications.
 * Outside Tauri (browser dev) this is a no-op.
 */
import { isTauri } from "./api/connection";
import type { Services } from "./state";

export const DEFAULT_HOTKEY = "CommandOrControl+Shift+Space";

export interface IntegrationStatus {
  hotkey: string | null; // registered shortcut, if any
  hotkeyError: string | null; // AC-US002-02: reported, app keeps working without it
  notifications: "granted" | "denied" | "unavailable";
}

export const integration: IntegrationStatus = {
  hotkey: null,
  hotkeyError: null,
  notifications: "unavailable",
};

export async function setupDesktopIntegration(services: Services, hotkey = DEFAULT_HOTKEY) {
  if (!isTauri()) return integration;
  await registerHotkey(services, hotkey);
  await setupNotifications(services);
  return integration;
}

/** Start Panos at login (tauri-plugin-autostart, via the shell's own commands). */
export async function getAutostart(): Promise<boolean | null> {
  if (!isTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("autostart_enabled");
}

export async function setAutostart(enabled: boolean): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("set_autostart", { enabled });
}

async function registerHotkey(services: Services, hotkey: string) {
  const { register, isRegistered } = await import("@tauri-apps/plugin-global-shortcut");
  try {
    if (await isRegistered(hotkey)) return;
    await register(hotkey, (event) => {
      // Push-to-talk: pressed → listen, released → end of utterance (US-002).
      if (event.state === "Pressed") void services.api.POST("/api/v1/voice/listen");
      else void services.api.POST("/api/v1/voice/listen/stop");
    });
    integration.hotkey = hotkey;
  } catch (e) {
    integration.hotkeyError =
      `${hotkey} could not be registered (${e instanceof Error ? e.message : String(e)}). ` +
      "Try another shortcut, e.g. Ctrl+Alt+P; the talk button still works.";
  }
}

async function setupNotifications(services: Services) {
  const { isPermissionGranted, requestPermission, sendNotification } = await import(
    "@tauri-apps/plugin-notification"
  );
  let granted = await isPermissionGranted();
  if (!granted) granted = (await requestPermission()) === "granted";
  integration.notifications = granted ? "granted" : "denied";
  if (!granted) return;
  // Only what needs the user (approvals, blockers, questions, completion, failure) is
  // published as "notification" by the node; routine progress never is (AC-US051-02).
  services.events.on("notification", (e) => sendNotification({ title: e.payload.title, body: e.payload.body }));
}
