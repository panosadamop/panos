# ADR-009: AI provider abstraction, local runtime and tiers

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-003, BR-027, FR-021, FR-023, NFR-PORT-003, NFR-MNT-004, NFR-PERF-002

## Context
Panos must route between local and cloud models without hard-coding vendors. D-4/D-5 selected Ollama as the local runtime and Anthropic + OpenAI as the first cloud providers. S-1 (docs/planning/S-1_voice_feasibility.md §6, §7.2) measured the CPU-only reference machine (Ryzen 7 PRO 7735U; the Radeon 680M is dropped by Ollama; 62 GB RAM). Q4_K_M generation: 3B ≈ 11–13 tok/s, 7B coder ≈ 5–7 tok/s, 14B coder ≈ 2.8 tok/s. Prompt eval: 12–64 tok/s. qwen2.5-coder emits tool calls as JSON text instead of native `tool_calls`.

## Decision
**Ports:** provider-neutral `ChatModel` (streaming, tool calls), `EmbeddingModel`, `VisionModel`.

**Adapters:**
- OpenAI-compatible / Ollama HTTP (default local);
- Anthropic and OpenAI (official SDKs as optional extras);
- a scripted mock for tests.

**Locality:** a provider counts as local only if its endpoint is loopback, or a LAN host the user explicitly trusts.

**Capability registry (YAML + per-machine measurements):**
- tier, context size, `tool_use: native | json_in_content | none`, vision, languages, cost and privacy eligibility;
- **measured `prompt_tps`, `gen_tps` and `ttft` per machine**, seeded from S-1 and refreshed by a micro-benchmark during health checks.

**Tool-call fallback:** for `json_in_content` models, a strict parser accepts `{"name", "arguments"}` content only if it matches a declared tool schema. Everything else is treated as text.

**Default local tiers on reference hardware:**

| Tier | Default model | Use |
|---|---|---|
| Local lightweight | `qwen2.5:3b`, kept warm | Intent classification, tool selection, short replies |
| Local capable | `qwen2.5-coder:7b` | Privacy-restricted coding and requirements work, run as background jobs with trimmed context |
| Local large | `qwen2.5-coder:14b` | Background or overnight only |

**Routing:**
- The latency score includes prompt length (prompt eval dominates on CPU).
- When local-only policy forces a slow tier, the router reports the expected duration and the work runs as a background Job.
- Interactive and agentic coding uses cloud tiers when policy permits.

**Resource coordination:** a local-inference scheduler avoids running LLM generation concurrently with STT, or caps `num_thread`. Larger models are unloaded after use (`keep_alive`).

## Alternatives considered
LiteLLM (broad, but a large dependency in the privacy-critical path); LangChain model wrappers; llama-cpp-python in-process (couples the node to native builds; Ollama isolates crashes and model management).

## Consequences
- Local-only projects are **private but slow** on this hardware. Multi-file agentic coding with a 7B model takes minutes per step, which is acceptable only as background work.
- Qwen2.5 Greek prose is weak. Greek user-facing replies under local-only policy will have grammatical errors until a Greek-capable local model is evaluated.
- **Follow-ups (M4):** Ollama with `OLLAMA_IGPU_ENABLE=1` plus an AMD driver update; an MoE model (e.g. qwen3-30b-a3b); a Greek-capable local model; fastembed vs `nomic-embed-text`.
- Model licences: Qwen2.5 3B is research/non-commercial and 7B/14B are Apache-2.0, acceptable for personal use (D-6).

## Implementation notes (M4, 2026-10-09)

- **Ports:** `ChatProvider` (`chat`, `stream`, `embed`, `health`, `close`) with provider-neutral types (`panos_core.ai.types`).
- **Adapters:**
  - Ollama uses the native API, for timings that feed the registry's speed EMA.
  - OpenAI-compatible covers llama.cpp, LM Studio, vLLM and OpenAI via SSE.
  - Anthropic uses the official SDK (optional extra `panos-core[anthropic]`).
  - A scripted mock is used in tests.
  - Locality comes from the endpoint: loopback is local; a private IP is local only with `trusted_lan`; any DNS name other than localhost is cloud.
- **Anthropic specifics:**
  - Sampling parameters and forced `tool_choice` are never sent.
  - Thinking is adaptive (default); depth is set with `output_config.effort` (not for Haiku 4.5).
  - The server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) is on by default for Sonnet/Opus 5.5 (`ai.anthropic_refusal_fallback`).
  - `stop_reason: "refusal"` is surfaced.
- **Tier mapping (AI_ROUTING):** `cloud_light` = Claude Haiku 4.5, `cloud_efficient` = Claude Sonnet 5.5, `cloud_premium` = Claude Opus 5.5. Local tiers are qwen2.5 3B/7B/14B; embeddings use `nomic-embed-text` via Ollama. Registry: `panos_core/ai/defaults/models.yaml`.
- **Router:**
  - hard filters: privacy → health → tier suitability → tools/vision → context fit → premium gate → per-request cost → monthly budget;
  - ordering: tier preference for the task family, then estimated latency (measured tokens/s), then cost;
  - every decision carries reasons and rejected candidates (`POST /ai/route`, `panos ai route`).
- **Gateway:** the single path to a model.
  - It redacts for cloud and meters usage (`ai_usage` table, migration 0004).
  - A retryable failure (e.g. cloud unreachable) marks the provider unhealthy and re-routes once, giving offline fallback to local models without loops.
  - Health is cached for 60 s. Local runtimes must list the model (it is pulled); cloud providers are trusted to serve registry models.
- **Keys:** stored in the OS keyring as `ai.<provider>.api_key` via `panos ai set-key` (hidden prompt or `--stdin`, never argv). They are never read from the environment.
