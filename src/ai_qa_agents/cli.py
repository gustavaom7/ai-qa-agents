"""``uv run agent`` — list, check and run agents.

uv run agent list                    # registered agents and targets
uv run agent doctor                  # which LLM providers are configured
uv run agent run <agent> <target>    # run one agent against one target
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_qa_agents.agents import REGISTRY
from ai_qa_agents.core import dedupe, load_targets, save_findings
from ai_qa_agents.core.llm import AllProvidersFailedError, LLMClient, LLMRequest


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
    args = parser.parse_args(argv)
    code: int = args.fn(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
