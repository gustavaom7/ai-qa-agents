"""Persona agent pieces that need no browser: checks, parsing, prompts, recall, data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_qa_agents.agents import REGISTRY
from ai_qa_agents.agents.persona_testing.agent import recall
from ai_qa_agents.agents.persona_testing.brain import build_prompt, decide, parse_decision
from ai_qa_agents.agents.persona_testing.checks import evaluate
from ai_qa_agents.agents.persona_testing.driver import Element, Observation
from ai_qa_agents.agents.persona_testing.models import (
    Check,
    Mission,
    Step,
    load_missions,
    load_personas,
)
from ai_qa_agents.core import Finding, Severity, load_targets

from .conftest import scripted_client

ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    def __init__(self, **data: object) -> None:
        self.data = data

    @property
    def url(self) -> str:
        return str(self.data.get("url", "https://shop.test/inventory.html"))

    def texts(self, selector: str) -> list[str]:
        return list(self.data.get("texts", {}).get(selector, []))  # type: ignore[attr-defined]

    def attrs(self, selector: str, attr: str) -> list[str | None]:
        return list(self.data.get("attrs", {}).get((selector, attr), []))  # type: ignore[attr-defined]

    def input_values(self, selector: str) -> list[str]:
        return list(self.data.get("values", {}).get(selector, []))  # type: ignore[attr-defined]

    def visible(self, selector: str) -> bool:
        return selector in self.data.get("visible", ())  # type: ignore[operator]

    def console_errors(self) -> list[str]:
        return list(self.data.get("errors", []))  # type: ignore[call-overload]

    def observe(self) -> Observation:
        raise NotImplementedError

    def execute(self, step: Step) -> Step:
        raise NotImplementedError

    def screenshot(self, path: Path) -> None:
        raise NotImplementedError


def check(**kw: object) -> Check:
    return Check.model_validate(kw)


# -- checks ------------------------------------------------------------------


def test_not_sorted_numbers() -> None:
    s = FakeSession(texts={".price": ["$29.99", "$9.99", "$7.99"]})
    assert evaluate(check(kind="not_sorted", selector=".price", by="number"), s, []).confirmed
    s = FakeSession(texts={".price": ["$7.99", "$9.99", "$29.99"]})
    result = evaluate(check(kind="not_sorted", selector=".price", by="number"), s, [])
    assert result.valid and not result.bug
    desc = check(kind="not_sorted", selector=".price", by="number", order="desc")
    assert evaluate(desc, s, []).confirmed


def test_not_sorted_needs_two_items() -> None:
    s = FakeSession(texts={".name": ["Only"]})
    assert not evaluate(check(kind="not_sorted", selector=".name"), s, []).valid


def test_attr_all_equal() -> None:
    same = FakeSession(attrs={("img", "src"): ["dog.png", "dog.png", "dog.png"]})
    assert evaluate(check(kind="attr_all_equal", selector="img", attr="src"), same, []).confirmed
    mixed = FakeSession(attrs={("img", "src"): ["a.png", "b.png"]})
    assert not evaluate(check(kind="attr_all_equal", selector="img", attr="src"), mixed, []).bug
    one = FakeSession(attrs={("img", "src"): ["a.png"]})
    assert not evaluate(check(kind="attr_all_equal", selector="img", attr="src"), one, []).valid


def test_input_value_differs() -> None:
    s = FakeSession(values={"#last": [""]})
    c = check(kind="input_value_differs", selector="#last", expected="Mesquita")
    assert evaluate(c, s, []).confirmed
    assert not evaluate(c, FakeSession(values={"#last": ["Mesquita"]}), []).bug
    assert not evaluate(c, FakeSession(), []).valid


def test_visible_url_text_console() -> None:
    s = FakeSession(
        visible={".error"},
        url="https://shop.test/checkout-step-one.html",
        texts={".badge": ["1"]},
        errors=["boom"],
    )
    assert evaluate(check(kind="visible", selector=".error"), s, []).confirmed
    assert not evaluate(check(kind="visible", selector=".nope"), s, []).bug
    assert evaluate(check(kind="url_lacks", expected="complete"), s, []).confirmed
    assert evaluate(check(kind="text_lacks", selector=".badge", expected="2"), s, []).confirmed
    assert evaluate(check(kind="console_error"), s, []).confirmed
    assert not evaluate(check(kind="console_error"), FakeSession(), []).bug
    assert evaluate(check(kind="console_error", expected="bo"), s, []).confirmed
    assert not evaluate(check(kind="console_error", expected="other"), s, []).bug


def test_slow_step_uses_given_or_last_step() -> None:
    steps = [Step(0, "goto", duration_ms=500), Step(1, "click", duration_ms=6000)]
    assert evaluate(check(kind="slow_step", ms=3000), FakeSession(), steps).confirmed
    assert not evaluate(check(kind="slow_step", step=0, ms=3000), FakeSession(), steps).bug
    assert not evaluate(check(kind="slow_step"), FakeSession(), []).valid


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "visible"},
        {"kind": "attr_all_equal", "selector": "img"},
        {"kind": "url_lacks"},
        {"kind": "text_lacks", "selector": ".x"},
    ],
)
def test_incomplete_checks_are_invalid(kw: dict[str, object]) -> None:
    assert not evaluate(check(**kw), FakeSession(texts={".x": ["a"]}), []).valid


# -- parsing and prompting ---------------------------------------------------


def test_parse_decision_tolerates_fences_and_prose() -> None:
    text = 'Sure!\n```json\n{"thought":"t","action":{"type":"click","ref":"e2"}}\n```'
    d = parse_decision(text)
    assert d.action.type == "click"
    assert d.action.ref == "e2"
    assert d.issue is None


def test_parse_decision_drops_issue_without_check() -> None:
    d = parse_decision(json.dumps({"action": {"type": "done"}, "issue": {"title": "x"}}))
    assert d.issue is None


def test_parse_decision_reads_issue() -> None:
    d = parse_decision(
        json.dumps(
            {
                "action": {"type": "click", "ref": "e1"},
                "issue": {
                    "title": "Sort broken",
                    "severity": "high",
                    "check": {"kind": "not_sorted", "selector": ".p", "by": "number"},
                },
            }
        )
    )
    assert d.issue is not None
    assert d.issue.severity == Severity.HIGH
    assert d.issue.check.by == "number"


def test_parse_decision_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_decision("I would click the button")


def _obs() -> Observation:
    return Observation(
        url="https://shop.test/",
        title="Shop",
        text="Welcome",
        elements=[Element("e1", "button", "", "Login", '[data-test="login"]')],
        images=[],
    )


def test_prompt_has_goal_credentials_and_coarse_timing() -> None:
    personas = load_personas()
    mission = Mission(id="m", persona="power_user", user="problem_user", goal="Buy it")
    steps = [Step(0, "goto", value="https://shop.test/", duration_ms=123)]
    prompt = build_prompt(
        mission, personas["power_user"], "secret", _obs(), steps, ["a note"], ["Old bug"]
    )
    assert "GOAL: Buy it" in prompt
    assert 'password "secret"' in prompt
    assert "e1 button 'Login' [data-test=\"login\"]" in prompt
    assert "fast" in prompt
    assert "123" not in prompt, "exact timings would break cassette keys between runs"
    assert "Old bug" in prompt
    assert "note: a note" in prompt


def test_decide_retries_once_then_gives_up() -> None:
    personas = load_personas()
    mission = Mission(id="m", persona="power_user", user="u", goal="g")
    client, provider = scripted_client([{"raw": "nonsense"}, {"raw": "still nonsense"}])
    d = decide(client, mission, personas["power_user"], "", _obs(), [], [], [])
    assert d.action.type == "give_up"
    assert len(provider.prompts) == 2
    assert "INVALID" in provider.prompts[1]


def test_decide_recovers_on_retry() -> None:
    personas = load_personas()
    mission = Mission(id="m", persona="power_user", user="u", goal="g")
    client, _ = scripted_client([{"raw": "nonsense"}, {"type": "click", "label": "Login"}])
    d = decide(client, mission, personas["power_user"], "", _obs(), [], [], [])
    assert d.action.type == "click"
    assert d.action.ref == "e1"


# -- data and recall ---------------------------------------------------------


def test_registered() -> None:
    assert "persona_testing" in REGISTRY


def test_saucedemo_missions_reference_real_personas_and_users() -> None:
    personas = load_personas()
    target = load_targets(ROOT / "targets")["saucedemo"]
    missions = load_missions("saucedemo")
    assert len(missions) >= 5
    for m in missions:
        assert m.persona in personas
        assert m.user in target.credentials
    assert any(m.user == "standard_user" for m in missions), "a control run is required"


def test_recall_matches_user_and_check_kind() -> None:
    target = load_targets(ROOT / "targets")["saucedemo"]

    def f(user: str, kind: str) -> Finding:
        return Finding(
            agent="persona_testing",
            target="saucedemo",
            title=kind,
            severity=Severity.HIGH,
            summary="",
            tags={"user": user, "check_kind": kind},
        )

    found = [f("problem_user", "not_sorted"), f("standard_user", "attr_all_equal")]
    rec = recall(found, target, {"problem_user"})
    assert rec["found"] == ["problem-user-sort"]
    assert "problem-user-images" in rec["missed"]  # right kind, wrong user does not count
    assert rec["recall"] == 0.333


def test_recall_ignores_out_of_scope_signals() -> None:
    target = load_targets(ROOT / "targets")["saucedemo"]
    rec = recall([], target, {"visual_user"})
    assert rec == {"recall": None, "found": [], "missed": []}


def test_detector_one_candidate_per_new_error_and_ignores_favicon() -> None:
    from ai_qa_agents.agents.persona_testing.agent import _detect
    from ai_qa_agents.agents.persona_testing.models import MissionRun

    personas = load_personas()
    run = MissionRun(
        mission=Mission(id="m", persona="power_user", user="u", goal="g"),
        persona=personas["power_user"],
    )
    last = Step(1, "click", url_after="https://shop.test/checkout.html", duration_ms=100)
    seen: set[str] = set()
    _detect(run, run.persona, ["Failed to load resource: /favicon.ico 404"], last, seen)
    assert run.candidates == []
    _detect(run, run.persona, ["favicon.ico 404", "checkout failed"], last, seen)
    _detect(run, run.persona, ["favicon.ico 404", "checkout failed", "checkout failed"], last, seen)
    assert len(run.candidates) == 1
    issue = run.candidates[0].issue
    assert issue.check.expected == "checkout failed"
    assert issue.title.startswith("Console error on /checkout.html")

    slow = Step(2, "click", url_after="https://shop.test/", duration_ms=9000)
    _detect(run, run.persona, [], slow, seen)
    assert run.candidates[-1].issue.check.kind == "slow_step"
