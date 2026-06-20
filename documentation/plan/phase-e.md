# Phase E — Local model + tool-loop (post-v1)

> **Scope:** Phase E build plan — local-model substrate + tool-loop refactor (post-v1). Carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**` shape that `/build-phase` parses, plus phase-level goal, prerequisites, sequencing rationale, and open risks. Read this when working any Phase E step. Top-level overview is in [../master-plan.md](../master-plan.md).
>
> **Scope-cut 2026-05-10:** "Non-linear gameplay" (Step 29 / E5) is REMOVED from this phase. Phase G shipped multi-option choice steps + variable-length activities for the offline-template path on 2026-05-10. The remaining E5 sub-pieces (parent regenerate-from-here mid-activity, transcript reaction window, `step_pending` WS event for streaming generation) are latency accommodations for local inference rather than gameplay shape; if needed they fold into Step 28 (E4 tool-loop) or a future small phase, not into Phase E's critical path.

## What this feature does

Phase E ships two coupled capabilities on top of v1:

1. **Local inference** — replace Claude OAuth with a locally-hosted, supervised-fine-tuned open-weight model (Qwen2.5-7B Q4_K_M default, Qwen2.5-3B Q5_K_M fallback) so activity generation and the eval judge can run without network egress. Privacy-first households get "nothing leaves the house"; latency-sensitive paths get sub-2-second first-token responses on the home GPU.
2. **Tool-using generator loop** — refactor the single-shot generator interface so both Claude and local adapters can pull context on demand (`get_persona`, `get_room`, `get_inventory`, `get_recent_transcript`, `get_prior_steps`, `get_anti_signal`) instead of consuming a pre-resolved bag at prompt time. Two orthogonal env vars control dispatch: `TOYBOX_GENERATOR_ADAPTER=claude|local` picks the model path; `TOYBOX_GENERATOR_MODE=single|loop` picks single-shot vs tool-loop. Default is `adapter=claude, mode=single` (= v1 behavior). All four cells are valid shipping configs — see Appendix §"Adapter × mode matrix".

## Existing context

- **AI substrate** at [src/toybox/ai/](../src/toybox/ai/) already provides: `client.py` (Claude HTTP client), `oauth.py` + `refresh.py` (OAuth token lifecycle via Claude-CLI bridge), `capability.py` (capability gate), `breaker.py` (circuit breaker), `judge.py` (1-in-N async judge sampler), `rubric.py` (6-dim rubric: `schema`, `age_appropriateness`, `doability`, `persona_fidelity`, `coherence`, `safety`), `labeled_events.py` (telemetry table), `eval_dump.py` (ChatML JSONL export), `eval_run.py` (fixture batch + baseline regen + CI regression check). Phase E adds `local.py`, `tools.py`, `eval_compare.py`, and a thin Claude adapter wrapper.
- **Activity generator** at [src/toybox/activities/generator.py](../src/toybox/activities/generator.py) is currently single-shot — it consumes a `GeneratorContext` (pre-resolved persona / rooms / toys / banned themes / reading level / anti-signal) and returns an `Activity` with exactly 5 `ActivityStep`s. Phase E refactors this into a tool-loop adapter interface; the existing single-shot path becomes the `TOYBOX_GENERATOR_MODE=single` branch.
- **Activity-step storage** is normalized in `activity_steps` ([migrations/0001_initial.sql:84-92](../src/toybox/db/migrations/0001_initial.sql#L84-L92)) with `seq INTEGER NOT NULL` and `current INTEGER NOT NULL DEFAULT 0`. The Pydantic shape at [activities/models.py:69](../src/toybox/activities/models.py#L69) hard-codes `Field(min_length=5, max_length=5)` — E5 needs a migration + Pydantic loosening.
- **Eval scaffolding** ready: [tests/fixtures/eval/](../tests/fixtures/eval/) ships `prompts.jsonl` (20 fixtures spanning age × persona × trigger × room × edge cases), `holdout.json` (5 pinned IDs for CI regression), `baseline_scores.json` (placeholder — operator must run `uv run python -m toybox.ai.eval_run --judge claude` once to populate). The Phase E SFT export query (`safety>=4 AND mean_quality>=3.5 AND parent_signal != -1`) is already supported by the labeled_events schema; no migration needed for E3.
- **Plan format conventions** mature: Phases C/D in [plan.md](../master-plan.md) follow `### Phase` → table summary → `**Issues:**` line → `#### Step N:` with required bullets. This doc mirrors that.
- **Operating mode** for autonomous build: per [`feedback_autonomous_build_bundled_ui.md`](../../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md) the user prefers code-only/code-with-UI runs back-to-back with `--reviewers code` (drop `--ui` runtime reviewer); visual UI verification batches into one human-driven test pass at the end.

## Scope

**In:**
- Local inference runtime (Ollama / LM Studio / raw llama.cpp server — choice deferred to E1 decision doc) installed on the home Windows machine
- Two GGUFs downloaded: Qwen2.5-7B-Instruct Q4_K_M (primary) and Qwen2.5-3B-Instruct Q5_K_M (fallback)
- `LocalActivityGenerator` adapter implementing the same interface as the Claude path, with constrained decoding (Outlines or llama.cpp GBNF grammars) for schema-bound output
- LoRA SFT pipeline via Unsloth, consuming `labeled_events` filtered by safety/quality/parent-signal floors
- A/B eval (`uv run python -m toybox.ai.eval_compare`) with per-dimension delta + confidence intervals
- Tool registry (`src/toybox/ai/tools.py`) consumed by both Claude and local adapters; tool args validated against typed schemas before dispatch
- `TOYBOX_GENERATOR_ADAPTER=claude|local` and `TOYBOX_GENERATOR_MODE=single|loop` — two orthogonal env vars covering all four cells; single-shot Claude (= v1) retained as default fallback
- `is_local_capable()` extension to `src/toybox/ai/capability.py` — runtime HTTP health probe + model-loaded check, breaker per-adapter
- `is_complete: bool` per step + dynamic step count (1..max, max configurable, default 8)
- Activity-step migration + Pydantic loosening + child UI dynamic-step rendering ("..." indicator while next step generates)
- Parent pause/regenerate-from-here/end signal observation between steps + transcript-reaction window (default 30 s)
- Optional preference-pair RL (DPO or GRPO) gated on E3-E5 leaving a measurable gap

