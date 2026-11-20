# ai-qa-agents — notes for Claude Code

Autonomous QA agents against public demo apps. Python 3.12, uv, Playwright for Python (from delivery 2), pytest.

## Commands

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run agent list | doctor | ping | run <agent> <target>
uv run agent collect <agent> | review <agent> <fp> <status> | report
uv run python scripts/demo_bugshop.py   # demo run, no keys
```

All four checks must pass before a commit. CI runs them with `LLM_MODE=replay`.
Browser tests need Chromium: `uv run playwright install chromium ffmpeg`, or `PW_CHANNEL=chrome` to use an installed Chrome.

## Rules

- **Only public demo targets** (`targets/*.yaml`). Never point an agent at an employer's or client's app.
- **Every LLM call goes through `LLMClient`** (`core/llm`). Never call a vendor SDK or API directly from an agent; give each request a `task` like `"<agent>.<step>"` so cassettes and usage group by agent.
- **Agents return `Finding` objects** (`core/finding.py`), always as `draft`. Only a deterministic check or replay may set `reproduced=True`; never the model.
- **Free by default.** The default chain is gemini → groq → ollama. Do not make a paid provider the default.
- **Cassettes are committed.** A new agent ships with recorded answers so its tests run offline. Re-record with `LLM_MODE=record` and review the diff.
- **Tests are offline.** Use `httpx.MockTransport` or a fake `Provider`; no test may need a key or the network.
- **Report style** lives only in `core/report/style.css`, `theme.js` and the generated `fonts.css` (regenerate with `scripts/vendor_fonts.py`, never edit by hand); no CSS in Python strings, no external URLs (fonts, scripts, images). Status always has a text label, not just a colour.
- **Demo runs** (`meta.demo`) are never merged into `findings/`.
- New agent = package under `src/ai_qa_agents/agents/<name>/`, registered with `@register("<name>")`, a `findings/<name>.md`, and tests.
