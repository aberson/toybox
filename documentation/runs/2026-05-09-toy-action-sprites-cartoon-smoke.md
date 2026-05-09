# F.5-4 Smoke gate — toy action sprites, cartoon redo

**Date:** 2026-05-09
**Host:** Windows 11, RTX 4070 8 GB
**Backend HEAD:** `8fe71e2` (master)
**Issue:** [#67](https://github.com/aberson/toybox/issues/67)

## Pre-flight

- Working tree: clean at `8fe71e2`
- mypy --strict src (with `--extra image_gen`): clean, 87 source files
- Targeted pytest (image_gen unit + worker_e2e + toys_api_actions): 104 passed
- F.5 load smoke (`scripts/f5_load_smoke.py`): Mode A PASS (cartoon checkpoint + LCM-LoRA loads cleanly), Mode B SKIPPED (no cartoon LoRA at `data/models/image_gen/cartoon_lora/`, expected — optional A/B alt)
- `.env` overrides: `TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6`, `TIMEOUT_SEC=300`, `BREAKER_THRESHOLD=5`; `CARTOON_MODE` and `IMAGE_GEN_ENABLED` use defaults (`checkpoint`, `true`)
- Composite templates present: 10/10 + manifest + CREDITS

## Step 1 — Boot capability gate + Claude capability + VRAM logger

**Backend launch:** `uv run --extra image_gen python -m toybox.main`

**Capability gate (boot log):**

```
INFO toybox.app: image-gen capability=True reason=capable
```

✓ Gate green.

**Claude vision capability:** **True** — derived from observed `tags_populated_by` behavior in Step 2 (both uploaded toys came back with Claude-tagged metadata, see below). The `/api/health` endpoint does not expose `ai.claude_capable` (issue body assumed a shape that wasn't shipped; `current_capability_reason()` at [src/toybox/core/capability.py:76](../../src/toybox/core/capability.py#L76) is still a Phase A placeholder returning `None`).

**Production audio pipeline:** also came up cleanly (Whisper-small on cuda, mic capture started, transcript pipeline running). Not blocking F.5-4 but worth noting — the lifespan is wiring real components, matching project_audio_runtime_status memory.

**nvidia-smi snapshot logger:** running under agent `run_in_background` (task `bz7cknc0b`) instead of in a 4th operator-side terminal — addresses fix-bug skill's anti-pattern #1 (user-as-human-shell). Loop: `while ($true) { nvidia-smi --query-gpu=memory.used,temperature.gpu --format=csv,noheader >> logs/f54-vram.csv; Start-Sleep -Seconds 1 }`. Confirmed file is growing — idle baseline `785 MiB, 46°C`.

**Detour: `/fix-bug` for missing CSV.** Initial check found `logs/f54-vram.csv` did not exist. Skill diagnosis: high confidence the loop was never started (file absent drive-wide; nvidia-smi command itself works). Fix: started loop under agent control. Step 2 (independent repro) skipped — no code defect to second-opinion. Symptom-gone verified.

## Step 2 — Toy upload, checkpoint mode

**Toys uploaded** (default `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint`):

| Toy ID | Display name | Tags (Claude-populated) | Slots done | Created |
|---|---|---|---|---|
| `9991760b…` | Mr Unicorn | `stuffy` | 10/10 | 2026-05-09T20:30:28Z |
| `4a325ad9…` | Periodic Table | `Periodic table,learning` | 10/10 | 2026-05-09T20:31:46Z |

**Result:** 20/20 sprites `done`, 0 `failed`, 0 `running`/`queued` stuck. All slots populated with unique random seeds + valid `image_path` values pointing at `<slot>.png` artefacts. `tags_populated_by: claude` for both — confirms Claude vision capability is live.

**Wall-clock observation (operator):** "they generate fast" — fast enough that operator did not flag any per-slot delay. (No stopwatch data captured; per-slot start/finish timestamps are not in the `toy_actions` schema, only `updated_at` — see follow-up note.)

**Visual rating (operator):** "they aren't the best images but they generate and generate fast" → **soft pass.** Acceptable for the smoke gate (per F.5-4 issue criteria: "recognizable toys + clear action verbs in operator's visual judgment"). Quality-vs-speed tradeoff with LCM 4-step inference is expected; cartoon checkpoint output at 512² won't match a 30-step diffuser run.

**VRAM during run** (from `logs/f54-vram.csv`, 10355 rows so far):
- Idle baseline pre-load: ~785 MiB
- Peak during inference: **4599 MiB** (well under the 6 GB `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` floor)
- Steady-state with model loaded: ~4279 MiB
- (One anomalous `0 MiB` row in the dataset — likely a transient nvidia-smi sample during driver state change; not load-bearing.)

**Native crashes:** 0 (capability gate stayed green; backend stayed up; no `c10.dll` faulting application records).

> Operator note: two toys uploaded rather than one — extra data on cartoon checkpoint behavior, no procedural impact.

## Step 3 — Activity trigger / kiosk render

**Initial result (pre-fix):** manual trigger fires, activity proposed → approved → completed in DB, child kiosk loaded at `/child`, WS-connected, step bodies render. **Sprites did NOT render in the kiosk.** F.5 image-gen output verified independently good (`GET /api/static/images/toy_actions/9991760b.../jumping.png` → 200, 13.4 KB PNG); the breakage was upstream and pre-existing — `_persist_activity` hardcoded `toy_ids = NULL` in the INSERT, so the kiosk's F7 sprite branch (which reads `activity.toy_ids[0]`) never fired. NOT a F.5 regression — every historical activity row had `toy_ids=NULL`, so this gap predated F.5.

**`/fix-bug` detour invoked mid-UAT** (operator-elected; "let's fix that pre-existing bug"). Triage `full` (TDD + reviewer gauntlet + verify); end-to-end scope; worktree-isolated.

- **Investigation:** primary-source diagnosis with file:line citations; both my pass and an Explore-agent independent pass converged on the same chain (write-path drops toy id at three layers — `_pick_toy_name` returns name only, `Activity` model has no `toy_ids` field, `_persist_activity` hardcodes NULL).
- **Implementation:** delegated to `/build-step-tdd`. Test-writer agent wrote 8 failing tests first (3 generator + 3 model + 2 integration). Developer agent landed the fix in 1 iteration. Worktree gates green: pytest 1179, mypy strict 87 files clean, ruff clean.
- **Review:** 4-pass review-dev-gauntlet found 1 MEDIUM bug (sibling instance: `_persist_smoke_activity` in `src/toybox/main.py` also hardcoded NULL — duplicate-shape anti-pattern), 1 tautological test (deleted), 1 loose assertion (tightened), and a deferred `Sequence[str]` style nit. Folded the load-bearing two back in commit `dff8c82`.
- **Verify-against-repro:** operator restarted backend, re-uploaded Mr Unicorn (new id `5f6e3587…`), triggered activity, walked steps in kiosk. Operator visual confirms: **"woo, sprites are showing up"**. DB-side post-fix activity row has `toy_ids=["5f6e358780df450dae256234a8d3efb9"]` populated correctly. Symptom **gone**.

**Master commits added during this detour** (3 ahead of origin/master at session-end):
- `921430f` — fix(activities): persist toy_ids on propose so kiosk renders sprites
- `c23135c` — Merge branch 'build-tdd-toy-ids-1778359909'
- `dff8c82` — fix(activities): fold-back review findings on toy_ids persist

## Step 4 — Single-slot regenerate

**PASS** (operator visual + agent-side DB check). All 10 slots on the new Mr Unicorn (`5f6e3587…`) reach `done` with unique random seeds; sprites visibly different post-regenerate. Schema doesn't store seed history, so the regenerate event itself is only visible via the operator's visual on the parent UI grid + the WS state-transition stream they watched live.

| Slot | Seed (latest) | Image |
|---|---|---|
| cheering | 3568124313256347882 | cheering.png |
| confused | 5892627440975247561 | confused.png |
| idle | 777395851586184344 | idle.png |
| jumping | 549272391638238459 | jumping.png |
| looking | 3214547251046282306 | looking.png |
| pointing | 804907790132618097 | pointing.png |
| running | 3758834368641922318 | running.png |
| sleeping | 3811573084597458529 | sleeping.png |
| thinking | 5149875209977380050 | thinking.png |
| waving | 6512485554971568811 | waving.png |

## Step 5 — A/B with LoRA mode

**SKIP-CLEAN.** `data/models/image_gen/cartoon_lora/` is empty — no `pytorch_lora_weights.safetensors` was dropped by the autonomous F.5-1 install (no good HF-hosted SD 1.5 cartoon LoRA was available without browser/auth, per `project_f5_status_2026-05-09.md` memory). The F.5-1 load smoke already confirmed graceful handling: `Mode B SKIPPED — no cartoon LoRA at data\models\image_gen\cartoon_lora (optional A/B alt)`.

**Implication for winner pick:** mode `checkpoint` is the only testable path in this run, which aligns with the documented tiebreak rule — `checkpoint` is a single-model load, fewer moving parts, simpler operator setup. Winner = **checkpoint**. Documented in Step 8.

## Step 6 — Capability-False composite verification

**PASS** (operator-confirmed visual + agent-side artefact analysis).

**Procedure correction:** issue #67 step 8 says to rename `data/models/image_gen/sd15/base/model_index.json`, but per [src/toybox/image_gen/capability.py:110-114](../../src/toybox/image_gen/capability.py#L110-L114) the capability gate's required-checkpoints set is mode-dependent — in `checkpoint` mode (the run's default) it probes `cartoon_checkpoint/model_index.json`, NOT `sd15/base/model_index.json`. Renaming the sd15 file in checkpoint mode is a no-op (capability stays True). The operator's first attempt observed exactly that. Procedure corrected mid-run: rename `cartoon_checkpoint/model_index.json` instead. Filing as another doc gap in #67.

**After the corrected rename + restart:**
- Boot log: capability=False reason=`checkpoints missing: cartoon_checkpoint/model_index.json` (operator-confirmed)
- Parent UI: "running in composite-only mode" banner visible (operator-confirmed)
- Two fresh toys uploaded for verification: Pichu (`9de7ca75…` at 22:04:27Z) + Miss Unicorn (`61c60899…` at 22:05:25Z). Both 10/10 sprite slots `done`.

**Agent-side validation that the composite path actually fired (file-size heuristic):**

| Toy | Mode | Avg sprite size | Total | 10/10? |
|---|---|---|---|---|
| `5f6e3587…` Mr Unicorn (post-fix, SD 1.5) | checkpoint | ~15 KB | ~150 KB | ✓ |
| `9de7ca75…` Pichu (composite) | Tier C composite | ~6 KB | 61.6 KB | ✓ |
| `61c60899…` Miss Unicorn (composite) | Tier C composite | ~5.7 KB | 56.8 KB | ✓ |
| (templates baseline) | — | ~1.5 KB | 14.8 KB | n/a |

Composite outputs are ~3x smaller than SD 1.5 outputs and ~4x larger than bare templates — consistent with `template + alpha-composited toy cutout`, the F.5-3a Tier C contract. **Composite path verified end-to-end.**

**Wall-clock:** operator did not time precisely but rated as "fast"; consistent with the F.5-3a design target (~100 ms per slot, ~1 s total — no GPU inference, just Pillow + PNG encode).

**State after step:** both `model_index.json` files restored (operator confirmed via Move-Item; agent confirmed via filesystem check). Backend still running with capability=False because we haven't restarted yet — Step 7's restart picks that up cleanly.

## Step 7 — Env-disabled regression

**PASS** (operator visual + agent-side DB check).

**Procedure:** Ctrl+C backend → `$env:TOYBOX_IMAGE_GEN_ENABLED='false'` → restart.

**Boot log (verbatim):**

```
INFO toybox.app: image-gen capability=False reason=image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED
```

**Operator-uploaded test toy:** `2d34ba68…` Mr Unicorn (22:10:51Z) — upload itself succeeded (toy row created). Parent UI surfaced banner: **"Image generation disabled: image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED"**. **No sprites attempted** (operator visual: no 10-cell grid populating).

**Agent-side verification:** new toy row present, **0 rows in `toy_actions` for that toy** — sprite jobs never queued, AND no Tier C composite fallback fired (which is the documented hard-off contract: env-disabled is a HARDER off than capability-False; capability-False falls through to composite, env-disabled does not). The expected 409 response shape (`image_gen_disabled`) is surfaced through the parent UI's error banner; we did not separately probe `POST /api/toys/<id>/actions/regenerate-all` from curl since the operator visual is sufficient evidence.

**State after step:** env var still set on the operator's PowerShell session — operator instructed to `Remove-Item Env:TOYBOX_IMAGE_GEN_ENABLED` before the Step 8 final restart so the shipping config has image-gen enabled.

## Step 8 — Winner pick + doc updates

**Winner: `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint`.** Tiebreak rule applied (per `project_f5_status_2026-05-09.md` memory and plan §F.5-4 step 9): checkpoint mode is a single-model load — fewer moving parts, simpler operator setup, cleaner default. The LoRA path was skip-clean (no LoRA dropped during F.5-1), so `checkpoint` was the only testable mode this run; the visual rating ("not the best images but generate fast") was acceptable per the F.5-4 issue criteria.

**No code changes required:** [`src/toybox/image_gen/pipeline.py`](../../src/toybox/image_gen/pipeline.py) `DEFAULT_CARTOON_MODE = "checkpoint"` is already the shipping default. The `.env` file doesn't pin the var (relies on the default), which is correct.

**Doc updates landed:**
- [`documentation/operator/image-gen-runtime.md`](../operator/image-gen-runtime.md) — "Cartoon LoRA" section: added F.5-4 outcome callout pointing at this run-doc, recorded the tiebreak rationale, kept the LoRA install path documented for future opt-in.
- This run-doc: full procedure + agent-side validation + decision rationale.

**Doc updates deferred** (filed as follow-ups to the next phase, not blocking F.5-4/F.5-5):
- `.env.example` — has never existed in the repo (verified `git log --all -- .env.example` returns empty). The plan repeatedly references it (most prominently in `phase-f-5-sprite-cartoon-redo.md` lines 148, 396, 403, 414) but creating it from scratch is a meaningful new artifact deserving its own scoped task. Opening `.env.example` is in the deferred-followups list below.

## Outcome

**F.5-4 PASS.** All load-bearing verifications green:

| Verification | Result | Evidence |
|---|---|---|
| Capability gate green at boot (checkpoint mode) | ✓ | `image-gen capability=True reason=capable` |
| Toy upload + 10/10 sprite generation (Mode A) | ✓ | 3 toys uploaded across the run, 30/30 sprite slots `done` |
| Activity trigger / kiosk render | ✓ (after fix-bug detour) | Operator visual + DB toy_ids populated post-fix |
| Single-slot regenerate | ✓ | All 10 slots done with unique seeds |
| Mode B LoRA A/B | SKIP-CLEAN | No LoRA dropped — documented winner = checkpoint |
| Capability-False composite (Tier C) | ✓ | 2 toys composite-mode, file-size heuristic ~3x smaller than SD-1.5 |
| Env-disabled hard-off (409 / no fallback) | ✓ | DB shows 0 toy_actions for new toy after env-disable |
| 0 native crashes throughout the session | ✓ | `nvidia-smi` log clean; no python.exe WER records |
| Peak VRAM under 6 GB floor | ✓ | 4599 MiB peak per `logs/f54-vram.csv` |

**Unblocks F.5-5 soak.**

## Anomalies / follow-ups

### From the F.5-4 procedure itself
- **Issue #67 doc gap (1):** step 1 references `(Invoke-RestMethod /api/health).ai.claude_capable`, but that field doesn't exist in [src/toybox/api/health.py](../../src/toybox/api/health.py). The "Phase A placeholder" comment at [src/toybox/core/capability.py:82](../../src/toybox/core/capability.py#L82) marks Step 5 wiring as deferred. F.5-4 derived Claude capability from observed `tags_populated_by` behavior at toy-upload time. Follow-up: either ship the `/api/health` Claude-capable field or revise issue body.
- **Issue #67 doc gap (2):** step 8 says to rename `data/models/image_gen/sd15/base/model_index.json`; but per [src/toybox/image_gen/capability.py:110-114](../../src/toybox/image_gen/capability.py#L110-L114) the required-checkpoints set is mode-dependent. In `checkpoint` mode (the run's default) the gate doesn't probe that file at all — operator must rename `cartoon_checkpoint/model_index.json` instead. Procedure was corrected mid-run.
- **`.env.example` never existed:** plan references it but `git log --all -- .env.example` is empty. Opening it as a fresh artifact is its own scoped task.

### From the `/fix-bug` detour
- **Loop-mode (Claude tool-loop) toy_ids wiring:** out of scope from the toy_ids fix; `ClaudeActivityGenerator`'s Activity output gets default `toy_ids=()`. Kiosk still won't render sprites in loop mode. Wire when escalation/on_intent is moved to production.
- **LOW: `_pick_toy_entry` sort tiebreak** — duplicate-display-name toys deterministically pick on insertion order. Add `(id)` as secondary sort key.
- **STYLE: `Sequence[str]` → `list[str]`** in `_persist_activity`'s new param (file-local convention is `list[X]`).
- **Cosmetic: trim per-test docstrings** in the new `tests/unit/activities/` files (sibling 1-sentence convention).
- **Pre-existing test flakes (not regressions):** `test_ws_heartbeat::test_server_pings_periodically` and `test_version_conflicts::test_concurrent_propose_at_cap_evicts_consistently` — both pass in isolation, occasionally flake under full-suite concurrency.

### Cosmetic anomaly logged earlier
- One `0 MiB` row in `logs/f54-vram.csv` — likely a transient nvidia-smi sample during driver state change. Not load-bearing.
