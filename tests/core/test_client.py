"""Fallback chain, cassette modes and usage logging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_qa_agents.core.llm import (
    AllProvidersFailedError,
    Cassette,
    CassetteMissError,
    LLMClient,
    LLMRequest,
    Provider,
    ProviderUnavailableError,
    UsageLog,
)

REQ = LLMRequest(prompt="group these labels", task="copy_drift.group")


class FakeProvider(Provider):
    def __init__(
        self,
        name: str,
        answer: str = "ok",
        fail: bool = False,
        configured: bool = True,
        price: tuple[float, float] | None = (0.0, 0.0),
    ) -> None:
        super().__init__(model=f"{name}-model")
        self.name = name
        self.answer = answer
        self.fail = fail
        self.configured = configured
        self.cost_per_mtok = price
        self.calls = 0

    def is_configured(self) -> bool:
        return self.configured

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError(f"{self.name}: rate limited")
        return self.answer, 100, 20


def make(tmp_path: Path, providers: list[Provider], mode: str = "auto") -> LLMClient:
    return LLMClient(
        providers,
        cassette=Cassette(tmp_path / "cassettes"),
        usage_log=UsageLog(tmp_path / "runs" / "usage.jsonl"),
        mode=mode,  # type: ignore[arg-type]
    )


def test_falls_back_to_next_provider(tmp_path: Path) -> None:
    gemini = FakeProvider("gemini", fail=True)
    groq = FakeProvider("groq", answer="from groq")
    resp = make(tmp_path, [gemini, groq], mode="live").complete(REQ)
    assert resp.text == "from groq"
    assert resp.provider == "groq"
    assert gemini.calls == 1


def test_skips_unconfigured_provider_without_calling(tmp_path: Path) -> None:
    gemini = FakeProvider("gemini", configured=False)
    ollama = FakeProvider("ollama", answer="local")
    assert make(tmp_path, [gemini, ollama], mode="live").complete(REQ).text == "local"
    assert gemini.calls == 0


def test_all_failed_lists_every_reason(tmp_path: Path) -> None:
    client = make(
        tmp_path, [FakeProvider("gemini", fail=True), FakeProvider("groq", configured=False)]
    )
    with pytest.raises(AllProvidersFailedError) as info:
        client.complete(REQ)
    assert "gemini: rate limited" in str(info.value)
    assert "groq: not configured" in str(info.value)


def test_auto_records_then_replays_without_calling(tmp_path: Path) -> None:
    provider = FakeProvider("gemini", answer="first")
    client = make(tmp_path, [provider])
    assert client.complete(REQ).text == "first"
    provider.answer = "second"
    again = client.complete(REQ)
    assert again.text == "first"
    assert again.from_cassette is True
    assert provider.calls == 1
    files = list((tmp_path / "cassettes" / "copy_drift").glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text())
    assert stored["task"] == "copy_drift.group"
    assert stored["response"]["latency_s"] == 0.0


def test_replay_miss_raises_and_never_calls(tmp_path: Path) -> None:
    provider = FakeProvider("gemini")
    with pytest.raises(CassetteMissError, match=r"copy_drift\.group"):
        make(tmp_path, [provider], mode="replay").complete(REQ)
    assert provider.calls == 0


def test_replay_hit_ignores_provider_change(tmp_path: Path) -> None:
    make(tmp_path, [FakeProvider("gemini", answer="recorded")]).complete(REQ)
    resp = make(tmp_path, [FakeProvider("ollama", fail=True)], mode="replay").complete(REQ)
    assert resp.text == "recorded"
    assert resp.provider == "gemini"


def test_record_overwrites(tmp_path: Path) -> None:
    provider = FakeProvider("gemini", answer="v1")
    make(tmp_path, [provider]).complete(REQ)
    provider.answer = "v2"
    make(tmp_path, [provider], mode="record").complete(REQ)
    assert make(tmp_path, [provider], mode="replay").complete(REQ).text == "v2"


def test_live_never_writes_cassettes(tmp_path: Path) -> None:
    make(tmp_path, [FakeProvider("gemini")], mode="live").complete(REQ)
    assert not (tmp_path / "cassettes").exists()


def test_cache_key_depends_on_prompt_not_task_label() -> None:
    a = LLMRequest(prompt="x", task="a.b")
    assert a.cache_key() == LLMRequest(prompt="x", task="c.d").cache_key()
    assert a.cache_key() != LLMRequest(prompt="y", task="a.b").cache_key()


def test_usage_log_and_summary(tmp_path: Path) -> None:
    free = FakeProvider("gemini")
    paid = FakeProvider("anthropic", price=None)
    client = make(tmp_path, [free])
    client.complete(REQ)  # live call, recorded
    client.complete(REQ)  # replay
    make(tmp_path, [paid], mode="live").complete(LLMRequest(prompt="z", task="persona.step"))

    summary = UsageLog(tmp_path / "runs" / "usage.jsonl").summary()
    assert summary["copy_drift"] == {
        "calls": 2,
        "live_calls": 1,
        "tokens": 240,
        "cost_usd": 0.0,
        "untracked": 0,
    }
    assert summary["persona"]["untracked"] == 1


def test_priced_provider_cost(tmp_path: Path) -> None:
    provider = FakeProvider("anthropic", price=(1.0, 5.0))
    make(tmp_path, [provider], mode="live").complete(REQ)
    line = json.loads((tmp_path / "runs" / "usage.jsonl").read_text().splitlines()[0])
    assert line["cost_usd"] == pytest.approx((100 * 1.0 + 20 * 5.0) / 1e6)


def test_from_env_chain_and_forced_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    monkeypatch.delenv("LLM_MODE", raising=False)
    client = LLMClient.from_env(tmp_path)
    assert [p.name for p in client.providers] == ["gemini", "groq", "ollama"]
    assert client.mode == "auto"

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert [p.name for p in LLMClient.from_env(tmp_path).providers] == ["anthropic"]


@pytest.mark.parametrize(
    ("var", "value"), [("LLM_PROVIDERS", "gemini,nope"), ("LLM_MODE", "sometimes")]
)
def test_from_env_rejects_bad_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError):
        LLMClient.from_env(tmp_path)
