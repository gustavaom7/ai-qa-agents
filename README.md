# ai-qa-agents

[![CI](https://github.com/gustavaom7/ai-qa-agents/actions/workflows/pr.yml/badge.svg?branch=main)](https://github.com/gustavaom7/ai-qa-agents/actions/workflows/pr.yml)

Autonomous QA agents that run every night against public demo apps and report findings a human reviews before anything becomes a bug. The point is AI applied to everyday QA work: simulated users that find bugs, data that breaks layouts, copy that drifts, screens that no longer match Figma.

Everything runs at **$0**: LLMs on free tiers or local, Android emulators on GitHub Actions.

> **Status: delivery 1 of 9, the core.** The LLM client, recorded answers, the finding schema, the targets and CI are in place. No agent ships yet. See the [roadmap](#roadmap).

## Agents (planned)

| Agent | What it reports | Target |
|---|---|---|
| `persona_testing` | Simulated users (first-timer, power user, slow 3G, keyboard-only) pursue a goal; a stuck run becomes a bug with steps and video, **only if a scripted replay fails the same way** | saucedemo |
| `worst_case_data` | 5,000-char text, emoji, RTL, zero and 10,000 items; overflow, clipping, console errors | TodoMVC, saucedemo |
| `copy_drift` | "Sign in" vs "Log in": every variant, where it appears, a suggested standard | both web targets |
| `design_tokens` | Colors, font sizes and spacing outside `tokens.json`, with a highlighted screenshot | saucedemo |
| `figma_diff` | Figma frame vs. the live screen, each differing region described | saucedemo |
| `device_sweep` | Maestro flows on an Android emulator matrix plus 20+ Playwright device profiles; broken screens | Wikipedia Android |
| `store_reviews` | 1–2 star App Store / Play Store reviews grouped by complaint and turned into test scenarios | Wikipedia Android |

Targets are public demo apps only (`targets/*.yaml`). saucedemo's deliberately broken users are the answer key: an agent's recall is measured against `known_bugs`.

## Quickstart

```bash
uv sync
cp .env.example .env          # add a free GEMINI_API_KEY and/or GROQ_API_KEY
uv run agent doctor           # which providers are ready
uv run agent list             # agents and targets
uv run pytest                 # offline, no keys needed
```

## How the LLM layer works

One client, `ai_qa_agents.core.llm.LLMClient`, used by every agent.

**Fallback chain.** Providers are tried in order. A provider with no key, a rate limit, an outage or bad credentials is skipped and the next one answers:

| Provider | Role | Cost |
|---|---|---|
| Gemini API (free tier) | Default, text and vision | $0, no card |
| Groq (free tier) | Fallback, fast | $0, no card |
| Ollama (local) | Offline development, last fallback | $0 |
| Claude API | Optional quality comparison (`LLM_PROVIDER=anthropic`), off by default | paid, tokens logged |

Override the chain with `LLM_PROVIDERS=groq,ollama`, or force one provider with `LLM_PROVIDER=ollama`.

**Recorded answers (cassettes).** Every answer is stored under `cassettes/<agent>/<key>.json`, keyed by the request (prompt, system, images), not by the provider. CI on pull requests replays them, so it is fast, free and deterministic.

| `LLM_MODE` | Behaviour |
|---|---|
| `auto` (default) | Replay if recorded, otherwise call and record |
| `replay` | Recorded answers only; a missing one fails. Used on pull requests |
| `record` | Always call and overwrite |
| `live` | Always call, never touch recordings |

**Usage log.** Every call appends tokens, latency, provider and cost to `runs/usage.jsonl`. The nightly report sums it per agent, so "$0" is measured, not claimed.

## Findings

Every agent emits the same `Finding` (`core/finding.py`): severity, steps, expected vs. actual, evidence (screenshot, video, trace, diff), a `reproduced` flag set by a deterministic replay (never by the model) and a review status. Findings start as `draft`; a human marks them `confirmed` or `false_positive`, and that history gives each agent's precision. A fingerprint (agent + target + location + normalized title) removes duplicates across nights.

## Repository layout

```
src/ai_qa_agents/
├── core/
│   ├── llm/          # client, providers, cassettes, usage log
│   ├── finding.py    # finding schema, dedupe, precision
│   └── targets.py    # target schema and loader
├── agents/           # one package per agent (from delivery 2)
└── cli.py            # `uv run agent ...`
targets/              # public demo apps, one YAML each
cassettes/            # recorded LLM answers, committed
findings/             # reviewed findings per agent, committed
tests/                # offline pytest suite
```

## Roadmap

| # | Delivery | Done when |
|---|---|---|
| 1 | **Core and green CI** ✅ | CI passes; the client falls back Gemini → Groq → Ollama; cassette replay works in tests |
| 2 | Persona testing on saucedemo | Finds 3+ known bugs of `problem_user` / `error_user`, each replay-confirmed with video |
| 3 | HTML report on GitHub Pages | Nightly publishes the page with the persona findings |
| 4 | Worst-case data | Every break has a screenshot and the data that caused it |
| 5 | Copy drift | Inconsistent-term table for both web targets |
| 6 | Design tokens | `visual_user` flagged as off-system |
| 7 | Figma vs. production | Every difference planted in the Figma spec is found; false positives measured |
| 8 | Device sweep | Wikipedia flows on 5+ Android emulators and 20+ web profiles |
| 9 | Store reviews | Complaint groups become test scenarios; one feeds the device matrix |

## Principles

1. **Human in the loop.** Every finding is a draft until reviewed.
2. **Reproduce before reporting.** A bug from an AI-driven run counts only if a scripted replay fails the same way.
3. **Measured, not claimed.** Precision, recall against known bugs, tokens and cost per run.
4. **Deterministic CI.** Pull requests replay recorded answers; live runs are nightly.
5. **Findings are public**, false positives included.

## License

MIT
