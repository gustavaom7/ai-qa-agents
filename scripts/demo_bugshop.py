"""Demo run of persona_testing on the local bug shop, with a scripted model.

Shows the whole pipeline (explore, live check, replay with video/trace,
findings, recall) without network or LLM keys. The run is marked as a demo:
the report labels it and ``agent collect`` never merges it into the record.

    uv run python scripts/demo_bugshop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_qa_agents.agents.persona_testing.agent import run_missions  # noqa: E402
from ai_qa_agents.agents.persona_testing.driver import launch  # noqa: E402
from ai_qa_agents.agents.persona_testing.models import Mission, load_personas  # noqa: E402
from ai_qa_agents.core import save_findings  # noqa: E402
from tests.agents.conftest import scripted_client, serve  # noqa: E402
from tests.agents.test_persona_e2e import SCRIPT, SHOP, TARGET  # noqa: E402

PORT = 8765


def main() -> int:
    out_dir = ROOT / "runs" / "persona_testing" / "bugshop-demo"
    client, _ = scripted_client(SCRIPT)
    server = serve(SHOP, PORT)
    target = TARGET.model_copy(update={"base_url": f"http://127.0.0.1:{PORT}/index.html"})
    mission = Mission(
        id="sort-and-buy",
        persona="power_user",
        user="bug_user",
        goal="Sort by price, add a product and check out",
    )
    with sync_playwright() as pw:
        browser = launch(pw)
        try:
            findings, _ = run_missions(
                [mission], load_personas(), target, client, browser, out_dir, root=ROOT, demo=True
            )
        finally:
            browser.close()
            server.shutdown()
    save_findings(findings, out_dir / "findings.json")
    print(f"demo: {len(findings)} finding(s) -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
