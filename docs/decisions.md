# Decisions

Short records of choices that shape the repo. Newest first.

## 2026-11-19 — Delivery 1: the core

**Python, not TypeScript.** Same language as `llm-eval-qa`; pytest drives both the agents and their tests. Playwright for Python covers the browser work.

**Free LLM chain: Gemini → Groq → Ollama.** Gemini's free tier is the default because it is generous, needs no card and does vision (needed by `figma_diff`). Groq is a fast fallback. Ollama keeps local development offline and is the last resort; it is too slow on free CI runners to be the default. The Claude API is wired in for quality comparisons only and is off by default. Free tiers have daily caps and may use prompts for training, which is acceptable because every target is a public demo app.

**HTTP adapters instead of vendor SDKs.** Each provider is ~40 lines over httpx. Fewer dependencies, one error model (`ProviderUnavailableError` = try the next provider; `LLMError` = our bug), and tests swap the transport for `httpx.MockTransport`.

**Cassettes keyed by the request, not the provider.** The key hashes prompt, system prompt, image hashes, temperature and max tokens. An answer recorded with Gemini still replays if the chain changes, so pull-request CI stays deterministic and free. One file per answer under `cassettes/<agent>/` keeps PR diffs readable.

**One `Finding` schema for every agent.** Report, dedupe (fingerprint of agent + target + location + normalized title) and precision (confirmed ÷ reviewed) are written once. `reproduced` is set only by deterministic replay, never by a model.

**Known bugs as the answer key.** saucedemo's broken test users are listed in `targets/saucedemo.yaml` so agents report recall, not just finding counts.
