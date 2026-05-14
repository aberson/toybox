# Activity-quality eval fixtures

Canonical rubric definition: [`src/toybox/ai/rubric.py`](../../../src/toybox/ai/rubric.py). This README is the source-of-truth for the fixture set itself (layout, distribution, baseline workflow). The historical `documentation/eval-fixtures.md` was retired 2026-05-10 — its content was duplicative of `rubric.py` (rubric anchors) and this README (operational workflow).

## Layout

```
prompts.jsonl         # 20 fixture inputs, one JSON object per line
baseline_scores.json  # most-recent committed baseline judge scores per fixture
holdout.json          # ids of the 5 held-out fixtures used by CI regression
README.md             # this file
```

## Distribution check

| Axis | Targets met |
|---|---|
| Ages | 3 (4 fx), 5 (5 fx), 7 (5 fx), 9 (5 fx) — ≥3 each |
| Personas | mr_unicorn (6), captain_banana (5), detective_fox (5), neutral (4) — ≥3 each |
| Triggers | boredom_explicit (4), implicit_lull (4), excitement_spike (4), conflict (4), ambiguous_mumble (4) — ≥2 each |
| Rooms | living_room (7), bedroom (6), kitchen (4), multi_room (3) — ≥2 each |
| Edge cases | tired_kid (2), sparse_inventory (3), persona_age_mismatch (2), anti_signal (2), ambiguous_trigger (2) — ≥1 each |

## Baseline status

`baseline_scores.json` ships as a **placeholder** — every fixture is flagged
`"placeholder": true` with synthetic 4-out-of-5 scores. The CI gate
(`toybox.ai.eval_run.evaluate_regression`) treats an all-placeholder baseline
as "no baseline available yet" and SKIPS the regression check rather than
failing the build.

This lets the eval scaffold ship before live Claude OAuth is wired in CI.
**An operator with working Claude OAuth must refresh the baseline before
the regression check becomes meaningful** — see "Refresh baseline" below.

## Refresh baseline (operator)

When the live Claude judge is reachable in your environment:

```powershell
uv run python -m toybox.ai.eval_run `
  --fixtures tests/fixtures/eval/prompts.jsonl `
  --judge claude `
  --mode baseline `
  --out tests/fixtures/eval/baseline_scores.json
```

Then commit:

```powershell
git add tests/fixtures/eval/baseline_scores.json
git commit -m "eval: refresh baseline scores after <reason>"
```

**When to refresh**: after a deliberate prompt or rubric change with documented rationale.
**Never** refresh "to make the build pass" after a regression — investigate the regression instead.

## CI regression run (automated)

```powershell
uv run python -m toybox.ai.eval_run --mode ci
```

Exits 0 on pass / skip-because-placeholder, exits 1 on regression. The check fails when:

- mean dimension score (across all 5 holdout fixtures × 6 dimensions) drops more than 0.5 from baseline, OR
- any holdout fixture's safety dimension auto-fails (score == 1), OR
- any holdout fixture's `expected_floor` is violated.

## Add a new fixture

1. Append a JSON line to `prompts.jsonl` with a unique id (next `f0NN`).
2. Run a single-fixture pass to confirm it generates cleanly:
   ```powershell
   uv run python -m toybox.ai.eval_run --fixtures-only fNNN --mode baseline --judge stub
   ```
3. If the fixture is a useful regression check, add it to `holdout.json` and refresh the baseline.
4. Update the distribution-check table above if the new fixture covers a previously-missing axis combination.

## What this fixture set does NOT cover

- Real-world transcript noise (faster-whisper errors, partial captures) — covered by Phase B integration tests.
- Concurrent state (multiple kids, mid-activity triggers) — out of v1 scope.
- Very long context (multi-day pattern learning) — Phase E or later.
- Voice tone / engagement signals — only observable post-hoc via parent feedback (`parent_signal` in `labeled_events`).

These are intentional gaps. If Phase E A/B testing needs them, expand the matrix then.
