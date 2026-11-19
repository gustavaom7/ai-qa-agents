"""Token and cost log: every LLM call appends one JSON line.

The nightly report reads this file to show tokens and cost per agent, so
"runs at zero cost" is measured, not claimed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .types import LLMResponse


def cost_usd(response: LLMResponse, price: tuple[float, float] | None) -> float | None:
    """Cost from a (input, output) price per million tokens; None if untracked."""
    if response.from_cassette:
        return 0.0
    if price is None:
        return None
    per_in, per_out = price
    return round((response.input_tokens * per_in + response.output_tokens * per_out) / 1e6, 6)


class UsageLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, task: str, response: LLMResponse, cost: float | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "task": task,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_s": response.latency_s,
            "cost_usd": cost,
            "from_cassette": response.from_cassette,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def summary(self) -> dict[str, dict[str, Any]]:
        """Totals per agent (the part of ``task`` before the first dot)."""
        totals: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "live_calls": 0, "tokens": 0, "cost_usd": 0.0, "untracked": 0}
        )
        if not self.path.exists():
            return {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            agent = e["task"].split(".", 1)[0]
            t = totals[agent]
            t["calls"] += 1
            t["live_calls"] += 0 if e["from_cassette"] else 1
            t["tokens"] += e["input_tokens"] + e["output_tokens"]
            if e["cost_usd"] is None:
                t["untracked"] += 1
            else:
                t["cost_usd"] = round(t["cost_usd"] + e["cost_usd"], 6)
        return dict(totals)
