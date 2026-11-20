from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_qa_agents.cli import main
from ai_qa_agents.core import (
    Evidence,
    Finding,
    ReviewStatus,
    Severity,
    load_findings,
    merge,
    save_findings,
    set_status,
)
from ai_qa_agents.core.report import build_site


def finding(title: str, **kw: object) -> Finding:
    base: dict[str, object] = {
        "agent": "persona_testing",
        "target": "saucedemo",
        "title": title,
        "severity": Severity.HIGH,
        "summary": "s",
        "location": "/inventory.html",
        "tags": {"mission": "m1", "user": "problem_user", "check_kind": "not_sorted"},
    }
    base.update(kw)
    return Finding.model_validate(base)


# -- review -------------------------------------------------------------------


def test_merge_keeps_review_and_adds_new_as_draft() -> None:
    reviewed = finding("Sort broken", status=ReviewStatus.CONFIRMED, review_note="real")
    incoming = [
        finding("sort BROKEN", evidence=[Evidence(kind="video", path="runs/v.webm")]),
        finding("Images wrong", status=ReviewStatus.CONFIRMED),  # status from a run is ignored
    ]
    result = merge([reviewed], incoming)
    assert [f.title for f in result.findings] == ["Sort broken", "Images wrong"]
    kept = result.findings[0]
    assert kept.status == ReviewStatus.CONFIRMED
    assert kept.review_note == "real"
    assert kept.evidence[0].path == "runs/v.webm"
    assert kept.last_seen is not None
    assert result.findings[1].status == ReviewStatus.DRAFT
    assert len(result.new) == 1
    assert len(result.seen_again) == 1


def test_merge_never_drops_findings() -> None:
    old = [finding("A"), finding("B")]
    assert len(merge(old, []).findings) == 2


def test_set_status_by_prefix() -> None:
    fs = [finding("A"), finding("B")]
    updated = set_status(fs, fs[1].fingerprint[:6], ReviewStatus.FALSE_POSITIVE, "noise")
    assert fs[1].status == ReviewStatus.FALSE_POSITIVE
    assert updated.review_note == "noise"
    with pytest.raises(KeyError):
        set_status(fs, "zzzz", ReviewStatus.CONFIRMED)
    with pytest.raises(KeyError, match="ambiguous"):
        set_status(fs, "", ReviewStatus.CONFIRMED)


# -- fixtures for report and CLI ------------------------------------------------


def write_run(root: Path, target: str, findings: list[Finding], demo: bool = False) -> Path:
    run_dir = root / "runs" / "persona_testing" / target
    run_dir.mkdir(parents=True, exist_ok=True)
    save_findings(findings, run_dir / "findings.json")
    summary = {
        "meta": {
            "agent": "persona_testing",
            "target": target,
            "finished_at": "2026-11-19T06:30:00+00:00",
            "providers": ["gemini", "groq"],
            "demo": demo,
        },
        "missions": [
            {"id": "m1", "persona": "power_user", "user": "problem_user", "steps": 9,
             "ended": "done", "candidates": 2}
        ],
        "findings": len(findings),
        "unconfirmed": [
            {"mission": "m1", "title": "Ghost bug", "source": "model", "check": {},
             "detail": "not seen on replay"}
        ],
        "recall": {
            "recall": 0.5, "found": ["problem-user-sort"], "missed": ["problem-user-images"]
        },
    }  # fmt: skip
    (run_dir / "summary.json").write_text(json.dumps(summary))
    return run_dir


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shot = tmp_path / "runs/persona_testing/saucedemo/m1/00-sort/screenshot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fake")
    confirmed = finding("Sort broken", status=ReviewStatus.CONFIRMED, review_note="seen it")
    fp = finding("Ghost image", status=ReviewStatus.FALSE_POSITIVE)
    save_findings([confirmed, fp], tmp_path / "findings" / "persona_testing.json")
    rel, gone = str(shot.relative_to(tmp_path)), "runs/gone.webm"
    write_run(
        tmp_path,
        "saucedemo",
        [
            finding("Sort broken", evidence=[Evidence(kind="screenshot", path=rel)]),
            finding("<script>alert(1)</script> in title", location="/cart.html"),
            finding("Old video", location="/x", evidence=[Evidence(kind="video", path=gone)]),
        ],
    )  # fmt: skip
    write_run(tmp_path, "bugshop-demo", [finding("Demo bug", target="bugshop")], demo=True)
    usage = tmp_path / "runs" / "usage.jsonl"
    usage.write_text(
        json.dumps(
            {"ts": "x", "task": "persona_testing.decide", "provider": "gemini", "model": "g",
             "input_tokens": 100, "output_tokens": 10, "latency_s": 1, "cost_usd": 0.0,
             "from_cassette": False}
        ) + "\n"
    )  # fmt: skip
    return tmp_path


# -- report -------------------------------------------------------------------


