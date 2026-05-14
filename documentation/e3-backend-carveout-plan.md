# Phase E Step 27 (E3) backend carve-out — PII redaction + SFT export substrate

> **Scope:** Land the operator-zero-touch backend prerequisites for Phase E Step 27 (E3 SFT iteration) so when the `labeled_events` row count crosses the SFT floor (≥50 rows passing filter), no further plumbing is needed. All steps are `--reviewers code`, all backend, no local model required. Designed to run overnight as an autonomous `/build-phase` target. Companion to the Step 28 carve-out (shipped 2026-05-05, commit `33a4b3c`).
>
> **Run command:** `/build-phase --plan documentation/e3-backend-carveout-plan.md`
> **Branch base:** start from `master` at commit `eac19e2` (the pre-flight cleanup; see §"Existing Context"). `/build-phase` will create a worktree per step.

## Terms (one-line glossary)

- **SFT** — Supervised Fine-Tuning. The post-pretraining stage that adapts a base LLM to a domain by training on `(prompt, ideal_completion)` pairs. Phase E uses SFT to specialize a local model on parent-approved activity generations.
- **LoRA** — Low-Rank Adaptation. A parameter-efficient fine-tuning method that trains a small set of additive matrices rather than the full model weights; output is an "adapter" merged into the base at inference.
- **PII** — Personally Identifiable Information. Names, phone numbers, addresses, emails. Must be scrubbed from any training corpus that may leave the home machine.
- **ChatML** — A messages-array format (`[{"role": "system|user|assistant", "content": "..."}, ...]`) used by OpenAI/Qwen/Llama-family SFT tooling. The `inputs_chatml_json` column stores the prompt side; the SFT export appends the assistant turn (from `activity_json`) to form a complete fine-tuning example.
- **JSONL** — JSON Lines. Newline-delimited JSON; one complete JSON object per line. The SFT export emits one JSONL line per training example.
- **NANP** — North American Numbering Plan. The 10-digit phone-number format covered by the redactor's phone regex.
- **ASR** — Automatic Speech Recognition. Toybox uses `faster-whisper` for transcripts; proper nouns are typically title-cased by Whisper, which is why title-case-only matching catches most ASR-produced child-name mentions.
- **DDL** — Data Definition Language. The SQL subset that defines schema (`CREATE TABLE`, `ALTER TABLE`); contrasted with DML (`INSERT`, `UPDATE`, `DELETE`).

## 1. What This Feature Does

Adds the four backend substrates that E3 (SFT iteration) needs but doesn't strictly require a trained model to ship:

1. A `redact_for_sft` opt-out column on `labeled_events` so operators can flag rows known to contain unredactable PII without losing them from the audit log.
2. A pure-function PII scrubber (`src/toybox/ai/redact.py`) that deny-lists child names (title-case + word-boundary only) and regex-scrubs phone numbers, addresses, and emails.
3. A new `eval_dump.py --sft-export` mode that bundles the existing SFT-quality filter with PII redaction and the `redact_for_sft=0` predicate, producing a training-ready ChatML JSONL.
4. A `data/models/lora/REGISTRY.md` template so when the first real adapter trains, the registry shape is already there with a slot for the PII-filter version that produced its corpus.

Built ahead of the model work because (a) the redaction filter is the long-pole audit risk — kid-facing PII in a training corpus is unrecoverable — and (b) the substrate is pure code with no hardware dependency, making it ideal for an unattended autonomous build run.

## 2. Existing Context

