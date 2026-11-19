"""Recorded LLM answers, so CI on pull requests is fast and deterministic.

One JSON file per request, grouped by the agent that asked
(``cassettes/<agent>/<key>.json``), so a PR diff shows exactly which answers
were added or changed.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .types import LLMRequest, LLMResponse

PROMPT_PREVIEW_CHARS = 300


class Cassette:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, request: LLMRequest) -> Path:
        agent = request.task.split(".", 1)[0] or "unspecified"
        return self.root / agent / f"{request.cache_key()}.json"

    def get(self, request: LLMRequest) -> LLMResponse | None:
        path = self._path(request)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        response = dict(data["response"])
        response["from_cassette"] = True
        return LLMResponse(**response)

    def put(self, request: LLMRequest, response: LLMResponse) -> Path:
        path = self._path(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = asdict(response)
        stored.pop("from_cassette", None)
        stored["latency_s"] = 0.0  # keep diffs stable between recordings
        record = {
            "task": request.task,
            "prompt_preview": request.prompt[:PROMPT_PREVIEW_CHARS],
            "images": len(request.images),
            "response": stored,
        }
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path
