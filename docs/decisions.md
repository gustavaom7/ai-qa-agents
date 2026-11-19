# Decisions

Short records of choices that shape the repo. Newest first.

## 2026-11-19 — Delivery 3: report and review

**The record is a file, the review is a PR.** `findings/<agent>.json` is committed and is the source of truth. Each nightly run is merged into it by fingerprint: known findings keep their review and get fresh evidence, new ones arrive as drafts, nothing is removed. The nightly opens a PR; reviewing means setting each status with `agent review` and merging. No database, full history in git.

**One self-contained page.** The report is a single HTML file with inline CSS and a small theme script, plus copied evidence. It makes no external request, so it opens offline and from an artifact. It follows the Terminal Dashboard look of my other QA reports: flat, dark by default with a light theme, colour only for status and always with a text label, rates in blue.

**Demo runs are labelled and kept apart.** The bug-shop demo shows the pipeline on the public page before any live run, but it is marked "Demo · local fixture, scripted model", excluded from totals, and `collect` never merges it.

**Detector fix found by the demo.** Serving the bug shop over local HTTP produced a favicon 404 on the first page. The console-error detector fired once, on that noise, and the replay correctly dropped it, but the real checkout error later never became a candidate. The detector now creates one candidate per distinct message, matches that message on replay and ignores favicon noise. The e2e test runs over both `file://` and local HTTP to keep this covered.

## 2026-11-19 — Delivery 2: persona_testing

**The model proposes, code confirms.** A model-reported issue must include a check from a fixed list (`not_sorted`, `attr_all_equal`, ...). Code evaluates it on the live page, then again after a scripted replay in a fresh browser. The model never decides that a bug is real. This is what makes "reproduced" trustworthy and keeps hallucinations out of the findings.

**Own page snapshot instead of Playwright MCP.** A small script (`observe.js`) lists interactive elements with replayable CSS selectors (data-test, id, name, then a structural path). The model answers with a ref (`e7`); the step stores the selector, so the replay needs no model. This keeps the agent in Python and makes every step deterministic to re-run.

**Deterministic detectors next to the model.** Console errors and slow steps are flagged by code, with no tokens spent.

**Coarse timings in the prompt.** Steps are described as "fast", "1-3 s" or "slow" instead of exact milliseconds, so the same run produces the same prompts and recorded answers replay.

**A local bug shop for tests.** `tests/fixtures/bugshop/` has four planted bugs. The end-to-end test drives a real browser over `file://` pages with a scripted model: no network, no keys, and the whole loop (explore, live check, replay, evidence, recall) is covered.

**Recall from an answer key.** Known bugs carry the test user and the check kind that should catch them, so recall is computed, not estimated.

## 2026-11-19 — Delivery 1: the core

**Python, not TypeScript.** Same language as `llm-eval-qa`; pytest drives both the agents and their tests. Playwright for Python covers the browser work.

**Free LLM chain: Gemini → Groq → Ollama.** Gemini's free tier is the default because it is generous, needs no card and does vision (needed by `figma_diff`). Groq is a fast fallback. Ollama keeps local development offline and is the last resort; it is too slow on free CI runners to be the default. The Claude API is wired in for quality comparisons only and is off by default. Free tiers have daily caps and may use prompts for training, which is acceptable because every target is a public demo app.

**HTTP adapters instead of vendor SDKs.** Each provider is ~40 lines over httpx. Fewer dependencies, one error model (`ProviderUnavailableError` = try the next provider; `LLMError` = our bug), and tests swap the transport for `httpx.MockTransport`.

**Cassettes keyed by the request, not the provider.** The key hashes prompt, system prompt, image hashes, temperature and max tokens. An answer recorded with Gemini still replays if the chain changes, so pull-request CI stays deterministic and free. One file per answer under `cassettes/<agent>/` keeps PR diffs readable.

**One `Finding` schema for every agent.** Report, dedupe (fingerprint of agent + target + location + normalized title) and precision (confirmed ÷ reviewed) are written once. `reproduced` is set only by deterministic replay, never by a model.

**Known bugs as the answer key.** saucedemo's broken test users are listed in `targets/saucedemo.yaml` so agents report recall, not just finding counts.
