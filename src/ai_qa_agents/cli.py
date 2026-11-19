"""``uv run agent`` — list, check and run agents.

uv run agent list                    # registered agents and targets
uv run agent doctor                  # which LLM providers are configured
uv run agent run <agent> <target>    # run one agent against one target
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_qa_agents.agents import REGISTRY
from ai_qa_agents.core import (
    ReviewStatus,
    dedupe,
    load_findings,
    load_targets,
    merge,
    save_findings,
    set_status,
)
from ai_qa_agents.core.llm import AllProvidersFailedError, LLMClient, LLMRequest
from ai_qa_agents.core.report import build_site


def _root() -> Path:
    return Path.cwd()


def cmd_list(_: argparse.Namespace) -> int:
    print("agents:", ", ".join(sorted(REGISTRY)) or "(none yet)")
    targets = load_targets(_root() / "targets")
    for t in targets.values():
        where = t.base_url or t.app_id
        print(f"target: {t.name:<20} {t.kind.value:<8} {where}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    client = LLMClient.from_env(_root())
    print(f"LLM_MODE={client.mode}")
    any_ready = False
    for p in client.providers:
        ready = p.ping()
        any_ready |= ready
        print(f"  {p.name:<10} {p.model:<28} {'ready' if ready else 'not ready'}")
    if not any_ready and client.mode != "replay":
        print("no provider configured: set GEMINI_API_KEY or GROQ_API_KEY, or run Ollama")
        return 1
    return 0


def cmd_ping(_: argparse.Namespace) -> int:
    client = LLMClient.from_env(_root())
    client.mode = "live"
    try:
        resp = client.complete(
            LLMRequest(prompt="Reply with the single word: pong", max_tokens=10, task="ping.smoke")
        )
    except AllProvidersFailedError as exc:
        print(exc)
        return 1
    print(
        f"{resp.provider}/{resp.model}: {resp.text.strip()!r} "
        f"({resp.input_tokens}+{resp.output_tokens} tokens, {resp.latency_s}s)"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.agent not in REGISTRY:
        print(f"unknown agent {args.agent!r}; available: {sorted(REGISTRY) or 'none yet'}")
        return 2
    targets = load_targets(_root() / "targets")
    if args.target not in targets:
        print(f"unknown target {args.target!r}; available: {sorted(targets)}")
        return 2
    out_dir = _root() / "runs" / args.agent / args.target
    out_dir.mkdir(parents=True, exist_ok=True)
    findings = dedupe(
        REGISTRY[args.agent](targets[args.target], LLMClient.from_env(_root()), out_dir)
    )
    save_findings(findings, out_dir / "findings.json")
    print(f"{len(findings)} finding(s) -> {out_dir / 'findings.json'}")
    return 0


def _is_demo(run_dir: Path) -> bool:
    summary = run_dir / "summary.json"
    if not summary.exists():
        return False
    meta = json.loads(summary.read_text(encoding="utf-8")).get("meta", {})
    return bool(meta.get("demo"))


def cmd_collect(args: argparse.Namespace) -> int:
    record = _root() / "findings" / f"{args.agent}.json"
    existing = load_findings(record)
    incoming = []
    for run_findings in sorted((_root() / "runs" / args.agent).glob("*/findings.json")):
        if _is_demo(run_findings.parent):
            continue  # fixture runs with a scripted model never enter the record
        incoming += load_findings(run_findings)
    result = merge(existing, incoming)
    save_findings(result.findings, record)
    lines = [
        f"## {args.agent}: {len(result.new)} new, {len(result.seen_again)} seen again",
        "",
    ]
    if result.new:
        lines += ["| Fingerprint | Severity | Title | Mission | User |", "|---|---|---|---|---|"]
        lines += [
            f"| `{f.fingerprint}` | {f.severity.value} | {f.title} "
            f"| {f.tags.get('mission', '')} | {f.tags.get('user', '')} |"
            for f in result.new
        ]
        lines += [
            "",
            "Review each with its video/trace in the run artifact, then:",
            f"`uv run agent review {args.agent} <fingerprint> confirmed|false_positive "
            '--note "..."`',
        ]
    body = "\n".join(lines) + "\n"
    if args.summary:
        Path(args.summary).write_text(body, encoding="utf-8")
    print(body, end="")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    record = _root() / "findings" / f"{args.agent}.json"
    findings = load_findings(record)
    try:
        updated = set_status(findings, args.fingerprint, ReviewStatus(args.status), args.note)
    except KeyError as exc:
        print(exc.args[0])
        return 2
    save_findings(findings, record)
    print(f"{updated.fingerprint} -> {updated.status.value}: {updated.title}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    index = build_site(_root(), Path(args.out))
    print(f"report -> {index}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    run = sub.add_parser("run")
    run.add_argument("agent")
    run.add_argument("target")
    run.set_defaults(fn=cmd_run)
    collect = sub.add_parser("collect")
    collect.add_argument("agent")
    collect.add_argument("--summary", help="also write the markdown summary to this file")
    collect.set_defaults(fn=cmd_collect)
    review = sub.add_parser("review")
    review.add_argument("agent")
    review.add_argument("fingerprint", help="full fingerprint or a unique prefix")
    review.add_argument("status", choices=[s.value for s in ReviewStatus])
    review.add_argument("--note", default="")
    review.set_defaults(fn=cmd_review)
    report = sub.add_parser("report")
    report.add_argument("--out", default="site")
    report.set_defaults(fn=cmd_report)
    args = parser.parse_args(argv)
    code: int = args.fn(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
