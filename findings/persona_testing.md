# persona_testing — findings

Simulated users pursue a goal on saucedemo. A suspected bug becomes a finding only when a scripted replay in a fresh browser shows it again, checked by code.

> **Status: no live run yet.** The agent is verified end to end on a local shop with four planted bugs (`tests/fixtures/bugshop/`): all four found and replay-confirmed, a hallucinated issue dropped, recall 0.8 (the planted slow step is not exercised by that script). The first saucedemo results land here after the first nightly run with free-tier keys.

## How it works

1. **Explore.** Each mission pairs a persona (`data/personas.yaml`) with a saucedemo test user and a goal (`data/missions/saucedemo.yaml`). Each turn the model sees the page (interactive elements with replayable selectors, images, visible text, recent steps) and returns one action, plus at most one suspected issue.
2. **Suspect.** A model issue must carry a deterministic check (`not_sorted`, `attr_all_equal`, `input_value_differs`, `visible`, `url_lacks`, `text_lacks`, `console_error`, `slow_step`). Code evaluates it on the live page; if it does not confirm, the issue is dropped and the model is told why. Two detectors need no model: console errors and steps slower than the persona's limit.
3. **Replay.** The recorded steps run again as a plain script in a new browser context with video and a Playwright trace. The same check is evaluated again.
4. **Report.** Only replay-confirmed candidates become `Finding`s (`reproduced=True`, status `draft`). The rest go to `summary.json` as `unconfirmed`, so the false-positive rate is visible.

The keyboard-only persona activates controls with focus + Enter; the slow-3G persona runs with Chrome DevTools network throttling.

## Answer key

`targets/saucedemo.yaml` lists saucedemo's deliberate bugs with the test user and the check kind that should catch each. A finding counts toward recall when both match. `standard_user` missions are the control: findings there are likely false positives.

| Known bug | User | Expected signal | Found? |
|---|---|---|---|
| Same image on every product | problem_user | `attr_all_equal` | pending first run |
| Sorting has no effect | problem_user | `not_sorted` | pending first run |
| Last name field drops input | problem_user | `input_value_differs` | pending first run |
| Checkout cannot finish | error_user | `url_lacks` | pending first run |
| Slow login | performance_glitch_user | `slow_step` | pending first run |
| Visual misalignment | visual_user | out of scope (design_tokens) | — |

## Results

| Run | Missions | Findings | Confirmed by review | False positives | Recall | Live LLM calls | Cost |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — |

## Known limitations

- The model reads the page as text and element lists, not pixels, so purely visual bugs are out of scope.
- `slow_step` depends on network timing; a slow CI runner can produce one-off slow steps. The replay check reduces this but does not remove it.
- Selectors come from `data-test`, `id` or `name` when present; on pages without them the fallback CSS path is less stable.
