/** Push-to-talk (US-002): press and hold (mouse, Space or Enter); release ends the utterance. */
import { useCallback, useState } from "react";

import { useI18n } from "../i18n";
import { useEvent, useLive, useServices } from "../state";

export function VoiceButton() {
  const { t } = useI18n();
  const { api } = useServices();
  const [state, setState] = useState<string>("idle");
  const status = useLive(async () => (await api.GET("/api/v1/voice/status")).data ?? null, [
    "voice.ready",
    "voice.error",
  ]);
  useEvent(
    "voice.state",
    useCallback((e) => setState(e.payload.state), []),
  );
  const running = !!status.data?.running;
  const press = () => running && void api.POST("/api/v1/voice/listen");
  const release = () => running && void api.POST("/api/v1/voice/listen/stop");

  const label = !running
    ? t("voice.off")
    : state === "listening"
      ? t("voice.listening")
      : state === "transcribing"
        ? t("voice.transcribing")
        : t("voice.talk");

  return (
    <div className="voice">
      <button
        type="button"
        className={`ptt ptt-${state}`}
        aria-pressed={state === "listening"}
        disabled={!running}
        onMouseDown={press}
        onMouseUp={release}
        onKeyDown={(e) => {
          if ((e.key === " " || e.key === "Enter") && !e.repeat) press();
        }}
        onKeyUp={(e) => {
          if (e.key === " " || e.key === "Enter") release();
        }}
      >
        🎙 {label}
      </button>
      {running && (
        <button type="button" onClick={() => void api.POST("/api/v1/voice/stop-talking")}>
          {t("voice.stopTalking")}
        </button>
      )}
    </div>
  );
}