- **`labeled_events` table** holds every generation since Phase D (one row per Claude/offline generation). Schema in [migrations/0003_labeled_events.sql](../src/toybox/db/migrations/0003_labeled_events.sql); Step 28 added a nullable `tool_calls` JSON column ([migrations/0004_labeled_events_tool_calls.sql](../src/toybox/db/migrations/0004_labeled_events_tool_calls.sql)). After this carve-out's migration 0013 lands, the full column list is:

  ```sql
  id                    INTEGER PRIMARY KEY AUTOINCREMENT
  activity_id           TEXT NOT NULL          -- UUIDv4; logical FK to activities.id (no constraint; outlives parent)
  generated_at          TEXT NOT NULL          -- ISO8601 UTC "YYYY-MM-DDTHH:MM:SSZ"
  generator_path        TEXT NOT NULL          -- CHECK IN ('claude','offline','local')
  inputs_chatml_json    TEXT NOT NULL          -- ChatML messages array, sort-keys JSON (system+user)
  activity_json         TEXT NOT NULL          -- Pydantic Activity model dumped to JSON
  parent_signal         REAL                   -- NULL until parent acts; 1.0 / -0.5 / -1.0
  parent_signal_set_at  TEXT                   -- ISO8601 when signal was recorded
  ended_at_step         INTEGER                -- NULL unless end-early
  judge_scores_json     TEXT                   -- NULL unless 1-in-N judge sample fired
  judge_run_at          TEXT                   -- ISO8601 when judge ran
  tool_calls            TEXT                   -- NULL for single-shot rows; JSON array for loop-mode (post-Step 28)
  redact_for_sft        INTEGER NOT NULL DEFAULT 0  -- ADDED BY THIS CARVE-OUT (migration 0013)
  ```

  Unique index on `activity_id`; composite index on `(generated_at, generator_path)`. Step 5's smoke gate seeds rows via `record_generation()` (at [labeled_events.py:212](../src/toybox/ai/labeled_events.py#L212)) — that function owns `id` / `generated_at` / `tool_calls` insertion semantics; raw `UPDATE` is fine for setting `redact_for_sft=1` on a seeded row.
- **`eval_dump.py`** ([src/toybox/ai/eval_dump.py](../src/toybox/ai/eval_dump.py)) already ships ChatML JSONL export with the SFT-quality filter baked into the default path: `safety>=4 AND mean_quality>=3.5 AND parent_signal != -1`. `--all` opts out of the filter. PII redaction is the new layer. Each JSONL line has this envelope:

  ```json
  {
    "messages": [
      {"role": "system",    "content": "<persona card + rubric guardrails>"},
      {"role": "user",      "content": "<intent + slot + persona + inventory + transcript window>"},
      {"role": "assistant", "content": "<activity_json verbatim>"}
    ],
    "metadata": {
      "activity_id":          "<UUIDv4>",
      "generated_at":         "<ISO8601>",
      "generator_path":       "claude | offline | local",
      "parent_signal":        -1 | -0.5 | 0 | 1 | null,
      "parent_signal_set_at": "<ISO8601>" | null,
      "ended_at_step":        <int> | null,
      "judge_scores":         { schema, age_appropriateness, doability, persona_fidelity, coherence, safety } | null,
      "judge_run_at":         "<ISO8601>" | null
    }
  }
  ```

  Step 3 adds `metadata.pii_filter_version: "1.0"` (or current value) when `--sft-export` is set. No other envelope fields change. The redactor scrubs the `user` message content + the `assistant` content (i.e. `activity_json`); the `system` message and all `metadata` fields stay verbatim.
