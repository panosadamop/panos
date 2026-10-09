# ADR-025: Screenshot handling

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-021, FR-015, NFR-PRV-002, US-013, AC-US013-01..04

## Context
A screenshot may show anything, including content from local-only projects or other applications.

## Decision
- Capture only on explicit request or an approved workflow step.
- Images are kept in memory only, labeled `LOCAL_ONLY` by default, and analyzed by a local vision model.
- Cloud vision only with explicit per-request consent, and only when the active project policy permits it.
- Never persisted unless the user saves the image or diagnostics retention is enabled.
- Every capture is audited (metadata only, no image).

## Alternatives considered
Persisting screenshots for history (privacy risk).

## Consequences
Analysis quality depends on available local vision models on CPU-only hardware (see S-1 / ADR-009).

## Implementation notes (M7, 2026-10-09)

- **Capture:** `mss` grabs the foreground window's rectangle (Windows: `GetWindowRect`) into a PNG in memory. On macOS/Linux the adapters don't report window bounds yet, so the whole primary screen is captured. Wayland is unsupported (mss needs X11), and there is no macOS screen-recording permission flow yet.
- **Entry points:** `POST /screen/analyze {question, project?, allow_cloud}`, `panos screen [-p project] [--cloud] "question"`, and the chat/voice intent "what's on my screen" (Greek and English).
- **Routing:** the image travels as `Message.images`, and the router requires a vision-capable model. Vision models are ordered local first (`gemma3:4b`, `qwen2.5vl:7b` in the registry). With `allow_cloud` **and** a STANDARD project, the request is labelled STANDARD with `prefer_cloud`. Otherwise it stays LOCAL_ONLY, and cloud models are refused even if consent was given.
- **Audit:** `screen.captured` with metadata only: window title (first 200 characters), process, size, privacy label, cloud consent, `persisted: false`. No image or text from the screen is stored. Note that the window title itself can be sensitive.
- **Tests:** no file is written during capture or analysis; routing picks local vision for LOCAL_ONLY projects; a cloud model is used only with consent and STANDARD. Live capture of the active window was verified on Windows. No local vision model is pulled on the reference machine yet (`ollama pull gemma3:4b`), so live end-to-end analysis has not been run; the analysis path is covered with fake providers.