**Out:**
- Multi-modal local model (vision stays Claude Haiku via OAuth — see [how-to-run.md `TOYBOX_CLAUDE_VISION_MODEL`](how-to-run.md#configuration-env--settings)); only text generation moves local in this phase
- Production-ready safety filtering retraining (E2 explicitly leaves "ship local in mode 2 only / mode 2+3 / not ready" as a decision-by-evidence outcome)
- Cloud burst as the default (RunPod is documented fallback for any run that won't fit on the home GPU; not a default deployment target)
- v1.5 polish items unrelated to the local/loop/non-linear thesis (those are tracked separately in plan.md's Auxiliary section)
- Backwards compatibility for existing 5-step activities in flight at upgrade time (the `is_complete` migration is forward-only; existing rows default to `is_complete=0` which is the correct semantic for completed historical activities since they ran to step 5 and ended)

## Impact analysis

| File / module | Nature | Notes |
|---|---|---|
| `src/toybox/ai/local.py` | NEW | `LocalActivityGenerator` adapter — runtime client + constrained decoding + tool-loop integration |
| `src/toybox/ai/eval_compare.py` | NEW | A/B Claude-vs-local report with per-dimension delta + bootstrap CI |
| `src/toybox/ai/tools.py` | NEW | Tool registry (`get_persona`, `get_room`, `get_inventory`, `get_recent_transcript`, `get_prior_steps`, `get_anti_signal`) consumed by both adapters |
| `src/toybox/ai/adapters/__init__.py` | NEW | Adapter package; exposes `ClaudeActivityGenerator` + `LocalActivityGenerator` |
| `src/toybox/ai/adapters/claude.py` | NEW | Thin Claude wrapper that implements the loop-mode interface (single-shot path delegates to existing `client.py`) |
| `src/toybox/activities/generator.py` | REFACTOR | Extract single-shot path; add loop-mode branch dispatching to active adapter; keep current behavior under `TOYBOX_GENERATOR_MODE=single` |
| `src/toybox/activities/models.py` | MODIFY | Loosen `Activity.steps` to `min_length=1, max_length=<TOYBOX_MAX_STEP_CAP default 8>`; add `is_complete: bool` to `ActivityStep` |
| `src/toybox/db/migrations/0014_activity_steps_is_complete.sql` | NEW (E5) | Adds `is_complete INTEGER NOT NULL DEFAULT 0` to `activity_steps`. Renumbered forward from earlier-planned 0006 to 0014 because the play-queue + transcript-retention migrations (0010–0012) and Step 1's `redact_for_sft` (0013) shipped first. |
| `src/toybox/db/migrations/0004_labeled_events_tool_calls.sql` | DONE (Step 28 carve-out) | Adds nullable `tool_calls TEXT` column to `labeled_events`. Shipped 2026-05-05 in commit `33a4b3c`. |
| `src/toybox/ai/labeled_events.py` | MODIFY | Persist tool-call telemetry per generation as a sub-array (one row per generation, tool calls in JSON sub-field — shape pinned in Appendix §"Tool-call telemetry shape") |
| `src/toybox/ai/labeled_events.py` (CLI) | NEW (within file) | `python -m toybox.ai.labeled_events --count [--since <ISO>]` operator CLI; gates Step 27 prerequisites |
| `src/toybox/ai/capability.py` | MODIFY | Extend with `is_local_capable()` — HTTP health + `model loaded` probe; per-adapter breaker semantics |
| `src/toybox/ai/eval_dump.py` | MODIFY (E3) | Add `--sft-export` mode that applies PII redaction (transcript fields scrubbed of names/addresses) AND filters `redact_for_sft=true` rows out before ChatML emission |
| `src/toybox/ai/labeled_events.py` (schema) | MIGRATE (E3) | New column `redact_for_sft INTEGER NOT NULL DEFAULT 0` in migration 0013 — opt-out flag for rows operator manually flags as not-for-training |
| `src/toybox/api/activities.py` | MODIFY | Wire dynamic step generation: emit-one → observe → emit-next pattern; expose pause/regenerate-from-here/end transitions during generation. Emit new WS event `activity.step_pending` while generating next step (shape pinned in Appendix §"WS payloads for streaming generation"). |
| `src/toybox/db/migrations/0013_labeled_events_redact_for_sft.sql` | NEW (E3) | Adds `redact_for_sft INTEGER NOT NULL DEFAULT 0` to `labeled_events`. Note: the `tool_calls` column added by the Step 28 carve-out's 0004 migration is unrelated; both are nullable additions to the same table. Shipped as the E3 backend carve-out (see [e3-backend-carveout-plan.md](archive/e3-backend-carveout-plan.md)). |
| `frontend/src/parent/SuggestionCard.tsx` | MODIFY (E5) | Render dynamic step count + "..." indicator while next step generates; surface "regenerate from here" on the active step |
| `frontend/src/child/App.tsx` | MODIFY (E5) | Handle dynamic step list; "..." placeholder while loop generates next step; max-step cap enforcement |
| `scripts/train_lora.py` | NEW | Unsloth LoRA training driver consuming labeled_events ChatML JSONL export |
| `tests/fixtures/eval/prompts.jsonl` | REUSE | Existing 20 fixtures + 5-id holdout — no shape change; benchmark / pilot / SFT eval all consume the same set |
| `tests/fixtures/eval/baseline_scores.json` | REGEN | Operator must run `uv run python -m toybox.ai.eval_run --judge claude` once with real OAuth before E2; current file is placeholder |
| `documentation/local-model-decision.md` | NEW | E1 deliverable — runtime choice + 7B vs 3B vs cloud-burst decision with measured numbers |
| `documentation/rl-decision.md` | NEW | E6 deliverable — DPO/GRPO go/no-go with measured gap; written even if the answer is "skipped, here's why" |
| ~~`documentation/eval-fixtures.md`~~ | n/a | E5 superseded; doc retired 2026-05-10 (rubric canonical in `src/toybox/ai/rubric.py`) |
| `documentation/operator/local-runtime.md` | NEW | E1 operator setup procedure (CUDA toolkit, llama.cpp build, GGUF download paths, runtime startup) |
| `pyproject.toml` | MODIFY | Add `[project.optional-dependencies] local = [...]` for `outlines>=0.0.40` (or grammar lib choice from E2), `unsloth` (E3 only), maybe `httpx` extras for runtime client |
| `data/models/` | EXTEND | Already has `silero_vad.onnx`, `whisper`; add `qwen2_5_7b_instruct_q4_k_m.gguf`, `qwen2_5_3b_instruct_q5_k_m.gguf` (gitignored — placeholder `.gitkeep` ships) |
| `data/models/lora/` | NEW | Trained adapter directory `<timestamp>/`; gitignored; registry doc tracks active version |
| `.env` example | MODIFY | Add `TOYBOX_GENERATOR_ADAPTER` (claude\|local; default claude), `TOYBOX_GENERATOR_MODE` (single\|loop; default single), `TOYBOX_LOCAL_RUNTIME` (ollama\|lmstudio\|llamacpp; chosen in E1), `TOYBOX_LOCAL_MODEL_PATH`, `TOYBOX_LOCAL_RUNTIME_URL` (default `http://localhost:<TOYBOX_LOCAL_RUNTIME_PORT>`), `TOYBOX_LOCAL_RUNTIME_PORT` (default per runtime: ollama=11434, lmstudio=1234, llamacpp=8080), `TOYBOX_MAX_STEP_CAP` (default 8), `TOYBOX_TOOL_LOOP_TIMEOUT_SEC` (default 30), `TOYBOX_REACTION_WINDOW_SEC` (default 10, cap 30), `TOYBOX_LOOP_LATENCY_BUDGET_X` (default 3 — loop generation must complete within Nx single-shot baseline) |

## New components

### `src/toybox/ai/tools.py`
Tool registry and resolver. Each tool is a typed callable returning JSON-serializable data. Adapters (Claude or local) call tools via a uniform `call_tool(name, args, ctx)` interface. Tool calls are logged into `labeled_events.tool_calls` as a sub-array on the matching generation row. Initial tools per [phase-e.md "E4"](phase-e.md): `get_persona(persona_id)`, `get_room(room_id)`, `get_inventory(child_id, recency_window)`, `get_recent_transcript(window_sec)`, `get_prior_steps(activity_id)`, `get_anti_signal(template_id, slot_dict)`. Tool resolution must be cancellation-safe and capped via `TOYBOX_TOOL_LOOP_TIMEOUT_SEC` (default 30 s aggregate per generation).

### `src/toybox/ai/adapters/`
Symmetric adapter package. `claude.py` wraps the existing `client.py` to expose both single-shot (`generate_activity(ctx)`) and loop-mode (`generate_activity_loop(ctx, tools)`) entry points. `local.py` implements the same interface against a local runtime (Ollama HTTP / LM Studio HTTP / raw llama-server HTTP) with constrained decoding via Outlines or llama.cpp GBNF. Both adapters honor the existing breaker + capability gate so v1's safety net stays intact.

### `src/toybox/ai/local.py`
`LocalActivityGenerator` — concrete adapter for the local runtime. Owns the HTTP client to whichever runtime E1 picks. Loads grammars from a side directory (`data/grammars/activity.gbnf` etc.) so swapping schemas doesn't require redeploying the adapter. Lazy-loads constrained-decoding deps so `import toybox.ai.local` is cheap when the local path isn't active. CLI: `uv run python -m toybox.ai.local --probe` (E1 smoke; shipped 2026-05-14 at `2330de7`).

### `src/toybox/ai/local_benchmark.py`
E1c benchmark CLI; sibling to `local.py` to avoid the `python -m toybox.ai.local` self-import warning surfaced at E1b ship. CLIs: `--benchmark --model <tag>` (per-model measurement; refuses to run if probe-pass marker is absent or >1 h stale) and `--write-decision-doc --7b-results <path> --3b-results <path>` (applies the E1c threshold gate, writes `documentation/local-model-decision.md`). Code prereq to Step 25c — see Step 25c §"Code prerequisites" below.

### `src/toybox/ai/eval_compare.py`
A/B evaluation driver. Reads two judge-scored runs (Claude baseline + local candidate) over the same fixture IDs, computes per-dimension mean delta with bootstrap 95% CI, and writes a markdown report under `documentation/eval-runs/<date>-claude-vs-local.md`. Reuses existing `judge.py` and `rubric.py` — no new scoring logic.

### `scripts/train_lora.py`
Unsloth LoRA training driver. Consumes ChatML JSONL exported from `eval_dump.py` filtered by the documented floors. Produces a merged-and-quantized GGUF adapter under `data/models/lora/<timestamp>/`. Logs training loss, evaluates against the held-out fixture set after training, writes a small training report card. Llama-Factory documented as fallback if Unsloth's Triton-on-Windows install breaks (per [phase-e.md "Open risks"](phase-e.md#open-risks)). RunPod documented as cloud-burst path for runs that won't fit the home GPU.

### `documentation/local-model-decision.md`
E1 deliverable. Captures: runtime chosen + rationale, measured TPS / first-token / VRAM headroom for both 7B and 3B, schema-bound JSON validity %, decision (7B / 3B / cloud-burst), known gotchas. Future-Phase-F should NOT need to re-run E1 to know what was picked.

### `documentation/rl-decision.md`
E6 deliverable. Captures: measured gap (Claude vs local mean dimension scores after two SFT iterations), decision (proceed / skip), if proceed: which algorithm (DPO vs GRPO), data shape, expected timeline. Written even if the answer is "skipped, here's why."

### `documentation/operator/local-runtime.md`
E1 operator setup procedure. Step-by-step: CUDA toolkit install (Windows native, no WSL required per [phase-e.md "E1"](phase-e.md)), llama.cpp CUDA build OR Ollama install OR LM Studio install (one path picked in E1 decision doc), GGUF download paths and verification (sha256), runtime startup smoke (`curl http://localhost:<runtime-port>/v1/models` returns 200), how to stop+restart, where logs live.

## Design decisions

### Adapter × mode env-var matrix (orthogonal axes)
The original master plan named only one env var (`TOYBOX_GENERATOR_MODE=single|loop`) but the carve-out on Step 28 makes "Claude+loop" a real shipping config — so adapter selection and mode are now two independent axes. `TOYBOX_GENERATOR_ADAPTER=claude|local` picks the model path; `TOYBOX_GENERATOR_MODE=single|loop` picks single-shot vs tool-loop. All four cells are valid: `claude+single` is v1 default; `claude+loop` is the Step 28 carve-out (lands first); `local+single` ships in E2 as the first local-inference proof point; `local+loop` is the post-E3 target. Keeping them orthogonal means the operator can flip adapter without re-evaluating mode, and a regression in one cell doesn't force fallback through the other axis. Full matrix table in Appendix §"Adapter × mode matrix".

### Tool-args validation as a hard interface boundary
The model emits tool-call args; without validation a fine-tuned model could emit `room_id="../../etc/passwd"` and a naive `get_room` resolver would happily read it. Tool args go through Pydantic schemas in `tools.py` (one schema per tool) BEFORE dispatch — UUID for `room_id`/`persona_id`/`activity_id`, slug regex for library persona ids, bounded ints for window/limit args, etc. Validation failures return a structured error to the model loop (`{error: "invalid_args", tool: "get_room", reason: "room_id must be UUIDv4"}`) so the model can recover by re-issuing with valid args, not crash. This is the same posture FastAPI already uses on REST input — extending it to model-emitted args closes the obvious injection vector.

### Local-runtime capability gate (`is_local_capable()`)
Existing `capability.py` only knows about Claude (OAuth + Claude breaker). The carve-out on Step 28 needs a parallel check for the local path so loop-mode dispatch can fall back when the runtime is down or the model isn't loaded. New `is_local_capable()` makes a cheap HTTP GET to `<TOYBOX_LOCAL_RUNTIME_URL>/v1/models` (or runtime-specific equivalent) with a short timeout, asserts the chosen model id is in the response, and is rate-limited via the breaker so a flapping local runtime doesn't generate a load storm. Per-adapter breaker state means the Claude breaker tripping doesn't disable local and vice versa.

### Sequencing — keep canonical E1→E6 with one explicit carve-out
The master plan rationale ([phase-e.md "Sequencing rationale"](phase-e.md)) is: E1 first because the entire phase's thesis depends on adequate local TPS; E4 last among the architectural changes because tool-loop latency stacked on an unproven model is the worst possible compounding-failure path. Phase E plan-doc keeps that order. **Carve-out:** the *Claude-only* parts of E4 (tool registry interface in `tools.py`, `ClaudeActivityGenerator` adapter, `TOYBOX_GENERATOR_MODE=single|loop` flag plumbing, `labeled_events.tool_calls` sub-array) can land *before* E3 since the latency-stack rationale only applies once a local adapter is the active path. The local-adapter implementation of the loop interface stays gated on E1+E2+E3 landing first. This carve-out makes Step 28 the natural "long-unattended autonomous build" target while E1-E3 wait on hardware + telemetry availability.

### Tool registry as a separate module
`src/toybox/ai/tools.py` rather than collapsing tools into `activities/generator.py`. Two adapters consume tools (Claude + local), and we want a single source of truth for the tool surface so an adapter can't silently miss one. Also gives a natural seam for unit tests that wire fake context vs real DB. Tool calls are recorded into `labeled_events.tool_calls` so analytics + SFT data both see the same trail.

### Adapter symmetry — introduce ClaudeActivityGenerator wrapper
Master plan only names `LocalActivityGenerator`, but the loop refactor only works if both paths implement the same interface. Adding `ClaudeActivityGenerator` as a thin wrapper around existing `client.py` gives the symmetric pair, lets `TOYBOX_GENERATOR_MODE=single|loop` dispatch cleanly without forking `activities/generator.py`, and keeps the v1 single-shot Claude path untouched (it routes through the wrapper's `generate_activity` method that delegates to the existing implementation). Risk: adds one indirection layer; mitigated by keeping the wrapper genuinely thin (no logic, just dispatch).

### Schema migration vs JSON-encoded `is_complete`
`is_complete` ships as a real column on `activity_steps` (migration 0014 — renumbered forward from earlier-planned 0006 since the play-queue + transcript-retention migrations (0010–0012) and Step 1's 0013 shipped first), not as a key inside `summary` JSON. Activity steps are already a normalized table; adding a column is the obvious move and keeps query plans simple ("find the next not-yet-complete step" stays an indexed lookup). Pydantic shape change is the bigger lift — `Field(min_length=5, max_length=5)` becomes `Field(min_length=1, max_length=<TOYBOX_MAX_STEP_CAP default 8>)`. Offline templates ship 5 linear steps and remain the breaker-open / model-down floor (per [phase-e.md "E5"](phase-e.md)).

### Operating-mode override for E5's UI work
Step 29 (E5) canonical recommended flags are `--reviewers full --ui` per the master plan table. Per the autonomous-build operating mode, the per-step run uses `--reviewers code` and the UI verification batches into a Phase-E bundled UI test pass when E5 lands (working name: M2.6). Manual M2.5 — defined in [plan.md](../master-plan.md) before the Phase E header — is scoped to v1 only (steps 16/17/18/21/22/23/24); E5 is explicitly out of M2.5's scope because it isn't built yet. Plan-doc preserves the canonical flags as the default and surfaces the override as an explicit operating-mode note, mirroring how Phase C handled steps 16/17/18.

### Smoke gate carved out as its own step (25b)
The benchmark itself runs 10 prompts and is therefore not "long-running observation" in the data-pipeline guardrail sense. But a runtime/GGUF mismatch (wrong tokenizer, missing quant kernel, partial download) burns the first 1-2 prompts of any benchmark run and produces noisy data that's tempting to over-interpret. So E1 is split into three sub-steps with the smoke probe (25b) as an explicit autonomous-friendly `Type: code` step between operator-driven 25a (install + GGUFs) and 25c (benchmark + decision doc). The probe is `uv run python -m toybox.ai.local --probe` — ONE prompt through the chosen runtime end-to-end, asserts no exception, asserts the output parses as valid JSON under the activity schema. Wall-clock budget <60 s. The benchmark CLI in 25c refuses to run if the most recent probe-pass marker is older than 1 hour, forcing 25b re-run on runtime drift. This split also satisfies the build-phase taxonomy rule that no step silently mixes implementation with manual verification (per `feedback_plan_auto_vs_manual_sections.md`).

## Build steps

| # | Step | Type | Reviewers (canonical) | Done-when summary |
|---|------|------|----------------------|-------------------|
| 25a | E1a — Install runtime + GGUFs | operator | n/a | Runtime installed; both GGUFs on disk + sha256-verified |
| 25b | E1b — Smoke probe | code | `--reviewers code` | `local.py --probe` passes one fixture prompt through the runtime end-to-end; output parses against activity schema; <60 s wall-clock |
| 25c | E1c — Benchmark + decision doc | code prereq + operator | code prereq: `--reviewers code` · operator: n/a | **Code prereq:** `local_benchmark.py` shipped with `--benchmark` + `--write-decision-doc` CLIs + unit tests. **Operator:** benchmark run for 7B + 3B; `documentation/local-model-decision.md` landed with measured numbers + 7B-vs-3B-vs-cloud-burst decision + 8 GB host contention answer. |
| 26 | E2 — Constrained-decoding pilot vs Claude baseline | code | `--reviewers code` | `LocalActivityGenerator` ships; A/B eval comparison written; mode-2-only / mode-2+3 / not-ready decision recorded |
| 27 | E3 — First SFT iteration | code (with operator-driven training run) | `--reviewers code` | LoRA adapter trained, merged to GGUF, deployed alongside base; eval re-run; per-dimension delta vs base reported; judge-vs-parent agreement re-checked |
| 28 | E4 — Tool-loop refactor | code | `--reviewers code` | Tool registry + Claude+local loop-mode adapters; both env axes (`TOYBOX_GENERATOR_ADAPTER`, `TOYBOX_GENERATOR_MODE`) wired; tool args validated; `is_local_capable()` shipped; tool-call telemetry into `labeled_events`; latency budget + cache strategy documented |
| 29 | E5 — Non-linear gameplay | full-stack | `--reviewers full --ui` (autonomous: `--reviewers code`) | `is_complete` per step; dynamic step count 1..max-cap; emit-one → observe → emit-next loop; child UI dynamic; offline path stays 5-step linear; coherence rubric updated; WS `activity.step_pending` event shipped |
| 30 | E6 — Preference-pair RL (optional / gated) | code (with operator-driven training run) | `--reviewers code` | DPO/GRPO loop OR documented "skipped, here's why"; only proceeds if E3-E5 leave mean-dimension delta >0.5 after two SFT iterations |

**Issues:** Phase E umbrella → #34 · step 25a → #35 · step 25b → #36 · step 25c → #37 · step 26 → #38 · step 27 → #39 · step 28 → #40 (carve-out closed 2026-05-05) + #43 (full-step follow-up) · step 29 → #41 · step 30 → #42 (created by `/repo-sync` 2026-05-05).

#### Step 25a: E1a — Install runtime + GGUFs

- **Problem:** Operator-driven install of the chosen local-inference runtime on the home Windows machine: llama.cpp CUDA build OR Ollama OR LM Studio (Windows-native; no WSL required per [phase-e.md "E1"](phase-e.md)). Runtime choice + rationale documented in `documentation/operator/local-runtime.md` (E1a deliverable). Two GGUFs downloaded to `data/models/` (gitignored): Qwen2.5-7B-Instruct Q4_K_M (primary) and Qwen2.5-3B-Instruct Q5_K_M (fallback). Both verified by sha256 against upstream-published checksums; checksums recorded in the operator doc. Runtime-load probe (whatever the runtime's "load this model" command is) confirms both GGUFs load without OOM on the home GPU. RunPod cloud-burst setup procedure (API key sourcing, billing model — pay-per-second on H100/A100; "when to escalate" criterion: 7B fails benchmark + 3B fails benchmark = cloud-burst trigger) included as an appendix in the operator doc.
- **Type:** operator
- **Issue:** #35
- **Flags:** n/a (operator step; not invoked through `/build-step`)
- **Status:** PENDING
- **Depends on:** none (kicks off Phase E)
- **Done when:** Runtime process responds on `<TOYBOX_LOCAL_RUNTIME_URL>/v1/models` (or runtime-specific equivalent); both GGUFs sha256-verified; both load via runtime's load command without OOM; `documentation/operator/local-runtime.md` covers install + sha256s + RunPod escalation procedure.

#### Step 25b: E1b — Smoke probe

- **Problem:** New `uv run python -m toybox.ai.local --probe` CLI sends ONE prompt drawn from `tests/fixtures/eval/prompts.jsonl` through the installed runtime end-to-end. Output is parsed against the existing activity schema (5-step shape OK at this stage — loop-mode lands in E4); any exception, schema mismatch, or non-zero exit fails the probe. Wall-clock budget <60 s. The probe is the autonomous-friendly portion of E1 — it's a real smoke gate against real components (runtime + GGUF + activity schema) but doesn't depend on any human judgment. Probe writes a `data/models/.probe-pass-<iso>.json` marker on success that the benchmark CLI in 25c reads to enforce its "passing probe within the last hour" prerequisite. The CLI also serves as the regression-detection seam: re-running the probe after an OS update / driver bump / GGUF re-download surfaces breakage in <60 s before any benchmark or pilot work wastes a session.
- **Type:** code
- **Issue:** #36
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step 25a (#35)
- **Done when:** Probe CLI shipped at `src/toybox/ai/local.py`; running `uv run python -m toybox.ai.local --probe` against the chosen runtime parses one fixture activity end-to-end with no exceptions; marker file written; CI-friendly fake-runtime fixture in tests so probe-CLI logic is tested without a real GGUF.

#### Step 25c: E1c — Benchmark + decision doc

- **Problem:** Operator runs `uv run python -m toybox.ai.local_benchmark --prompts tests/fixtures/eval/prompts.jsonl` (10-prompt fixed set drawn from the eval fixtures, deterministic subset `f001` … `f010`) for both 7B Q4_K_M and 3B Q5_K_M. Benchmark CLI refuses to run if the most recent `data/models/.probe-pass-<iso>.json` marker is older than 1 hour (forces 25b re-run, catching runtime drift). Per-prompt: cold-start time, warm first-token latency, steady-state TPS, peak VRAM at 4K context, schema-bound JSON validity %. Aggregate stats written to `documentation/local-model-decision.md`. Decision recorded per the gate at [phase-e.md "Manual M6"](phase-e.md#manual-m6--local-model-tps-check-during-e1): 7B if it clears <30 s cold / <2 s warm / ≥30 TPS / <11 GB VRAM / 100% validity; else 3B if it clears <15 s cold / <1 s warm / ≥60 TPS / <7 GB VRAM / 100% validity; else cloud-burst (RunPod) and Phase E scope re-discussion (privacy thesis vs compute reality is a discussion, not a default — see master plan §"Decision gate"). Decision doc also captures: runtime chosen + rationale, known gotchas surfaced during install, post-mortem on any benchmark numbers that surprised the operator. Future-Phase-F reads this doc instead of re-running E1.

  **Note on 8 GB hosts:** the 7B-vs-3B-vs-cloud-burst gate above measures each model in isolation. On the Plan-default RTX 4070 Laptop (8 GB VRAM shared with F.5 image-gen, which peaks at ~6.1 GB per [`2026-05-06-phase-f-8gb-feasibility.md`](../runs/2026-05-06-phase-f-8gb-feasibility.md)), the decision doc must ALSO record the **contention answer** chosen from: (a) sequentialize image-gen and local-model behind the capability gate, (b) default to 3B-only so both can coexist, or (c) cloud-burst 7B. E1b's marker on this host already recorded 7B at 6115 MiB VRAM at idle-plus-loaded — leaving no headroom for image-gen — so the contention question is operative regardless of which model wins the in-isolation benchmark.

- **Type:** code prerequisite then operator
- **Issue:** #37 (operator); code prereq sub-issue TBD via `/repo-sync`, or attached to #37 via `--issue 37` on the build-step
- **Flags:** code prereq runs as `/build-step --reviewers code`; the operator step itself is n/a (human-driven measurement + decision write-up)
- **Status:** PENDING

##### Code prerequisites (must ship before the operator run)

The Step 25b probe CLI shipped at master `2330de7` covers `--probe` only. The `--benchmark` and `--write-decision-doc` CLIs called out in the Problem statement above are code work that needs to land before the operator can run E1c. Suggested split:

- **Module:** new `src/toybox/ai/local_benchmark.py` (separate from `local.py` to avoid the `python -m toybox.ai.local` self-import RuntimeWarning surfaced at E1b ship — see [`documentation/operator/local-runtime.md`](../operator/local-runtime.md) "Recorded on this host" §"Notes / deviations").
- **`--benchmark --model <tag>` behaviour:**
  - Reads the most recent `data/models/.probe-pass-*.json` marker. If absent OR `iso_ts` older than 1 hour, exit 1 with `code=probe_stale detail=...`. Forces an E1b re-run, catching runtime drift since the probe last passed.
  - Iterates the 10-prompt subset `f001` … `f010` from `tests/fixtures/eval/prompts.jsonl` in order.
  - Per-prompt metrics: cold-start time (first request after model load — force-unload via `/api/generate` with `keep_alive=0` between models), warm first-token latency (median across the 10 prompts after the cold call), steady-state TPS (response tokens / wall-clock excluding first-token), peak VRAM at 4 K context (sample `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` mid-generation via a background thread), schema-bound JSON validity (`Activity.model_validate` boolean per prompt).
  - Writes per-model results to `data/models/.benchmark-<model-slug>-<iso>.json` (colon-stripped ISO, matching the marker filename convention E1b established).
- **`--write-decision-doc --7b-results <path> --3b-results <path>` behaviour:** reads both per-model result files, applies the threshold gate from the Problem statement above, and writes `documentation/local-model-decision.md` with: per-model metrics table, decision (7B / 3B / cloud-burst), contention-answer placeholder for 8 GB hosts (a/b/c above — operator fills in), runtime choice rationale, known gotchas section (operator fills in).
- **HTTP convention:** urllib only, mirroring `src/toybox/ai/local.py`'s `--probe` pattern. No `requests`, no `httpx`.
- **Tests at `tests/unit/ai/test_local_benchmark.py`:** unit-tested with stub measurements + fake-runtime `http.server` fixture matching `tests/unit/ai/test_local_probe.py`'s pattern. Branches to cover:
  - probe-stale (no marker / marker older than 1 hour) → exit 1 `code=probe_stale`
  - schema-validity tally (mix of valid + invalid `Activity` responses → correct % computed)
  - decision gate (each of 7B-pass / 3B-pass / cloud-burst paths exercised with stub metrics)
  - decision-doc write (assert file contents include both models' metrics tables, the verdict, and the contention-answer placeholder)
  - the real-GPU benchmark itself is NOT unit-tested — the operator step that follows is the live run.
- **Reviewer flags:** `--reviewers code`. No UI, no runtime gauntlet.
- **Done when (code):** `local_benchmark.py` + `test_local_benchmark.py` shipped; ruff/mypy/pytest clean; `LocalActivityGenerator` and `local.py` unchanged from `2330de7`.

##### Operator run (after the code prereq lands)

- **Depends on:** Step 25a (#35); Step 25b (#36 — probe pass marker); code prereq above
- **Steps:**
  1. Run `uv run python -m toybox.ai.local --probe` to land a fresh marker (must be within the last hour).
  2. `uv run python -m toybox.ai.local_benchmark --model qwen2.5:7b-instruct-q4_K_M`
  3. `uv run python -m toybox.ai.local_benchmark --model qwen2.5:3b-instruct-q5_K_M`
  4. `uv run python -m toybox.ai.local_benchmark --write-decision-doc --7b-results <path> --3b-results <path>`
  5. Operator inspects `documentation/local-model-decision.md`, fills in the contention-answer + runtime-choice + gotchas sections, commits the file.
- **Done when (operator):** Benchmark numbers captured for both GGUFs; `documentation/local-model-decision.md` landed with measured numbers + decision (7B / 3B / cloud-burst) + contention answer for 8 GB hosts + runtime choice rationale + known gotchas.
- **Manual companion:** Manual M6 — Local model TPS check ([phase-e.md "Manual M6"](phase-e.md#manual-m6--local-model-tps-check-during-e1)) IS this step; no separate manual run.

#### Step 26: E2 — Constrained-decoding pilot vs Claude baseline

- **Problem:** New `LocalActivityGenerator` adapter (`src/toybox/ai/local.py`) implements activity generation behind the same single-shot interface as the existing Claude path, using Outlines OR llama.cpp GBNF grammars for schema-bound output (NOT raw JSON-mode prompting — too brittle on 3B-7B scale per [phase-e.md "Open risks"](phase-e.md#open-risks)). Held-out eval fixtures from step 15 run against both Claude and local model via `uv run python -m toybox.ai.eval_compare`; judge scores stored side-by-side in `labeled_events` (one row per fixture per generator path). The A/B report writes per-dimension mean delta with bootstrap 95% CI to `documentation/eval-runs/<YYYY-MM-DD>-<NN>-claude-vs-<adapter>.md` (NN is zero-padded run-of-day counter so multiple runs per day don't collide; first run is `01`). **Decision recorded:** ship local in mode 2 only / mode 2+3 / not ready, based on the measured deltas + safety auto-fail count. Constrained-decoding library choice (Outlines vs GBNF) recorded with rationale; both options stay supported in `local.py` so swapping is a configuration change, not a refactor. **Grammar rejection handling:** when constrained decoding rejects model output mid-stream (parser violation, max-token exhaustion, schema mismatch), the adapter retries once with the same prompt + a stricter grammar (no optional fields); on second failure, falls back to the existing offline template path with a logged WARNING carrying the violation excerpt (capped at 200 chars to avoid log spam). The offline fallback is the breaker-open / model-down floor that already exists in v1 — extending it here means a flaky local model never blocks the kid-facing path. Eval-fixtures baseline must be populated before this step runs — operator gate: `tests/fixtures/eval/baseline_scores.json` has real Claude scores (NOT placeholder).
- **Type:** code
- **Issue:** #38
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step 25c (#37 — decision doc landed → runtime + GGUF chosen); Manual eval-fixtures baseline regen (operator runs `uv run python -m toybox.ai.eval_run --judge claude` once with real OAuth before E2 starts — this is the eval-fixtures baseline gate from [memory line 233](../../../../.claude/projects/c--Users-abero-dev/memory/MEMORY.md))
- **Done when:** `LocalActivityGenerator` ships at `src/toybox/ai/local.py` with `generate_activity(ctx)` interface matching the Claude path; constrained decoding via the chosen library produces 100% schema-valid output on the held-out fixture set; A/B report at `documentation/eval-runs/<date>-<NN>-claude-vs-local.md` has per-dimension delta + bootstrap 95% CI + safety-auto-fail count; mode-2-only / mode-2+3 / not-ready decision recorded in the report; grammar-rejection retry+fallback path covered by integration test (force a rejection, assert offline fallback fires).

#### Step 27: E3 — First SFT iteration

> Backend substrate carve-out: see [e3-backend-carveout-plan.md](archive/e3-backend-carveout-plan.md). This Step 27 ships the model-side work (Unsloth training driver, redacted-corpus export, eval comparison); the carve-out shipped the PII-filter + SFT-export plumbing — `redact_for_sft` column (migration 0013) and the `--sft-filter` CLI gate — ahead of model work.

- **Problem:** Unsloth-driven LoRA fine-tuning of the chosen base model on real-use data. Ideal sample target is ≥200 `(inputs, activity, parent_signal)` tuples accumulated in `labeled_events`, but no hard phase-gate floor — training driver only refuses to run with 0 rows (defensive empty-corpus guard, not a gate). The ≥1-month calendar gate (dropped 2026-05-10) and the ≥50-row SFT-filter gate (dropped 2026-05-21) were both vibes-not-load-bearing. Install Unsloth on Windows native via `uv pip install unsloth --torch-backend=auto` per upstream docs (Triton-Windows fragility is a known [phase-e.md "Open risks"](phase-e.md#open-risks); Llama-Factory documented as fallback if the install breaks). **Migration 0013** adds `redact_for_sft INTEGER NOT NULL DEFAULT 0` to `labeled_events` — operator-set opt-out flag for rows known to contain unredactable PII (rare; default 0 means most rows train). **PII redaction pass:** `eval_dump.py --sft-export` mode applies a redaction filter to the *input* side of every ChatML pair before JSONL emission — transcript text strings have child names (from `children.display_name`), addresses, phone numbers, and full-name patterns scrubbed via a deny-list + regex pass. The filter also drops rows where `redact_for_sft=1`. **Filter combined:** `safety_score>=4 ∧ mean_quality>=3.5 ∧ parent_signal!=-1 ∧ redact_for_sft=0` produces the training corpus. Training driver `scripts/train_lora.py` consumes the redacted ChatML JSONL. Trained adapter merged to GGUF via Unsloth's `save_pretrained_gguf` and deployed to local runtime alongside base. Same eval suite re-run via `eval_compare`; per-dimension delta vs base reported. **Judge-vs-parent agreement re-checked** to detect judge drift on the fine-tuned model (the judge-as-target risk per [phase-e.md "Open risks"](phase-e.md#open-risks) — optimizing toward judge preferences instead of parent preferences is the failure mode; mitigation: parent signal weighted higher in SFT label composition; periodic agreement audit). Trained adapters live under `data/models/lora/<timestamp>/` (gitignored); registry doc `data/models/lora/REGISTRY.md` (tracked) records active version + base + training data window + measured deltas + PII-filter version (so future audits can re-derive the corpus). RunPod documented as cloud burst for any run that won't fit on the home GPU; escalation criterion + API-key sourcing in `documentation/operator/local-runtime.md` from Step 25a.
- **Type:** code (with operator-driven training run)
- **Issue:** #39
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step 26 (#38 — local adapter shipped + baseline measured); eval-fixtures baseline current. No calendar gate, no row-count gate (both dropped — ≥50-row gate cut 2026-05-21); operator runs `uv run python -m toybox.ai.labeled_events --count --since <ISO> --sft-filter` for situational awareness only.
- **Done when:** Migration 0013 lands + tested forward + idempotent; `eval_dump.py --sft-export` produces redacted ChatML JSONL with PII scrubbed (verified by unit test that injects synthetic PII strings and asserts redaction); `scripts/train_lora.py` runs end-to-end on a real corpus, produces a merged GGUF adapter, exits 0; `data/models/lora/REGISTRY.md` updated with the new adapter row including PII-filter version; eval re-run report at `documentation/eval-runs/<date>-<NN>-claude-vs-local-sft1.md` shows per-dimension delta vs base + judge-parent agreement audit.

#### Step 28: E4 — Tool-loop refactor

- **Problem:** Refactor the generator interface to accept a tool registry (`src/toybox/ai/tools.py`) exposing `get_persona(persona_id)`, `get_room(room_id)`, `get_inventory(child_id, recency_window)`, `get_recent_transcript(window_sec)`, `get_prior_steps(activity_id)`, `get_anti_signal(template_id, slot_dict)`. **Tool args validated via Pydantic schemas BEFORE dispatch** (per §"Design decisions"): UUIDs for `room_id`/`persona_id`/`activity_id`/`child_id`, slug regex for library persona ids, bounded ints for window/limit args. Validation failures return a structured recovery error to the model loop (`{error: "invalid_args", tool: "get_room", reason: "room_id must be UUIDv4"}`) so the model re-issues with valid args rather than crashing. Both adapters (`src/toybox/ai/adapters/claude.py`, `src/toybox/ai/adapters/local.py`) implement loop-mode entry points; single-shot path retained behind `TOYBOX_GENERATOR_MODE=single` flag for fallback. **Two orthogonal env axes wired:** `TOYBOX_GENERATOR_ADAPTER=claude|local` and `TOYBOX_GENERATOR_MODE=single|loop`; default `claude+single` (= v1 behavior). All four cells covered by integration tests (see Appendix §"Adapter × mode matrix"). **`is_local_capable()` extension to `src/toybox/ai/capability.py`** — HTTP GET to `<TOYBOX_LOCAL_RUNTIME_URL>/v1/models` (or runtime-specific equivalent), asserts the chosen model id is in the response, breaker per-adapter so Claude breaker tripping doesn't disable local and vice versa. Tool resolution is cancellation-safe and capped via `TOYBOX_TOOL_LOOP_TIMEOUT_SEC` (default 30 s aggregate per generation). **Tool-call telemetry captured per turn into `labeled_events.tool_calls` JSON sub-array** — shape pinned in Appendix §"Tool-call telemetry shape" (`{tool: str, args: dict, result_summary: str, latency_ms: int, error: str | null, ts: ISO}`); no migration needed since `labeled_events` already has free-form JSON columns. Eval suite re-run; regressions flagged. Latency budget documented (loop generation must complete within Nx single-shot, N tunable via `TOYBOX_LOOP_LATENCY_BUDGET_X` default 3); prompt cache strategy documented (which tool results are cacheable, which aren't — `get_persona`/`get_room`/`get_inventory` cacheable per-activity; `get_recent_transcript`/`get_prior_steps`/`get_anti_signal` re-resolve per turn).

  **Carve-out for autonomous build:** the *Claude-only* parts of this step — tool registry interface, args validation in `tools.py`, `ClaudeActivityGenerator` wrapper, `TOYBOX_GENERATOR_ADAPTER` + `TOYBOX_GENERATOR_MODE` flag plumbing, `is_local_capable()` (with stub probe returning `False` until E1c lands), `labeled_events.tool_calls` sub-array — can land *before* E3 since the master plan's "don't stack tool-loop latency on an unproven model" rationale ([phase-e.md "Sequencing rationale"](phase-e.md)) only applies once the local adapter is the active loop path. The local-adapter loop-mode implementation stays gated on E1c+E2+E3. When pulled in early, this step becomes the natural "long-unattended autonomous build" target while E1-E3 wait on hardware + telemetry availability — the work is pure code refactor, fully unit/integration tested, no UI gate.
- **Type:** code
- **Issue:** #40 (carve-out, closed 2026-05-05) + #43 (full-step follow-up)
- **Flags:** --reviewers code
- **Status:** CARVE-OUT DONE (2026-05-05, commit `33a4b3c`); full-step PENDING (gated on #36/#37/#38/#39)
- **Depends on:** Step 27 (#39) for the full step (local adapter loop-mode); the carve-out landed before E1 (Claude wrapper + tool registry + flag plumbing + `is_local_capable()` stub + `labeled_events.tool_calls` + operator CLI all shipped)
- **Parallel-safe with:** Step 25a (#35), 25b (#36), 25c (#37), 26 (#38), 27 (#39) — but ONLY for the Claude-only carve-out scope (tool registry, ClaudeActivityGenerator wrapper, flag plumbing, capability stub, telemetry sub-array). The full-step local-adapter implementation is strictly sequential after Step 27. Operator picking the autonomous-friendly target should bound `/build-step` scope to the carve-out via the issue body's explicit checklist.
- **Done when (carve-out):** `src/toybox/ai/tools.py` + `src/toybox/ai/adapters/{__init__,claude}.py` shipped; tool args validated by Pydantic schemas with structured recovery errors on invalid args (covered by unit tests using crafted bad-args inputs); `TOYBOX_GENERATOR_ADAPTER` + `TOYBOX_GENERATOR_MODE` env vars wired through generator dispatch; `is_local_capable()` stub returns False with cause "local runtime not yet installed" until E1c lands; ClaudeActivityGenerator passthrough test (single-shot output structurally equal to direct `client.py` output for same context) green; `labeled_events.tool_calls` sub-array populated on all loop-mode generations (covered by integration test).
- **Done when (full step, after E3):** local-adapter loop-mode shipped + integration tested against the live local runtime; all four adapter×mode cells covered by integration tests; latency budget enforced + prompt cache strategy documented in code + commit.

#### Step 29: E5 — Non-linear gameplay (SUPERSEDED 2026-05-10)

- **Status:** SUPERSEDED 2026-05-10 by Phase G. Phase G shipped multi-option choice steps + variable-length activities on the offline-template path; the variable-step shape and terminal-node semantics this step would have introduced for the LLM path now have a working precedent in the template path. Residual sub-pieces that are still unshipped — parent regenerate-from-here mid-activity, transcript reaction window, `activity.step_pending` WS event for streaming generation — are latency accommodations for local inference, not gameplay shape; pull them into Step 28 (E4 tool-loop) if they prove necessary during local-adapter integration, otherwise defer to a future small phase. Original problem text preserved below for historical reference only.
- **Original (pre-supersede) Problem:** Drop the hard-coded 5-step shape. Migration 0014 (renumbered forward from earlier-planned 0006 — the play-queue + transcript-retention migrations (0010–0012) and E3's `redact_for_sft` (0013) shipped first; Step 28 carve-out's `labeled_events.tool_calls` took 0004) adds `is_complete INTEGER NOT NULL DEFAULT 0` to `activity_steps`. Pydantic `Activity.steps` loosens from `Field(min_length=5, max_length=5)` to `Field(min_length=1, max_length=<TOYBOX_MAX_STEP_CAP default 8>)`; `ActivityStep` gains `is_complete: bool`. Generator emits one step at a time, observes parent's pause/regenerate-from-here/end signals plus a transcript reaction window (default `TOYBOX_REACTION_WINDOW_SEC=10`, cap 30 — short default chosen because the kid is waiting; cap exists so an attentive parent can pause longer without timing out the loop). Child UI handles dynamic step count with a "..." indicator while next step generates. **New WS event `activity.step_pending`** emits between steps while generation is in flight — payload pinned in Appendix §"WS payloads for streaming generation". Eval rubric updated: `coherence` now scores arc across actually-emitted steps, not against an assumed-5 baseline.
- **Type:** full-stack
- **Issue:** #41 (close as superseded)
- **Flags:** n/a
- **Depends on:** n/a (superseded)

#### Step 30: E6 — Preference-pair RL (optional / gated)

- **Problem:** DPO or GRPO loop over parent-signal preference pairs `(input, preferred_activity, rejected_activity)` extracted from `labeled_events`. **Hard gate:** only proceeds if E3-E5 leave a measurable gap to Claude that SFT alone has not closed. Criterion per [phase-e.md "E6"](phase-e.md): mean-dimension-score delta >0.5 on the held-out fixture set after **two** SFT iterations (i.e. E3 ran twice with different telemetry windows; both runs report deltas; if both still >0.5 below Claude, RL is on the table). Decision documented in `documentation/rl-decision.md` whether to proceed BEFORE any training begins. If skipped, document why (most likely: SFT closed the gap to within tolerance; RL adds risk + complexity that the measured numbers don't justify). Algorithm choice (DPO vs GRPO) recorded with rationale — DPO is simpler and the default; GRPO only if DPO is unstable on the available preference-pair corpus size. Trained adapters again live under `data/models/lora/<timestamp>/`; registry updated. Eval comparison re-run; agreement audit re-checked.
- **Type:** code (with operator-driven training run)
- **Issue:** #42
- **Flags:** --reviewers code
- **Status:** PENDING (hard-gated — may be permanently skipped)
- **Depends on:** Step 27 (#39) ran twice with two distinct telemetry windows (data-volume gate per Step 27 — no calendar minimum). Step 29 was superseded by Phase G; the gap measurement uses single-shot 5-step generation as the comparison shape, since the variable-step path now lives in the offline-template lane (Phase G) rather than in the LLM lane.
- **Done when:** `documentation/rl-decision.md` landed with the gap measurement + decision; if proceeding: DPO/GRPO loop runs end-to-end on the preference-pair corpus; trained adapter merged + deployed + eval re-run; per-dimension delta vs SFT-only adapter reported; `data/models/lora/REGISTRY.md` updated with the new adapter. If skipped: decision doc explains why (most likely "SFT closed the gap to within tolerance") and Phase E formally closes.

## Risks and open questions

| Item | Risk | Mitigation | Source |
|---|---|---|---|
| Triton-on-Windows for Unsloth | Pinned to specific PyTorch + CUDA combos; breaks on RTX 50-series and on PyTorch bumps | Snapshot venv after a working install; Llama-Factory as documented fallback in `train_lora.py` | [phase-e.md "Open risks"](phase-e.md#open-risks) |
| Constrained decoding vs creative writing | Strict GBNF schema + persona-driven step text often conflict at 3B-7B scale | A/B raw-prompted JSON vs constrained on a 100-prompt eval set during E2 before committing | [phase-e.md "Open risks"](phase-e.md#open-risks) |
| Judge-as-target in SFT | Optimizing toward judge preferences instead of parent preferences is the failure mode | Parent signal weighted higher in SFT label composition; periodic judge-vs-parent agreement audit on the overlap | [phase-e.md "Open risks"](phase-e.md#open-risks) |
| Tool-loop latency stack | Multi-turn generation + tool resolution on a 7B model on consumer hardware can stack to 10s+ per activity | Prompt cache aggressively in E4 (`get_persona`/`get_room` cacheable per-activity); `TOYBOX_MAX_STEP_CAP` enforced; "regenerate from here" remains parent escape hatch | [phase-e.md "Open risks"](phase-e.md#open-risks) |
| VRAM ceiling at 7B with KV cache | Quoted ~5 GB weight is misleading; working set creeps toward 7-8 GB at 4 K context | Documented in E1 decision doc; 3B fallback is real, not theoretical; cloud-burst fallback documented | [phase-e.md "Open risks"](phase-e.md#open-risks) |
| Eval-fixtures baseline still placeholder | Step 26 (E2) cannot run without a real Claude baseline | Operator runs `uv run python -m toybox.ai.eval_run --judge claude` once with real OAuth before E2 starts; gate is `baseline_scores.json` content check | [memory MEMORY.md:233](../../../../.claude/projects/c--Users-abero-dev/memory/MEMORY.md) |
| Telemetry volume awareness for E3 | <200 tuples produces an under-determined SFT corpus; risk of overfitting to small-N quirks | No hard phase-gate (calendar gate dropped 2026-05-10; ≥50 SFT-row gate dropped 2026-05-21 as not load-bearing). Ideal sample target ≥200 tuples; training driver still refuses on a 0-row corpus (defensive empty-corpus guard). Operator owns the call on when row count is sufficient | [phase-e.md](phase-e.md) |
| Adapter symmetry indirection | Adding `ClaudeActivityGenerator` wrapper introduces one indirection layer atop existing client | Wrapper is genuinely thin (no logic, just dispatch); covered by passthrough unit test asserting wrapper's output equals direct `client.py` output for the same context | this doc §"Design decisions" |
| Schema migration 0014 (E5) vs in-flight activities at upgrade time | Existing rows default to `is_complete=0` which is semantically wrong for already-completed historical activities | Acceptable noise floor — `is_complete` only consumed by the loop-mode dynamic-step path; single-shot path never reads it; documented at the migration site | this doc §"Scope" |
| Tool-args injection from model output | Fine-tuned model could emit crafted tool args (e.g. `room_id="../../etc/passwd"`); naive resolver would honor it | Pydantic schemas in `tools.py` validate every tool arg before dispatch; structured recovery error (`{error: "invalid_args", ...}`) returned to model loop on violation; covered by adversarial unit tests | this doc §"Design decisions" |
| PII in SFT training corpus | Transcript fields in labeled_events inputs may contain child names, addresses; SFT exposes the model to all of it | `eval_dump.py --sft-export` applies a deny-list + regex redaction pass before JSONL emission; new `redact_for_sft` column (migration 0013) lets operator opt rows out manually; PII-filter version recorded in REGISTRY.md so future audits can re-derive | Step 27 §"Problem" |
| Empty-corpus crash on E3 | If SFT filter returns 0 rows, training crashes with confusing PyTorch error mid-run | Defensive 0-row guard in the training driver — actionable error directs operator to either accumulate more telemetry or relax the filter (the ≥50-row phase-gate was dropped 2026-05-21; only the empty-corpus guard remains) | Step 27 §"Done when" |
| Adapter env-var conflation | Conflating mode (single/loop) with adapter (Claude/local) blocks the carve-out shipping cleanly and leaves 2 of the 4 cells unnamed | Two orthogonal env vars; all four cells covered by integration tests; default `claude+single` matches v1 so no v1-behavior regression | this doc §"Design decisions" |
| Local-runtime capability gate missing | Without `is_local_capable()`, loop-mode dispatch has nothing to fall back to when the local runtime is down | `capability.py` extended with HTTP health + model-loaded probe; per-adapter breaker so Claude breaker tripping doesn't disable local and vice versa; carve-out ships with a stub returning False until E1c lands | this doc §"Design decisions" |
| Constrained-decoding output rejection | Grammar parser violations or schema mismatches mid-stream would otherwise crash the kid-facing path | Single retry with stricter grammar; on second failure, fall back to existing offline template path with logged WARNING (200-char violation excerpt); offline path is the v1 breaker-open floor that already handles this class of failure | Step 26 §"Problem" |
| Eval-runs filename collision | Multiple A/B runs in one day overwrite each other under `<date>-claude-vs-local.md` | Convention `<YYYY-MM-DD>-<NN>-claude-vs-<adapter>.md` with zero-padded run-of-day counter; first run is `01` | Step 26 §"Problem" |
| RunPod cloud-burst setup undefined | API key sourcing, billing model, escalation criteria not pinned anywhere | Operator doc `documentation/operator/local-runtime.md` carries the procedure (E1a deliverable); escalation criterion: 7B fails benchmark AND 3B fails benchmark | Step 25a §"Problem" |
| Local-runtime port collision | Local runtime + backend (`:8000`) + frontend (`:4000`) all bind on localhost; default port collisions plausible | Per-runtime defaults pinned in `.env` example: ollama=11434, lmstudio=1234, llamacpp=8080; all distinct from backend/frontend | this doc §"Impact analysis" |

## Testing strategy

**Unit tests (every step):**
- E1b: `local.py --probe` exercised in CI via a fake-runtime HTTP server fixture (no real GGUF needed); CLI argparse + marker-file-write paths tested
- E1c: benchmark CLI argparse + decision-doc-write unit-tested with stub measurements; "probe-marker stale" branch tested by mutating marker timestamp
- E2: `LocalActivityGenerator` against an in-memory fake runtime with deterministic outputs; constrained-decoding shape assertions; **grammar-rejection retry+offline-fallback path** tested by injecting a parser violation; `eval_compare.py` math (mean delta, bootstrap CI) tested with seeded fixtures; eval-runs filename counter (NN suffix) tested by simulating 2 runs in one day
- E3: `train_lora.py` argparse + ChatML JSONL filtering logic + adapter-merge dry-run path; real Unsloth invocation gated `@pytest.mark.requires_gpu`; **PII redaction pass** tested by injecting synthetic PII strings into transcript inputs and asserting redaction output; **empty-corpus floor** tested by running with N=49 rows and asserting actionable error
- E4: tool registry call/cancel/timeout paths; **tool-args validation** tested with adversarial inputs (path-traversal `room_id`, oversized strings, wrong types) asserting structured recovery errors; adapter loop-mode contract tests (both adapters answer the same context with structurally-equivalent outputs); **adapter passthrough test** asserting `ClaudeActivityGenerator(ctx).generate_activity()` output structurally equals direct `client.py` output for the same context (proves the wrapper is a no-op layer); telemetry sub-array shape per Appendix; `is_local_capable()` tested with stub HTTP fixture (200-with-model, 200-without-model, timeout, refused) covering all breaker branches
- E5: migration 0014 forward + idempotence (renumbered forward from earlier-planned 0006 since play-queue + transcript-retention migrations (0010–0012) and E3's 0013 shipped first); Pydantic shape change with min/max length boundaries; loop-mode emit-one + observe + emit-next with stubbed signals; offline-path-stays-5-step regression; **WS `activity.step_pending` payload shape** asserted against the Appendix definition; reaction window default + cap honored
- E6: DPO/GRPO loss math; preference-pair extraction filter; decision-doc-write path

**Integration tests:**
- E2: live Claude vs live local A/B over 5 fixture IDs (gated `@pytest.mark.requires_claude` + `@pytest.mark.requires_gpu`)
- E4 (carve-out): full activity generation in loop mode via `ClaudeActivityGenerator` end-to-end, asserting tool_calls sub-array populated, latency under budget, output structurally identical to single-shot. **All four adapter×mode cells** exercised — `claude+single` (= v1 baseline), `claude+loop` (= carve-out output), `local+single` (= E2 output, deferred to post-E2), `local+loop` (= post-E3 final state)
- E5: full activity generation with dynamic step count, simulating parent pause + regenerate-from-here mid-stream; verify `is_complete` semantics across all step transitions; child UI dynamic rendering via Vitest + React Testing Library; WS `activity.step_pending` end-to-end (server emits, child UI subscribes, "..." indicator renders)
- E5 smoke: in loop mode, generate 1 activity from the eval fixtures, assert all expected_steps emitted without crash, assert final step has `is_complete=1`, assert activity state transitions to `completed`

**Eval regression:**
- Every step from E2 onward re-runs the held-out fixture set and checks that mean dimension scores haven't dropped >0.5 from baseline. The CI eval-regression gate is no-op until the operator populates `baseline_scores.json` (Step 26 gate).
- E5 updates the `coherence` rubric anchor to score arc across actually-emitted steps; re-baselining of coherence-only is acceptable at that step boundary, documented in the migration commit.

**Existing tests that may break:**
- `Activity.steps` `min_length=5, max_length=5` is asserted in current test fixtures; E5 loosens this and updates fixtures accordingly. Look for `min_length=5` / hard-coded `len(activity.steps) == 5` in `tests/unit/activities/` and `tests/integration/test_*.py` — grep before E5 lands.
- `activity_steps` schema tests in `tests/integration/migrations/test_0001_initial.py` — E5 migration adds 0014 test in the same pattern; E3 adds 0013 test. (Step 28 carve-out's `tests/integration/migrations/test_0004_labeled_events_tool_calls.py` already shipped on 2026-05-05.)
- Tool-loop refactor (E4) changes the `generator.generate_activity(ctx)` call signature minimally (adds optional `tools` kwarg defaulting to None for single-shot). Callers that pass positional args may need updating; grep `generate_activity(` before E4 lands.
- `eval_dump.py` ChatML JSONL output now applies PII redaction in `--sft-export` mode — existing tests that snapshot the export output will need re-baselining with the redacted shape.

**End-to-end verification:**
- E5 ships under the autonomous-build operating mode (`--reviewers code`), so visual UI verification batches into a Phase-E bundled UI test pass (working name: M2.6) — separate from the v1 [Manual M2.5](phase-d-uat-m2.5.md) which is scoped to steps 16/17/18/21/22/23/24 and has already been carved out. Operator must exercise: dynamic step count rendering, "..." indicator timing, regenerate-from-here mid-stream, max-step-cap enforcement, offline-path-still-5-step.
- Manual M6 (Local model TPS check) folds into Step 25c's benchmark + decision doc; no separate manual run.

## Operator pre-flight before kicking off any Phase E work

1. Check `labeled_events` row count via `uv run python -m toybox.ai.labeled_events --count` (CLI shipped as part of Step 28's carve-out — autonomous-friendly to add early). Ideal sample target is ≥200 tuples; no hard phase-gate (≥50 SFT-row gate dropped 2026-05-21). Operator runs `--count --since <ISO> --sft-filter` for situational awareness on filter pass-rate.
2. Run eval-fixtures baseline regen with real OAuth: `uv run python -m toybox.ai.eval_run --judge claude` and verify `tests/fixtures/eval/baseline_scores.json` no longer matches the placeholder.
3. Confirm CUDA toolkit installed + GPU visible to PyTorch: `uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`.
4. Confirm at least 30 GB free under `data/models/` for both GGUFs + LoRA adapters.
5. (RunPod-only, if home GPU benchmark fails) RunPod API key sourced + billing alert configured per `documentation/operator/local-runtime.md` Appendix.

The autonomous-friendly carve-out from Step 28 (Claude-only tool-loop scaffolding) does NOT require any of the above pre-flight items — only E1-E3+local-adapter work does.

## Appendix

### Adapter × mode matrix

| Adapter | Mode | Status | Notes |
|---|---|---|---|
| `claude` | `single` | ✅ v1 default | Existing single-shot Claude path; default if no env vars set; no migration needed |
| `claude` | `loop` | Step 28 carve-out | First loop-mode shipping config; lands BEFORE E1-E3 as autonomous build target; uses Claude prompt cache for tool results |
| `local` | `single` | Step 26 (E2) | First local-inference shipping config; constrained decoding via Outlines or GBNF; gated on E1c decision doc + eval-fixtures baseline |
| `local` | `loop` | Step 28 (full) | Post-E3 target; the privacy-first end-state of Phase E; gated on E1+E2+E3+adapter symmetry |

Operator switches via `TOYBOX_GENERATOR_ADAPTER` and `TOYBOX_GENERATOR_MODE` env vars at startup; runtime switching is out of scope for v1.5 (would require per-request env overrides + breaker per-cell, deferred).

### Tool-call telemetry shape

`labeled_events.tool_calls` is a JSON array (existing free-form column on the table — no migration needed) containing one object per tool call within a generation:

```json
[
  {
    "tool": "get_room",
    "args": {"room_id": "550e8400-e29b-41d4-a716-446655440000"},
    "result_summary": "kitchen — features: counter, fridge, sink",
    "latency_ms": 12,
    "error": null,
    "ts": "2026-05-12T14:30:01.234Z"
  },
  {
    "tool": "get_anti_signal",
    "args": {"template_id": "play_anytime_invent", "slot_dict": {"toy": "lego"}},
    "result_summary": "no anti-signal hits",
    "latency_ms": 4,
    "error": null,
    "ts": "2026-05-12T14:30:01.250Z"
  }
]
```

Field semantics:
- `tool` — exact registered tool name from `src/toybox/ai/tools.py`
- `args` — arg dict AFTER Pydantic validation (rejected args never appear here; rejection is logged to `error` on a separate row)
- `result_summary` — short human-readable summary of the result (full result not stored — would bloat labeled_events; the model's downstream use of the result is captured by the activity it ultimately produces)
- `latency_ms` — wall-clock latency of the tool call (resolver execution time, NOT including model inference)
- `error` — null on success; on failure, structured error code matching the recovery shape returned to the model (`"invalid_args:room_id_not_uuid"`, `"timeout"`, `"db_error"`)
- `ts` — ISO 8601 UTC timestamp

### WS payloads for streaming generation

Phase A established `activity.state` as the canonical activity-lifecycle topic. E5 adds a parallel `activity.step_pending` topic that fires while the loop is generating the next step:

```json
{
  "topic": "activity.step_pending",
  "activity_id": "11111111-2222-3333-4444-555555555555",
  "current_step_seq": 3,
  "expected_max_steps": 8,
  "generator_started_at": "2026-05-12T14:30:00.123Z"
}
```

Field semantics:
- `current_step_seq` — the seq of the most recently emitted step (so the UI knows which "..." position to render)
- `expected_max_steps` — `TOYBOX_MAX_STEP_CAP` value at generator-start time (the UI can show progress like "step 3 of up to 8")
- `generator_started_at` — when the next-step generation began; child UI uses this to compute spinner timing

The event fires once per step boundary; if generation completes inside the latency budget, no `step_pending` is emitted (the next `activity.state` envelope carrying the new step shows up directly). The event is suppressed for offline-template activities (those run synchronously through the 5-step path with no streaming).

### Upstream documentation pointers

Useful links for fresh-context work on this phase (saves search round-trips):

- Outlines (constrained decoding library): https://dottxt-ai.github.io/outlines/
- llama.cpp GBNF grammar spec: https://github.com/ggerganov/llama.cpp/blob/master/grammars/README.md
- Unsloth (LoRA SFT, Windows-native): https://docs.unsloth.ai/
- Llama-Factory (Unsloth fallback): https://github.com/hiyouga/LLaMA-Factory
- DPO paper (Direct Preference Optimization): https://arxiv.org/abs/2305.18290
- GRPO paper (DeepSeek R1, group-relative): https://arxiv.org/abs/2402.03300
- Qwen2.5 model card: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct
- Ollama docs: https://github.com/ollama/ollama/blob/main/docs/api.md
- LM Studio API docs: https://lmstudio.ai/docs/api