- **`labeled_events.py`** ([src/toybox/ai/labeled_events.py:482-542](../src/toybox/ai/labeled_events.py#L482-L542)) has `count_rows()` + a `--sft-filter` CLI flag that already implements the quality filter and **carries a TODO at [labeled_events.py:504-507](../src/toybox/ai/labeled_events.py#L504-L507) saying "extend when redact_for_sft lands"** — that extension is part of this carve-out.
- **Migration runner** ([db/migrations/__init__.py](../src/toybox/db/migrations/__init__.py)) wraps every migration file in a single BEGIN/COMMIT transaction. Migration files MUST NOT contain their own `BEGIN`/`COMMIT`/`ROLLBACK`.
- **Migration numbering is hot:** 0005 through 0012 are taken by `toy_actions`, `activity_step_action_slot`, `activity_step_choices`, `activity_slot_fills`, `banned_themes_global`, `transcript_retention`, `play_target_depth`, `play_cadence_seconds`. The next free number is **0013**. The phase-e.md plan reserves 0005 — that's stale and needs to be updated in the same PR that lands this carve-out.
- **`children` table** holds child profiles with a `display_name` column; the PII redactor sources its deny-list from here at scrub time.
- **Generator paths** that produce labeled_events rows have NULL `tool_calls` for single-shot (the v1 path) and a JSON array for loop-mode (post-Step 28). Both paths share the same `inputs_chatml_json` + `activity_json` columns — the redactor scrubs both.
- **Pre-flight cleanup landed** at commit `eac19e2` (`chore: drop dead eval-fixtures.md references`) — four files (`eval_run.py`, `rubric.py`, `holdout.json`, `tests/fixtures/eval/README.md`) had their pointers to the retired `documentation/eval-fixtures.md` removed. Not part of this carve-out; mentioned only so a fresh model reading the plan after `/build-phase` kickoff knows that branch base.

## 3. Scope

**In:**

- New SQLite migration `0013_labeled_events_redact_for_sft.sql` — `ALTER TABLE labeled_events ADD COLUMN redact_for_sft INTEGER NOT NULL DEFAULT 0`.
- Extend `labeled_events.py` `count_rows()` + `--sft-filter` CLI to require `redact_for_sft = 0`. Remove the forward-compat TODO comment.
- New module `src/toybox/ai/redact.py`:
  - `PII_FILTER_VERSION` constant (semver-string, starts at `"1.0"`).
  - `redact_pii(text: str, *, child_names: Sequence[str]) -> str` — pure function, no I/O.
  - Title-case + word-boundary child-name scrub.
  - Regex scrubs: phone numbers (NANP `\d{3}[-.\s]?\d{3}[-.\s]?\d{4}`), addresses (street-suffix pattern), email addresses.
  - Substitution token: `[REDACTED]` (fixed; not configurable).
- Extend `eval_dump.py` with `--sft-export` flag: bundles SFT filter + PII redaction (over `inputs_chatml_json` user message + `activity_json` content) + `redact_for_sft=0` predicate. Sources `child_names` from `children.display_name` at run time.
- New file `data/models/lora/REGISTRY.md` — markdown template with columns: `adapter | base | training_data_since | training_data_until | row_count | pii_filter_version | measured_delta_summary | deployed_at | active`. Initial body has the header row + an empty-state note.
- `data/models/lora/.gitkeep` — track the dir; `.gitignore` adjustment to ignore `data/models/lora/*/` (trained adapter subdirs) while preserving `REGISTRY.md` + `.gitkeep`.
- Unit tests for the redactor (synthetic PII inputs → assert scrubbed output) under `tests/unit/ai/test_redact.py`.
- Unit tests for the new migration (forward-only idempotency, default=0) under `tests/integration/migrations/test_0013_labeled_events_redact_for_sft.py`.
- Integration smoke test (`tests/integration/test_eval_dump_sft_export.py`) that seeds a temp DB with 3 representative `labeled_events` rows + 2 child profiles and asserts the `--sft-export` output is: (a) the right row count, (b) PII-free in both user and assistant content, (c) excludes `redact_for_sft=1` rows.

**Out:**

- The local model itself (LocalActivityGenerator) — gated on Phase E1c hardware install.
- Actual SFT training run — gated on ≥50 rows passing the filter in production.
- `scripts/train_lora.py` — Unsloth LoRA training driver. Belongs to the full E3 ship.
- Cloud-burst RunPod operator runbook — belongs to E1a operator setup.
- `eval_compare.py` Claude-vs-local A/B harness — belongs to E2.
- Backwards compatibility for rows generated BEFORE this migration (the `DEFAULT 0` clause covers them; "include in SFT corpus by default" is the right semantic for historical rows since they were generated before PII concerns were flagged).
- Operator UAT — pure backend work, no UI surface; covered by the autonomous integration test.

## 4. Impact Analysis

| File / module | Nature | Notes |
|---|---|---|
| `src/toybox/db/migrations/0013_labeled_events_redact_for_sft.sql` | NEW | Adds `redact_for_sft INTEGER NOT NULL DEFAULT 0`. No own transaction (runner wraps). |
| `src/toybox/ai/labeled_events.py` | MODIFY | Extend `count_rows(sft_filter=True)` SQL clauses to include `redact_for_sft = 0`. Update `--sft-filter` CLI help text. Remove the forward-compat TODO comment at [L504-L507](../src/toybox/ai/labeled_events.py#L504-L507) and at [L569-L571](../src/toybox/ai/labeled_events.py#L569-L571), [L601-L603](../src/toybox/ai/labeled_events.py#L601-L603). |
| `src/toybox/ai/redact.py` | NEW | Pure-function PII scrubber. No I/O. `PII_FILTER_VERSION = "1.0"`. |
| `src/toybox/ai/eval_dump.py` | MODIFY | Add `--sft-export` flag. When set: apply existing filter + `redact_for_sft = 0` + scrub `inputs_chatml_json` user-message content + scrub `activity_json` content. Sources `child_names` from `SELECT display_name FROM children` at run time. Metadata fields stay untouched (no PII in metadata). Emit `pii_filter_version` into per-row metadata. |
| `data/models/lora/REGISTRY.md` | NEW | Template; tracked. Records adapter provenance + PII filter version per row. |
| `data/models/lora/.gitkeep` | NEW | Tracks the dir. |
| `.gitignore` | MODIFY | Add `data/models/lora/*/` (ignore trained subdirs); ensure `data/models/lora/REGISTRY.md` and `.gitkeep` stay tracked. |
| `tests/unit/ai/test_redact.py` | NEW | Unit tests with synthetic PII corpus. |
| `tests/integration/test_eval_dump_sft_export.py` | NEW | Integration: temp DB + seeded rows → assert clean JSONL output. |
| `tests/integration/migrations/test_0013_labeled_events_redact_for_sft.py` | NEW | Forward-only idempotency + default value. |
| `documentation/plan/phase-e.md` | MODIFY | Fix stale migration numbering: E3 is migration **0013** (not 0005); E5's `is_complete` is migration **0014** (not 0006) once it lands. Update Step 27 status to "BACKEND CARVE-OUT DONE (<date>)" with commit pointer once the build-phase merges. |

## 5. New Components

### `src/toybox/ai/redact.py`

Pure-function PII scrubber. No DB access; no logging; deterministic output for a given (text, child_names) pair.

```python
PII_FILTER_VERSION: Final[str] = "1.0"

def redact_pii(text: str, *, child_names: Sequence[str]) -> str:
    """Scrub PII from `text`. Order: child names → emails → phones → addresses.

    Child-name match is title-case + word-boundary only (\bName\b),
    intentionally conservative — `Sage` is redacted, `sage advice` is not.
    Each name is wrapped in `re.escape(name)` before pattern construction
    so display_names containing regex metacharacters (`.`, `[`, `(`, etc.)
    do not crash compilation or match unintended text.
    Emails: \S+@\S+\.\S+ → [REDACTED]
    Phones: NANP shape \d{3}[-.\s]?\d{3}[-.\s]?\d{4} → [REDACTED]
    Addresses: \d+\s+\w+\s+(St|Ave|Rd|...|Pl)\b → [REDACTED]
    """
```

Ordering matters: child names before emails (so a child name embedded in an email gets caught by the email pattern as a whole-token scrub, not as a fragment). `re.escape()` is non-negotiable — [src/toybox/db/slugs.py:14-25](../src/toybox/db/slugs.py#L14-L25) only validates the SLUGIFIED form of `display_name`, not the original character set; a child named `Sage [the great]` slugifies cleanly but stores brackets in the deny-list source.

No new dependencies — the redactor uses only the stdlib `re` module. Do NOT introduce `regex`, `pii-detection`, or similar libraries; the substrate is intentionally small and audit-readable.

### `src/toybox/db/migrations/0013_labeled_events_redact_for_sft.sql`

```sql
-- Phase E Step 27 (E3) backend carve-out — opt-out flag for rows that
-- contain unredactable PII. Default 0 means "include in SFT corpus" —
-- most rows train; operator manually sets =1 to exclude.
--
-- After this column lands, both eval_dump.py --sft-export and
-- labeled_events.py --sft-filter require redact_for_sft = 0.
--
-- Migration runner wraps in BEGIN/COMMIT — do NOT add own transaction.

ALTER TABLE labeled_events ADD COLUMN redact_for_sft INTEGER NOT NULL DEFAULT 0;
```

### `data/models/lora/REGISTRY.md`

````markdown
# LoRA adapter registry

One row per trained adapter. PII-filter version is the
`PII_FILTER_VERSION` constant from `src/toybox/ai/redact.py` at the time
the training corpus was exported. When the filter changes (e.g. new
regex, new deny-list source), bump the version and retrain.

Trained adapter directories live alongside this README as
`data/models/lora/<YYYY-MM-DDTHHMMSS>/` and are gitignored.

| adapter | base | training_data_since | training_data_until | row_count | pii_filter_version | measured_delta_summary | deployed_at | active |
|---|---|---|---|---|---|---|---|---|
| _(empty — first adapter lands with Phase E Step 27 full ship)_ | | | | | | | | |

## Operator: flag a row as PII-unredactable

When spot-checking a labeled_events row reveals PII the regex/deny-list didn't catch
(unusual address format, ASR-mangled name, etc.), flag the row instead of trying to
patch the redactor for a single case:

```bash
sqlite3 data/toybox.db "UPDATE labeled_events SET redact_for_sft=1 WHERE id=<row_id>;"
```

Once the redactor changes structurally (new pattern, new deny-list source), bump
`PII_FILTER_VERSION` in `src/toybox/ai/redact.py` and add a new row to this registry
when the next training corpus is exported under that version.
````

### `--sft-export` flag on `eval_dump.py`

```
--sft-export    Apply the Phase E SFT-export pipeline: existing
                quality filter + redact_for_sft=0 + PII redaction
                (child names from children.display_name; emails,
                phones, addresses by regex). Metadata fields are not
                scrubbed. Emits `pii_filter_version` into per-row
                metadata. Mutually exclusive with --all.
```

When `--sft-export` is set, the `_row_to_jsonl` path scrubs the user-message content within `inputs_chatml_json` (NOT the system message — it's persona/rubric template text only) and the full `activity_json` content. Metadata fields stay verbatim — they're operational signals (activity_id, generated_at, generator_path, parent_signal, judge scores) without PII.

## 6. Design Decisions

**Why migration 0013 and not 0005.** The phase-e.md plan reserved 0005 in early 2026-04 when E5 was speculative. Phases F.5, G, H, I, J have all shipped intervening migrations and 0005–0012 are taken. The carve-out renumbers to 0013 and updates phase-e.md's reservation in the same PR; without that update, an operator following phase-e.md would conflict on the next run. Alternative: rename existing migrations to free 0005. Rejected — migrations are immutable once shipped; renaming corrupts every existing DB's `__migrations` table.

**Why title-case + word-boundary child-name scrub.** Names like "Sage", "River", "Hunter", "Pearl" are common nouns. Case-insensitive match (`sage` → `[REDACTED]`) would shred ordinary play text in the training corpus. The conservative position leaves a small fraction of lowercase mentions un-scrubbed (typically operator-typed in a transcript rendering, rare in audio→ASR pipelines where Whisper title-cases proper nouns by default). Alternative: contextual heuristic. Rejected — non-deterministic, harder to unit-test, harder to audit. Documented operator override: operator can set `redact_for_sft=1` on a row they spot-check and find under-scrubbed.

**Why `--sft-export` as a new flag rather than changing the default filter.** The existing default of `eval_dump.py` already applies the SFT quality filter without PII redaction. Operator scripts (audit / debug / corpus-sniff) depend on that behavior. Silently bolting redaction onto the existing default would change output of every existing invocation. The new flag is explicit at the call site, and the help text spells out exactly what it bundles. Alternative: orthogonal `--redact` flag. Rejected — four valid flag combinations to document and test, worse for a substrate that's only consumed in one combination.

**Why pure-function redactor, no DB access inside `redact.py`.** Keeps the module trivially unit-testable from synthetic inputs without a SQLite fixture. The DB call lives in `eval_dump.py` where it belongs (sourcing `child_names` from `children`). Side benefit: future call sites (live transcript scrub, parent UI redact-preview) can reuse the same function without dragging in DB plumbing.

**Why a smoke gate as a dedicated build step rather than folding into Step 3.** Per the [code-quality.md "New components require an integration test through the production caller"](../.claude/rules/code-quality.md) rule, the silent-wiring failure mode here is "redactor exists, eval_dump exists, but eval_dump never calls the redactor." A separate step that exercises the full path (DB seed → CLI invocation → output assertion) makes that wiring failure impossible to ship. Cost: one extra step. Worth it.

**`pii_filter_version` in row metadata, not just REGISTRY.md.** Per-row provenance survives the JSONL leaving the home machine (RunPod, etc.). REGISTRY.md captures the version for the adapter; per-row metadata captures it for any individual training example, useful when corpora get merged or re-split downstream.

## 7. Build Steps

### Step 1: Migration 0013 — `redact_for_sft` column + `--sft-filter` CLI extension

- **Problem:** Add `redact_for_sft INTEGER NOT NULL DEFAULT 0` to `labeled_events` via a forward-only migration. Extend `count_rows(sft_filter=True)` SQL clauses to require `redact_for_sft = 0`. Update `--sft-filter` CLI help text. Remove the three forward-compat TODO comments at `labeled_events.py:504-507`, `:569-571`, `:601-603`. Migration file MUST NOT contain its own `BEGIN`/`COMMIT` (runner wraps). `record_generation()` requires NO signature change — the `DEFAULT 0` clause covers all new rows at DDL level; do not add a `redact_for_sft` parameter. As part of the same diff, update [documentation/plan/phase-e.md](plan/phase-e.md) Step 27 to renumber `migration 0005` → `migration 0013` (and Step 29's `0006` → `0014` as a forward note) and add a back-pointer near Step 27 to `documentation/e3-backend-carveout-plan.md`.
- **Type:** code
- **Issue:** #106
- **Flags:** --reviewers code --tdd
- **Produces:** `src/toybox/db/migrations/0013_labeled_events_redact_for_sft.sql`, modifications to `src/toybox/ai/labeled_events.py`, `tests/integration/migrations/test_0013_labeled_events_redact_for_sft.py`.
- **Done when:** Migration runs forward against a fresh DB and against a DB at migration 0012; idempotent (running twice is a no-op via `__migrations` table); `count_rows(sft_filter=True)` returns 0 for a seeded row with `redact_for_sft=1` and the same row otherwise passing; `--sft-filter` CLI help text reads exactly *"Apply the SFT-export filter (parent_signal != -1 AND, when judge scores are present, safety >= 4 AND mean_quality >= 3.6 — i.e. strictly above 3.5; the rubric scores are 1..5 ints, so the sum of the five rubric fields is gated at >= 18; AND redact_for_sft = 0)."* (replaces the current text at [labeled_events.py:597-603](../src/toybox/ai/labeled_events.py#L597-L603); test asserts this exact string is present in `--help` output); all three TODO comments deleted; phase-e.md migration numbers updated and the back-pointer comment added; `uv run ruff check .` clean; `uv run mypy src` clean.
- **Depends on:** none.
- **Status:** DONE (2026-05-13)

### Step 2: PII redaction module + unit tests

- **Problem:** Land `src/toybox/ai/redact.py` with `PII_FILTER_VERSION = "1.0"` and `redact_pii(text, *, child_names)` per §"New Components". Title-case + word-boundary child-name match. Each `child_name` is wrapped in `re.escape()` before pattern construction so regex metacharacters in `display_name` (e.g. `Sage [the great]`, `Mr. Unicorn`) do not crash or match unintended text. Regex scrubs for emails, NANP phones, US street addresses. Replacement token: `[REDACTED]` (fixed). Pure function, no I/O, stdlib `re` module only — no new dependencies in `pyproject.toml`. Unit tests with a synthetic PII corpus covering: child name embedded mid-sentence, lowercase variant of a child name (must NOT redact), child name containing regex metacharacters (e.g. `Sage [the great]` and `Mr.Unicorn` — assert clean redaction with no `re.error`), email at sentence end with trailing period, phone with various separators (`-` `.` ` ` none), street address with various suffixes, multiple PII tokens in one input, empty input, input with no PII (passthrough).
- **Type:** code
- **Issue:** #107
- **Flags:** --reviewers code --tdd
- **Produces:** `src/toybox/ai/redact.py`, `tests/unit/ai/test_redact.py`.
- **Done when:** All listed test cases green; `redact_pii("Hello Sage, I love sage advice.", child_names=["Sage"])` returns `"Hello [REDACTED], I love sage advice."` exactly; `redact_pii("Hi Mr.Unicorn", child_names=["Mr.Unicorn"])` returns `"Hi [REDACTED]"` (proves `re.escape` is applied); redactor exports `PII_FILTER_VERSION` constant; `uv run ruff check .` clean; `uv run mypy src` clean.
- **Depends on:** none. Parallel-safe with Step 1.
- **Status:** DONE (2026-05-13)

### Step 3: `eval_dump.py --sft-export` mode

- **Problem:** Extend [src/toybox/ai/eval_dump.py](../src/toybox/ai/eval_dump.py) with a new `--sft-export` flag. When set: apply existing SFT-quality filter + `redact_for_sft = 0` predicate + PII redaction via `redact.redact_pii(...)`. Source `child_names` ONCE per CLI invocation via `SELECT display_name FROM children` (NOT per row — the result set is small, typically <10 rows; cache in a local variable and pass into the stream). Redact both `inputs_chatml_json` user-message content (the message at index ≥1; system message at index 0 stays untouched) AND `activity_json` content. Metadata fields stay verbatim. Emit `pii_filter_version` into per-row metadata under `metadata.pii_filter_version`. Extend the existing stderr summary at [eval_dump.py:302-306](../src/toybox/ai/eval_dump.py#L302-L306) to include `sft_export=<bool>` and (when true) `pii_filter_version=<value>` so an operator scanning terminal output can verify which filter applied without parsing JSONL. `--sft-export` is mutually exclusive with `--all` (argparse error). Behavior on malformed `inputs_chatml_json`: preserve the current `_row_to_jsonl` crash semantics (raise `json.JSONDecodeError` to caller); do NOT add a silent skip-on-decode-error path — data corruption deserves loud failure. If `children` table is empty, emit a single stderr WARNING at startup ("PII redaction running with no child-name deny-list; only regex scrubs apply") and continue.
- **Type:** code
- **Issue:** #108
- **Flags:** --reviewers code
- **Produces:** modifications to `src/toybox/ai/eval_dump.py`, internal unit test additions in `tests/integration/test_eval_cli.py` (existing file).
- **Done when:** `python -m toybox.ai.eval_dump --sft-export --since <iso>` exits 0 against a seeded DB; row-level unit tests cover the scrub path (user-message scrubbed, system message untouched, metadata untouched); `--sft-export` + `--all` raises a parser error; the `redact_for_sft = 0` clause is included in the SQL `WHERE` of `fetch_rows()`, not just an in-memory post-filter; stderr summary line includes `sft_export=true` and `pii_filter_version=1.0` when the flag is set; empty-`children` warning fires when applicable (covered by a unit test that seeds zero children); malformed `inputs_chatml_json` still raises (regression test exists); `uv run ruff check .` clean; `uv run mypy src` clean.
- **Depends on:** Step 1 (column must exist), Step 2 (redactor must exist).
- **Status:** DONE (2026-05-13)

### Step 4: LoRA registry template

- **Problem:** Create `data/models/lora/REGISTRY.md` with the table template from §"New Components". Create `data/models/lora/.gitkeep`. Extend `.gitignore` to ignore `data/models/lora/*/` (trained adapter subdirs) while preserving `REGISTRY.md` and `.gitkeep`. Verify `git status` shows both files tracked and a freshly-created subdir at `data/models/lora/test/` ignored.
- **Type:** code
- **Issue:** #109
- **Flags:** --reviewers code
- **Produces:** `data/models/lora/REGISTRY.md`, `data/models/lora/.gitkeep`, modifications to `.gitignore`.
- **Done when:** Both new files committable via `git add`; a temp `data/models/lora/test-2026-05-13/` subdir is ignored by `git status`.
- **Depends on:** none. Parallel-safe with Steps 1, 2.
- **Status:** DONE (2026-05-13)

### Step 5: End-to-end smoke gate (autonomous integration test)

- **Problem:** Land `tests/integration/test_eval_dump_sft_export.py` — a pytest integration test that exercises the full pipeline as an unattended autonomous gate. Test setup: create a temp SQLite DB at migration 0013; insert 2 children with `display_name` = "Sage" and "River"; insert 3 `labeled_events` rows: (a) one with `inputs_chatml_json` containing "Sage played with the ball" + `activity_json` containing "Call River at 555-123-4567 if needed" + good judge scores + `parent_signal=1` + `redact_for_sft=0` (should be exported, fully scrubbed), (b) one identical to (a) but with `redact_for_sft=1` (should be excluded), (c) one with `parent_signal=-1` (should be excluded by quality filter). Invoke `eval_dump.main(['--sft-export', '--since', '2020-01-01', '--db', str(tmp_db), '--out', str(tmp_out)])`. Assert: exactly 1 JSONL line; no occurrence of "Sage" or "River" or "555-123-4567" or "sage advice" (positive control — should still be there since the test doesn't include that string; verify by including "We had sage advice today." in the same content and asserting it survives); per-row metadata contains `pii_filter_version == "1.0"`. This step's value is catching the silent-wiring failure mode where the redactor is present and the CLI flag is present but the two are never connected.
- **Type:** code
- **Issue:** #110
- **Flags:** --reviewers code
- **Produces:** `tests/integration/test_eval_dump_sft_export.py`.
- **Done when:** Test green on a fresh `uv run pytest tests/integration/test_eval_dump_sft_export.py` invocation; test fails informatively if you intentionally break the redactor wiring in `eval_dump.py` (verify by manually disabling the redact call in `eval_dump.py`, re-running the test, confirming it fails with a "PII string still present" assertion message, then restoring the code).
- **Depends on:** Steps 1, 2, 3, 4.

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Migration 0013 conflicts with a future phase-e.md update | Phase-e.md still reserves 0005 for `redact_for_sft`. If an operator runs `/build-phase` on phase-e.md before the renumber lands, a second 0005 file is created and migration ordering breaks. | Step 1 includes the phase-e.md update in the same diff. Reviewers should explicitly verify phase-e.md migration numbers in code review. |
| Title-case + word-boundary scrub misses lowercase mentions | "we played a game with sage today" leaves the name in. | Documented in the redactor docstring + the REGISTRY.md note. Operators can flag specific rows with `redact_for_sft=1`. If this becomes systematic, bump `PII_FILTER_VERSION` to `1.1` with a contextual heuristic and retrain. |
| Address regex over-redacts in activity narratives | "1 little duck" or "5 jumping monkeys" could match `\d+\s+\w+\s+(St|...)` if the regex is loose. | Pin the street-suffix list to a closed set (`St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place`) with `\b` boundaries on both ends. Test corpus includes "1 little duck waddled to the park" and asserts NO redaction. |
| `children.display_name` could itself contain PII (last names, addresses) | If an operator types "River Smith, 123 Main St" as a display_name, that whole string becomes the deny-list entry. The title-case + word-boundary match means only `River Smith` matches; the address part of the entry is harmless. | Operator-facing docs already advise display_name = first name. Not in scope to enforce in this carve-out. |
| `eval_dump.py` `--sft-export` reads `children` table — what if the table is empty? | Empty deny-list means no child-name scrubbing happens; regex scrubs still run. | This is correct behavior. A pre-flight emit at stderr: "WARNING: children table empty; PII redaction running with no name deny-list" makes the situation visible. |
| Smoke gate test seeds via raw SQL, not via production APIs | Drift between test seed and production INSERT path could mask a bug. | The test imports `record_generation` from `labeled_events.py` for the row inserts, not raw SQL. Children are inserted via raw SQL because the children-API surface is broader than needed here. |
| `--sft-export` mutual-exclusion with `--all` may bite an operator script | An existing script that always passes `--all` for audit purposes would error when it gains `--sft-export`. | The two flags are mutually exclusive by intent — the operator must pick one. Argparse error is the right surface; silent precedence rule would be worse. |
| `PII_FILTER_VERSION` is a string constant — no formal versioning rules | When the regex changes, an operator might forget to bump. | Step 2 unit tests pin `PII_FILTER_VERSION == "1.0"`; any change to the redactor body that doesn't also bump the constant fails the constant-assertion test. Future contributors are forced to make the bump explicit. |

## 9. Testing Strategy

**Unit tests (`tests/unit/ai/test_redact.py`):**
- Pure-function coverage for `redact_pii` against a synthetic PII corpus.
- Negative cases (lowercase common-noun variants of child names must NOT redact).
- Edge cases (empty input, no-PII input, multiple PII tokens, PII at boundaries).
- Constant pin: `PII_FILTER_VERSION == "1.0"`.

**Migration tests (`tests/integration/migrations/test_0013_labeled_events_redact_for_sft.py`):**
- Forward migration on a DB at 0012: column added, default=0 backfilled for existing rows.
- Idempotency: running migration runner twice on a 0013 DB is a no-op.
- `count_rows(sft_filter=True)` respects the new column (seeded test rows with `redact_for_sft=0` and `=1` produce expected counts).

**eval_dump unit tests (extend `tests/integration/test_eval_cli.py`):**
- `--sft-export` + `--all` raises argparse error.
- `--sft-export` includes the `redact_for_sft = 0` predicate in the SQL.
- `_row_to_jsonl` under `--sft-export` scrubs user message content but leaves system message + metadata untouched.

**Integration smoke gate (`tests/integration/test_eval_dump_sft_export.py`):**
- Full pipeline: temp DB → seeded rows via `record_generation` + raw SQL `children` insert → CLI invocation → output JSONL parse → PII assertion.
- Positive control: "sage advice" string survives (NOT redacted by title-case rule).
- Negative control: "Sage", "River", a phone number, an email all absent from output.
- Filter exclusion: `redact_for_sft=1` row and `parent_signal=-1` row are absent.
- `pii_filter_version` present in per-row metadata.

**Existing tests that might break:**
- `tests/integration/test_eval_cli.py` may have golden-output assertions; verify after Step 3 that the new metadata key doesn't break those — extend the golden if it does.
- `tests/integration/test_labeled_events.py` (if it exists) may assert on `count_rows()` results without the new column — extend the existing tests in Step 1 to cover the new clause.

**End-to-end verification:**
- After `/build-phase` completes, run `uv run pytest tests/unit/ai/ tests/integration/` once at the master branch — full backend test suite must be green.
- Manual operator gate (NOT part of autonomous run): on a real DB with real `labeled_events` rows, run `uv run python -m toybox.ai.eval_dump --sft-export --since <30-days-ago>` and spot-check ~5 rows of output for PII leakage. This is the manual handoff after the autonomous build finishes — should happen in the morning before the calculator round.
