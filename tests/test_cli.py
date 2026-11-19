from __future__ import annotations

from pathlib import Path

import pytest

from ai_qa_agents.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    for var in ("LLM_PROVIDER", "LLM_PROVIDERS", "LLM_MODE"):
        monkeypatch.delenv(var, raising=False)


def test_list_shows_targets(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "saucedemo" in out
    assert "persona_testing" in out


def test_doctor_fails_without_any_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "gemini,groq")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert main(["doctor"]) == 1
    assert "no provider configured" in capsys.readouterr().out


def test_doctor_ok_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert main(["doctor"]) == 0


def test_run_unknown_agent(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "nope", "saucedemo"]) == 2
    assert "unknown agent" in capsys.readouterr().out


def test_ping_reports_failure_without_providers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["ping"]) == 1
    assert "gemini: not configured" in capsys.readouterr().out
