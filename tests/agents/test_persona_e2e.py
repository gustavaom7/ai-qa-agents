"""End to end on a local shop with planted bugs: real browser, scripted model.

Proves the whole loop: explore -> live check -> replay in a fresh browser ->
finding with evidence, and that unconfirmed issues are dropped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser

from ai_qa_agents.agents.persona_testing.agent import run_missions
from ai_qa_agents.agents.persona_testing.checks import CheckResult
from ai_qa_agents.agents.persona_testing.models import Mission, load_personas
from ai_qa_agents.core import KnownBug, ReviewStatus, Target

from .conftest import FIXTURES, scripted_client

SHOP = FIXTURES / "bugshop"

TARGET = Target(
    name="bugshop",
    kind="web",
    base_url=(SHOP / "index.html").as_uri(),
    credentials={"bug_user": "secret"},
    known_bugs=[
        KnownBug(id="sort", description="", user="bug_user", signal="not_sorted"),
        KnownBug(id="images", description="", user="bug_user", signal="attr_all_equal"),
        KnownBug(id="last-name", description="", user="bug_user", signal="input_value_differs"),
        KnownBug(id="console", description="", user="bug_user", signal="console_error"),
        KnownBug(id="slow", description="", user="bug_user", signal="slow_step"),
    ],
)

SCRIPT = [
    {"type": "fill", "label": "Username", "value": "bug_user"},
    {"type": "fill", "label": "Password", "value": "secret"},
    {"type": "click", "label": "Login"},
    {"type": "select", "label": "Name (A to Z)", "value": "Price (low to high)"},
    {
        "type": "click",
        "label": "Add to cart",
        "issue": {
            "title": "Sorting by price has no effect",
            "severity": "high",
            "expected": "Products ordered by price, low to high",
            "actual": "Order unchanged: $29.99, $9.99, $7.99",
            "check": {"kind": "not_sorted", "selector": ".item_price", "by": "number"},
        },
    },
    {
        "type": "click",
        "label": "Cart",
        "issue": {
            "title": "All products show the same image",
            "severity": "medium",
            "check": {"kind": "attr_all_equal", "selector": "img.item_img", "attr": "src"},
        },
    },
    {
        "type": "fill",
        "label": "Last Name",
        "value": "Mesquita",
        "issue": {  # hallucinated: its check cannot confirm, so it must be dropped
            "title": "Cart badge missing",
            "check": {"kind": "visible", "selector": ".does-not-exist"},
        },
    },
    {
        "type": "click",
        "label": "Continue",
        "issue": {
            "title": "Last name field drops typed text",
            "severity": "high",
            "check": {
                "kind": "input_value_differs",
                "selector": '[data-test="lastName"]',
                "expected": "Mesquita",
            },
        },
    },
    {"type": "click", "label": "No such button"},  # ref not on the page: agent must recover
    {"type": "done"},
]


@pytest.mark.parametrize("transport", ["file", "http"])
def test_full_loop_on_bugshop(
    browser: Browser, tmp_path: Path, bugshop_url: str, transport: str
) -> None:
    target = TARGET if transport == "file" else TARGET.model_copy(update={"base_url": bugshop_url})
    client, provider = scripted_client(SCRIPT)
    mission = Mission(id="buy", persona="power_user", user="bug_user", goal="Buy something")
    findings, runs = run_missions(
        [mission], load_personas(), target, client, browser, tmp_path, root=tmp_path
    )

    run = runs[0]
    assert run.ended == "done"
    assert [s.type for s in run.steps] == [
        "goto", "fill", "fill", "click", "select", "click", "click", "fill", "click"
    ]  # fmt: skip
    assert not any(s.error for s in run.steps), [s.error for s in run.steps]

    kinds = {f.tags["check_kind"] for f in findings}
    assert kinds == {"not_sorted", "attr_all_equal", "input_value_differs", "console_error"}
    assert all(f.reproduced and f.status == ReviewStatus.DRAFT for f in findings)
    assert "Cart badge missing" not in {f.title for f in findings}

    sort = next(f for f in findings if f.tags["check_kind"] == "not_sorted")
    assert sort.location.endswith("inventory.html")
    assert sort.steps[-2].startswith("Select 'Price (low to high)'")
    assert "order" in sort.steps[-1]
    kinds_of_evidence = {e.kind for e in sort.evidence}
    assert {"trace", "screenshot"} <= kinds_of_evidence
    if os.getenv("EXPECT_VIDEO"):  # CI installs ffmpeg; some local Chromes cannot record
        assert "video" in kinds_of_evidence
    for e in sort.evidence:
        assert (tmp_path / e.path).exists()

    # The model was told why its unconfirmed issue was not reported.
    assert any("did not confirm" in p for p in provider.prompts)
    assert any("is not on the page" in p for p in provider.prompts)

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["findings"] == 4
    assert summary["recall"]["missed"] == ["slow"]
    assert summary["recall"]["recall"] == 0.8
    assert "Recall vs known bugs" in (tmp_path / "summary.md").read_text()


def test_candidate_not_reproduced_is_unconfirmed(
    browser: Browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that confirmed live but not on replay never becomes a finding."""
    from ai_qa_agents.agents.persona_testing import agent

    real_replay = agent.replay

    def flaky_replay(*args: Any, **kwargs: Any) -> agent.ReplayResult:
        rep = real_replay(*args, **kwargs)
        rep.result = CheckResult(valid=True, bug=False, detail="not seen on replay")
        return rep

    monkeypatch.setattr(agent, "replay", flaky_replay)
    client, _ = scripted_client(SCRIPT)
    mission = Mission(id="buy", persona="power_user", user="bug_user", goal="g")
    findings, runs = run_missions(
        [mission], load_personas(), TARGET, client, browser, tmp_path, root=tmp_path
    )
    assert len(runs[0].candidates) == 4
    assert findings == []
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert len(summary["unconfirmed"]) == 4
    assert summary["unconfirmed"][0]["detail"] == "not seen on replay"
