from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_qa_agents.core import (
    Evidence,
    Finding,
    ReviewStatus,
    Severity,
    Target,
    dedupe,
    load_findings,
    load_targets,
    precision,
    save_findings,
)

ROOT = Path(__file__).resolve().parents[2]


def finding(title: str = "Sort has no effect", **kw: object) -> Finding:
    base: dict[str, object] = {
        "agent": "persona_testing",
        "target": "saucedemo",
        "title": title,
        "severity": Severity.HIGH,
        "summary": "Choosing Price (low to high) keeps the original order.",
        "location": "/inventory.html",
    }
    base.update(kw)
    return Finding.model_validate(base)


def test_fingerprint_ignores_case_and_punctuation() -> None:
    assert finding("Sort has no effect").fingerprint == finding("sort  has no EFFECT!").fingerprint
    assert finding("Sort has no effect").fingerprint != finding("Images are wrong").fingerprint
    assert finding(location="/a").fingerprint != finding(location="/b").fingerprint, (
        "same title on another page is another finding"
    )


def test_dedupe_keeps_first_in_order() -> None:
    first = finding(summary="first")
    result = dedupe([first, finding("Images wrong"), finding(summary="dup")])
    assert result[0].summary == "first"
    assert len(result) == 2


def test_precision_counts_only_reviewed() -> None:
    assert precision([finding()]) is None
    reviewed = [
        finding("a", status=ReviewStatus.CONFIRMED),
        finding("b", status=ReviewStatus.CONFIRMED),
        finding("c", status=ReviewStatus.FALSE_POSITIVE),
        finding("d"),
    ]
    assert precision(reviewed) == pytest.approx(0.667)


def test_new_finding_is_an_unreproduced_draft() -> None:
    f = finding()
    assert f.status == ReviewStatus.DRAFT
    assert f.reproduced is False


def test_save_load_roundtrip(tmp_path: Path) -> None:
    original = [finding(evidence=[Evidence(kind="video", path="runs/x.webm")])]
    path = tmp_path / "findings.json"
    save_findings(original, path)
    loaded = load_findings(path)
    assert loaded == original
    assert loaded[0].fingerprint == original[0].fingerprint
    assert load_findings(tmp_path / "missing.json") == []


def test_repo_targets_load() -> None:
    targets = load_targets(ROOT / "targets")
    assert set(targets) == {"saucedemo", "todomvc", "wikipedia-android"}
    assert targets["saucedemo"].known_bugs, "saucedemo is the recall answer key"


def test_target_needs_locator() -> None:
    with pytest.raises(ValidationError, match="base_url"):
        Target.model_validate({"name": "x", "kind": "web"})
    with pytest.raises(ValidationError, match="app_id"):
        Target.model_validate({"name": "y", "kind": "android"})


def test_target_name_must_match_file(tmp_path: Path) -> None:
    (tmp_path / "foo.yaml").write_text("name: bar\nkind: web\nbase_url: https://x.test\n")
    with pytest.raises(ValueError, match="must match"):
        load_targets(tmp_path)
