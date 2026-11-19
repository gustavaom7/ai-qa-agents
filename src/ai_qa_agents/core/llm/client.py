"""The one LLM entry point every agent uses.

    client = LLMClient.from_env()
    answer = client.complete(LLMRequest(prompt="...", task="copy_drift.group"))

Providers are tried in order (default: gemini -> groq -> ollama). A provider
that is not configured, rate limited or down is skipped; the next one answers.

Cassette modes (``LLM_MODE``):
    auto    replay a recorded answer if there is one, otherwise call and record (default)
    replay  recorded answers only; a missing one fails (used by CI on pull requests)
    record  always call and overwrite the recording
    live    always call, never touch recordings
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .cassette import Cassette
from .providers import PROVIDERS, Provider
from .types import (
    AllProvidersFailedError,
    CassetteMissError,
    LLMRequest,
    LLMResponse,
    ProviderUnavailableError,
)
from .usage import UsageLog, cost_usd

Mode = Literal["auto", "replay", "record", "live"]
MODES: tuple[Mode, ...] = ("auto", "replay", "record", "live")
DEFAULT_CHAIN = "gemini,groq,ollama"


class LLMClient:
    def __init__(
        self,
        providers: Sequence[Provider],
        cassette: Cassette | None = None,
        usage_log: UsageLog | None = None,
        mode: Mode = "auto",
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"LLM_MODE must be one of {MODES}, got {mode!r}")
        if mode != "live" and cassette is None:
            raise ValueError(f"mode {mode!r} needs a cassette")
        self.providers = list(providers)
        self.cassette = cassette
        self.usage_log = usage_log
        self.mode = mode

    @classmethod
    def from_env(cls, root: Path | None = None) -> LLMClient:
        root = root or Path(os.getenv("AIQA_ROOT", "."))
        forced = os.getenv("LLM_PROVIDER", "").strip()
        chain = forced or os.getenv("LLM_PROVIDERS", DEFAULT_CHAIN)
        names = [n.strip() for n in chain.split(",") if n.strip()]
        unknown = [n for n in names if n not in PROVIDERS]
        if unknown:
            raise ValueError(f"unknown providers {unknown}; known: {sorted(PROVIDERS)}")
        mode = os.getenv("LLM_MODE", "auto")
        if mode not in MODES:
            raise ValueError(f"LLM_MODE must be one of {MODES}, got {mode!r}")
        return cls(
            providers=[PROVIDERS[n]() for n in names],
            cassette=Cassette(root / "cassettes"),
            usage_log=UsageLog(root / "runs" / "usage.jsonl"),
            mode=mode,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.cassette is not None and self.mode in ("auto", "replay"):
            recorded = self.cassette.get(request)
            if recorded is not None:
                self._log(request, recorded, price=(0.0, 0.0))
                return recorded
            if self.mode == "replay":
                raise CassetteMissError(
                    f"no recording for task {request.task!r} (key {request.cache_key()}); "
                    "run with LLM_MODE=auto or record to create it"
                )

        response, provider = self._call_chain(request)
        if self.cassette is not None and self.mode in ("auto", "record"):
            self.cassette.put(request, response)
        self._log(request, response, price=provider.cost_per_mtok)
        return response

    def _call_chain(self, request: LLMRequest) -> tuple[LLMResponse, Provider]:
        reasons: list[str] = []
        for provider in self.providers:
            try:
                return provider.complete(request), provider
            except ProviderUnavailableError as exc:
                reasons.append(str(exc))
        raise AllProvidersFailedError("no provider answered:\n  " + "\n  ".join(reasons))

    def _log(
        self, request: LLMRequest, response: LLMResponse, price: tuple[float, float] | None
    ) -> None:
        if self.usage_log is not None:
            self.usage_log.append(request.task, response, cost_usd(response, price))