def text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_report_numbers_status_and_demo_label(repo: Path) -> None:
    index = build_site(repo, repo / "site", datetime(2026, 11, 19, 7, 0, tzinfo=UTC))
    html = index.read_text()
    text = text_of(html)
    assert "Findings 2 in the reviewed record" in text
    assert "Confirmed bugs 1 1 false positive(s)" in text
    assert "Precision 50%" in text
    assert "Recall 50%" in text  # demo run excluded from the mean
    assert "1 live call(s)" in text
    assert "Demo · local fixture, scripted model" in text
    assert "Ghost bug" in text and "not seen on replay" in text
    assert "No live run yet" not in text
    # A run finding already reviewed shows the record's status, and its note.
    assert "Bugs confirmed" in text
    assert "seen it" in text
    assert "<title>1 to review · 1 confirmed" not in html  # drafts in runs are not in the record
    assert "<title>0 to review · 1 confirmed — ai-qa-agents report</title>" in html


def test_report_is_self_contained_and_escaped(repo: Path) -> None:
    html = build_site(repo, repo / "site").read_text()
    assert not re.search(r'(src|href)="https?://', html), "no external requests"
    assert "<link" not in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.index("<script>") < html.index("<style"), "theme resolved before paint"
    assert 'id="tema"' in html


def test_report_copies_evidence_and_flags_missing(repo: Path) -> None:
    site = repo / "site"
    html = build_site(repo, site).read_text()
    shot = "runs/persona_testing/saucedemo/m1/00-sort/screenshot.png"
    assert (site / shot).read_bytes() == b"\x89PNG fake"
    assert f'src="{shot}"' in html
    assert "Evidence not kept" in html  # the video file does not exist
    assert (site / ".nojekyll").exists()


def test_report_on_empty_repo(tmp_path: Path) -> None:
    html = build_site(tmp_path, tmp_path / "site").read_text()
    assert "No live run yet" in html
    assert "No runs yet." in html


# -- CLI ------------------------------------------------------------------------


def test_cli_collect_skips_demo_and_writes_summary(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert main(["collect", "persona_testing", "--summary", "pr.md"]) == 0
    record = load_findings(repo / "findings" / "persona_testing.json")
    titles = [f.title for f in record]
    assert "Demo bug" not in titles
    assert titles.count("Sort broken") == 1
    assert len(record) == 4  # 2 reviewed + 2 new from the saucedemo run
    body = (repo / "pr.md").read_text()
    assert "2 new, 1 seen again" in body
    assert "uv run agent review persona_testing" in body


def test_cli_review_and_report(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    fp = load_findings(repo / "findings" / "persona_testing.json")[1].fingerprint
    assert main(["review", "persona_testing", fp[:8], "confirmed", "--note", "ok"]) == 0
    assert load_findings(repo / "findings" / "persona_testing.json")[1].status == "confirmed"
    assert main(["review", "persona_testing", "nope", "confirmed"]) == 2
    assert main(["report", "--out", "out"]) == 0
    assert (repo / "out" / "index.html").exists()


# -- history and fonts -------------------------------------------------------------


def test_history_accumulates_without_duplicates(repo: Path) -> None:
    site = repo / "site"
    build_site(repo, site)
    first = json.loads((site / "history.json").read_text())
    assert {(e["target"], e["demo"]) for e in first} == {
        ("saucedemo", False),
        ("bugshop-demo", True),
    }
    old = {
        "agent": "persona_testing", "target": "saucedemo",
        "finished_at": "2026-11-01T06:30:00+00:00",
        "findings": 1, "status": "No findings", "status_class": "st-ok",
    }  # fmt: skip
    previous = repo / "previous.json"
    previous.write_text(json.dumps([old, *first]))
    html = build_site(repo, site, history_path=previous).read_text()
    again = json.loads((site / "history.json").read_text())
    assert len(again) == 3, "same runs again are not duplicated"
    assert again[-1]["finished_at"] == old["finished_at"], "newest first"
    assert "Run history" in html
    assert 'class="quadro st-ok"' in html


def test_history_ignores_a_broken_previous_file(repo: Path) -> None:
    previous = repo / "previous.json"
    previous.write_text("<html>404</html>")
    build_site(repo, repo / "site", history_path=previous)
    assert len(json.loads((repo / "site" / "history.json").read_text())) == 2


def test_fonts_are_embedded_and_load_offline(repo: Path, browser: object) -> None:
    from playwright.sync_api import Browser

    assert isinstance(browser, Browser)
    index = build_site(repo, repo / "site")
    html = index.read_text()
    assert "fonts.googleapis" not in html and not re.search(r"url\(['\"]?https?:", html)
    context = browser.new_context()
    external: list[str] = []

    def block(route: object) -> None:
        from playwright.sync_api import Route

        assert isinstance(route, Route)
        if route.request.url.startswith(("file:", "data:")):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    context.route("**/*", block)
    page = context.new_page()
    page.goto(index.as_uri())
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.fonts.check('13px \"IBM Plex Mono\"')")
    assert page.evaluate("document.fonts.check('600 14px Inter')")
    used = page.evaluate(
        "[...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)"
    )
    assert {"IBM Plex Mono", "Inter"} <= set(used)
    assert external == []
    context.close()
