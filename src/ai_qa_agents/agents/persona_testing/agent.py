"""The persona testing loop: explore, suspect, replay, report.

1. Explore: the model plays a persona toward a goal, one action per turn.
2. Suspect: a candidate bug comes from the model (with a check that code
   confirmed on the live page) or from a deterministic detector (console
   errors, slow steps).
3. Replay: the recorded steps run again as a plain script in a fresh browser,
   with video and trace. The check is evaluated again.
4. Report: only candidates confirmed by the replay become findings.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser

from ai_qa_agents.core import Evidence, Finding, Severity, Target
from ai_qa_agents.core.llm import LLMClient

from .brain import decide
from .checks import CheckResult, evaluate
from .driver import PlaywrightSession
from .models import Candidate, Check, Issue, Mission, MissionRun, Persona, Step

AGENT = "persona_testing"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "issue"


def _path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


# -- exploring ---------------------------------------------------------------


def explore(
    mission: Mission,
    persona: Persona,
    target: Target,
    client: LLMClient,
    browser: Browser,
) -> MissionRun:
    run = MissionRun(mission=mission, persona=persona)
    session = PlaywrightSession(browser, persona)
    notes: list[str] = []
    reported: list[str] = []
    seen_errors = 0
    credentials = target.credentials.get(mission.user, "")
    try:
        run.steps.append(session.execute(Step(index=0, type="goto", value=target.base_url)))
        for _ in range(mission.max_steps):
            last = run.steps[-1]
            seen_errors = _detect(run, persona, session.console_errors(), last, seen_errors)
            if last.error:
                notes.append(f"step {last.index} failed: {last.error}")

            obs = session.observe()
            decision = decide(
                client, mission, persona, credentials, obs, run.steps, notes, reported
            )

            if decision.issue is not None:
                result = evaluate(decision.issue.check, session, run.steps)
                if result.confirmed:
                    run.candidates.append(Candidate("model", len(run.steps), decision.issue))
                    reported.append(decision.issue.title)
                else:
                    notes.append(
                        f"issue {decision.issue.title!r} not reported: its check "
                        f"did not confirm ({result.detail})"
                    )

            action = decision.action
            if action.type in ("done", "give_up"):
                run.ended = action.type
                break
            step = Step(index=len(run.steps), type=action.type, value=action.value)
            if action.type != "goto" and not (action.type == "press" and not action.ref):
                element = obs.element(action.ref)
                if element is None:
                    notes.append(f"ref {action.ref!r} is not on the page; use a listed ref")
                    continue
                step.selector, step.label = element.selector, element.label
            run.steps.append(session.execute(step))
        else:
            run.ended = "max_steps"
        _detect(run, persona, session.console_errors(), run.steps[-1], seen_errors)
    finally:
        session.close()
    return run


def _detect(
    run: MissionRun, persona: Persona, errors: list[str], last: Step, seen_errors: int
) -> int:
    """Deterministic detectors that need no model. Returns errors seen so far."""
    if len(errors) > seen_errors and seen_errors == 0:
        run.candidates.append(
            Candidate(
                "detector",
                len(run.steps),
                Issue(
                    title=f"Console errors on {_path(last.url_after)}",
                    severity=Severity.MEDIUM,
                    expected="No errors in the browser console",
                    actual=errors[0][:200],
                    check=Check(kind="console_error"),
                ),
            )
        )
    if last.type != "goto" and last.duration_ms > persona.slow_step_ms and not last.error:
        run.candidates.append(
            Candidate(
                "detector",
                len(run.steps),
                Issue(
                    title=f"Slow step: {last.describe()}",
                    severity=Severity.MEDIUM,
                    expected=f"Step completes within {persona.slow_step_ms} ms",
                    actual=f"Took {last.duration_ms} ms",
                    check=Check(kind="slow_step", step=last.index, ms=persona.slow_step_ms),
                ),
            )
        )
    return len(errors)


# -- replaying ---------------------------------------------------------------


@dataclass
class ReplayResult:
    result: CheckResult
    steps: list[Step]
    video: Path | None = None
    trace: Path | None = None
    screenshot: Path | None = None


def replay(
    run: MissionRun, candidate: Candidate, browser: Browser, evidence_dir: Path
) -> ReplayResult:
    """Re-run the steps as a script in a fresh browser and evaluate the check again."""
    session = PlaywrightSession(browser, run.persona, record_dir=evidence_dir, trace=True)
    steps: list[Step] = []
    screenshot: Path | None = None
    try:
        for original in run.steps[: candidate.step_index]:
            copy = Step(
                index=original.index,
                type=original.type,
                selector=original.selector,
                label=original.label,
                value=original.value,
            )
            steps.append(session.execute(copy))
        result = evaluate(candidate.issue.check, session, steps)
        if result.confirmed:
            screenshot = evidence_dir / "screenshot.png"
            session.screenshot(screenshot)
    finally:
        trace = evidence_dir / "trace.zip"
        video = session.close(trace_path=trace)
    if video is not None and video.exists():
        final = evidence_dir / "video.webm"
        video.replace(final)
        video = final
    return ReplayResult(result, steps, video, trace, screenshot)


# -- reporting ---------------------------------------------------------------


def to_finding(
    run: MissionRun, candidate: Candidate, rep: ReplayResult, target: Target, root: Path
) -> Finding:
    issue = candidate.issue
    location = _path(rep.steps[-1].url_after) if rep.steps else "/"
    evidence = [
        Evidence(
            kind=kind, path=str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        )
        for kind, path in (
            ("video", rep.video),
            ("trace", rep.trace),
            ("screenshot", rep.screenshot),
        )
        if path is not None and path.exists()
    ]
    steps = [s.describe() for s in rep.steps]
    steps.append(f"Check ({issue.check.kind}): {rep.result.detail}")
    return Finding(
        agent=AGENT,
        target=target.name,
        title=issue.title,
        severity=issue.severity,
        summary=f"{run.persona.name} as {run.mission.user}, mission {run.mission.id!r}.",
        steps=steps,
        expected=issue.expected,
        actual=issue.actual,
        location=location,
        evidence=evidence,
        reproduced=True,  # set only here, after the deterministic replay confirmed it
        tags={
            "persona": run.persona.id,
            "user": run.mission.user,
            "mission": run.mission.id,
            "check_kind": issue.check.kind,
            "source": candidate.source,
        },
    )


def recall(findings: list[Finding], target: Target, users: set[str]) -> dict[str, object]:
    """Known bugs found, for the test users the missions covered.

    Bugs whose signal is not a persona check (e.g. "visual") are out of scope.
    """
    from .models import CHECK_KINDS

    in_scope = [b for b in target.known_bugs if b.user in users and b.signal in CHECK_KINDS]
    found = sorted(
        b.id
        for b in in_scope
        if any(
            f.tags.get("user") == b.user and f.tags.get("check_kind") == b.signal for f in findings
        )
    )
    missed = sorted(b.id for b in in_scope if b.id not in found)
    value = round(len(found) / len(in_scope), 3) if in_scope else None
    return {"recall": value, "found": found, "missed": missed}


def write_summary(
    out_dir: Path,
    runs: list[MissionRun],
    findings: list[Finding],
    unconfirmed: list[dict[str, object]],
    rec: dict[str, object],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "missions": [
            {
                "id": r.mission.id,
                "persona": r.persona.id,
                "user": r.mission.user,
                "steps": len(r.steps),
                "ended": r.ended,
                "candidates": len(r.candidates),
                "step_log": [asdict(s) for s in r.steps],
            }
            for r in runs
        ],
        "findings": len(findings),
        "unconfirmed": unconfirmed,
        "recall": rec,
    }
    (out_dir / "summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    lines = ["# persona_testing run", ""]
    lines.append("| Mission | Persona | User | Steps | Ended | Candidates |")
    lines.append("|---|---|---|---|---|---|")
    for r in runs:
        lines.append(
            f"| {r.mission.id} | {r.persona.id} | {r.mission.user} | {len(r.steps)} "
            f"| {r.ended} | {len(r.candidates)} |"
        )
    lines += ["", f"**Findings (replay-confirmed):** {len(findings)}", ""]
    lines += [f"- [{f.severity.value}] {f.title} — `{f.tags.get('mission')}`" for f in findings]
    lines += ["", f"**Not confirmed by replay:** {len(unconfirmed)}", ""]
    lines += [f"- {u['title']} — {u['detail']}" for u in unconfirmed]
    lines += [
        "",
        f"**Recall vs known bugs:** {rec['recall']} — found {rec['found']}, missed {rec['missed']}",
        "",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_missions(
    missions: list[Mission],
    personas: dict[str, Persona],
    target: Target,
    client: LLMClient,
    browser: Browser,
    out_dir: Path,
    root: Path | None = None,
) -> tuple[list[Finding], list[MissionRun]]:
    root = root or out_dir
    findings: list[Finding] = []
    runs: list[MissionRun] = []
    unconfirmed: list[dict[str, object]] = []
    for mission in missions:
        persona = personas[mission.persona]
        run = explore(mission, persona, target, client, browser)
        runs.append(run)
        for n, candidate in enumerate(run.candidates):
            evidence_dir = out_dir / mission.id / f"{n:02d}-{_slug(candidate.issue.title)}"
            rep = replay(run, candidate, browser, evidence_dir)
            if rep.result.confirmed:
                findings.append(to_finding(run, candidate, rep, target, root))
            else:
                unconfirmed.append(
                    {
                        "mission": mission.id,
                        "title": candidate.issue.title,
                        "source": candidate.source,
                        "check": candidate.issue.check.model_dump(),
                        "detail": rep.result.detail,
                    }
                )
    rec = recall(findings, target, {m.user for m in missions})
    write_summary(out_dir, runs, findings, unconfirmed, rec)
    return findings, runs
