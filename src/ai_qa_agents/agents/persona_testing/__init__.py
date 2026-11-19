"""persona_testing (#28): simulated users explore the app; replay-confirmed bugs only.

Environment:
    PERSONA_MISSIONS   comma-separated mission ids to run (default: all for the target)
    PW_CHANNEL         browser channel, e.g. "chrome" for a local Chrome (default: bundled Chromium)
    PW_HEADED          set to 1 to watch the browser
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from ai_qa_agents.agents import register
from ai_qa_agents.core import Finding, Target
from ai_qa_agents.core.llm import LLMClient

from .agent import run_missions
from .driver import launch
from .models import load_missions, load_personas


@register("persona_testing")
def run(target: Target, client: LLMClient, out_dir: Path) -> list[Finding]:
    missions = load_missions(target.name)
    wanted = {m.strip() for m in os.getenv("PERSONA_MISSIONS", "").split(",") if m.strip()}
    if wanted:
        missions = [m for m in missions if m.id in wanted]
    if not missions:
        print(f"persona_testing: no missions for target {target.name!r}")
        return []
    with sync_playwright() as pw:
        browser = launch(pw)
        try:
            findings, _ = run_missions(
                missions, load_personas(), target, client, browser, out_dir, root=Path.cwd()
            )
        finally:
            browser.close()
    return findings
