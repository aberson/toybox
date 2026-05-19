# Distractor corpus — per-entry source attribution

`distractors.json` carries one entry per element of the form
`{ "element_id": "au-79", "fact_a_true": "...", "fact_b_false": "..." }`.
Each entry MUST have a matching row in the table below.

## Source tags

- **`operator`** — entry is human-authored or human-approved. Loader
  accepts unconditionally.
- **`llm`** — entry is machine-generated (Phase N Step N1.5 generator)
  and awaiting operator skim-review. Loader rejects these rows by
  default; set `TOYBOX_ALLOW_LLM_DISTRACTORS=1` to opt in (used while
  N1.5 has just run and N1 has not yet flipped tags).

## Format

Standard three-column markdown table:

```markdown
| element_id | source | reasoning |
|---|---|---|
| au-79 | operator | False fact "Gold floats in water" picked because Child B sees coins sink. |
```

Validation (run after every edit):

```
uv run python -m toybox.activities.distractor_corpus --validate
```

Successful output: `N entries, N credits rows, OK`.

## Entries

| element_id | source | reasoning |
|---|---|---|

## File history

- 2026-05-18 — Phase N Step N1-prep ships the empty scaffold. N1.5
  generator fills 118 rows tagged `llm`; N1 operator skim-review
  flips accepted rows to `operator` and edits/deletes rejects.
