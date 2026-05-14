"""E3 smoke-gate integration test for ``eval_dump --sft-export``.

This single linear test exercises the FULL ``--sft-export`` pipeline
through the production caller chain
(``main() → stream_export() → fetch_rows() → write_jsonl``) as an
unattended autonomous gate.

The whole value of this test is catching the silent-wiring failure
mode where the redactor module exists, the ``--sft-export`` CLI flag
exists, and the ``redact_for_sft`` SQL column exists, but the three
are never connected end-to-end. Per
``.claude/rules/code-quality.md`` §"New components require an
integration test through the production caller", unit tests of each
piece in isolation do NOT catch this — only an integration test that
runs the actual CLI entry point does.

The test seeds three rows:

* Row A: should be exported. Contains PII (Sage in user message;
  River and a phone number in activity_json). ``redact_for_sft = 0``,
  parent_signal = +1.0, judge scores well above the SFT floor.
* Row B: identical to Row A except ``redact_for_sft = 1`` — must be
  excluded by the SQL-level operator opt-out filter.
* Row C: identical to Row A except ``parent_signal = -1`` — must be
  excluded by the in-memory quality filter.

Then invokes ``eval_dump.main([...])`` and asserts on the output JSONL
file: exactly 1 line, PII scrubbed everywhere it should be, lowercase
"sage advice" preserved (the title-case-only conservative-match pin),
system message untouched, per-row ``pii_filter_version`` present, and
the negative-wiring catch (the JSONL contains ``[REDACTED]`` at least
three times).
"""

from __future__ import annotations

import json
from pathlib import Path

from toybox.ai.eval_dump import main as eval_dump_main
from toybox.ai.redact import PII_FILTER_VERSION
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


def test_sft_export_end_to_end_pipeline(tmp_path: Path) -> None:
    """End-to-end --sft-export gate: production caller chain, real redactor.

    Sub-assertions (all must pass):

    1. Exactly 1 JSONL line in the output file.
    2. "Sage" (child name) NOT in output.
    3. "River" (child name) NOT in output.
    4. "555-123-4567" (phone) NOT in output.
    5. "sage advice" (lowercase) IS preserved — conservative-match pin.
    6. "persona card here" (system message) preserved verbatim.
    7. metadata.pii_filter_version == PII_FILTER_VERSION.
    8. activity_id / generated_at / generator_path / parent_signal
       preserved in metadata.
    9. Negative-wiring catch: [REDACTED] appears at least 3 times.
    """
    # ---- 1. Set up a fresh DB at a known path; run all migrations. -----
    db_path = tmp_path / "toybox.db"
    out_path = tmp_path / "out.jsonl"

    conn = connect(db_path)
    try:
        run_migrations(conn)

        # ---- 2. Seed children. -----------------------------------------
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("c-sage", "Sage"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("c-river", "River"),
            )

        # ---- 3. Seed three labeled_events rows. ------------------------
        # All three share this ChatML / activity payload by design — the
        # filtering decisions hinge on redact_for_sft and parent_signal
        # only, not on content.
        chatml_str = json.dumps(
            [
                {"role": "system", "content": "persona card here"},
                {
                    "role": "user",
                    "content": ("Sage played with the ball. We had sage advice today."),
                },
            ]
        )
        activity_text = '{"text": "Call River at 555-123-4567 if needed"}'

        judge_scores_json = json.dumps(
            {
                "safety": 5,
                "schema": 4,
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
            }
        )
        judge_run_at = "2026-05-13T00:00:00Z"

        # Row A: PASSES both filters. parent_signal = 1.0 (thumbs-up),
        # redact_for_sft = 0, high judge scores. Should be the ONLY row
        # exported.
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json, "
                " parent_signal, parent_signal_set_at, "
                " judge_scores_json, judge_run_at, redact_for_sft) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "act-row-a",
                    "2026-05-10T12:00:00Z",
                    "claude",
                    chatml_str,
                    activity_text,
                    1.0,
                    "2026-05-10T12:01:00Z",
                    judge_scores_json,
                    judge_run_at,
                    0,
                ),
            )

        # Row B: identical to Row A EXCEPT redact_for_sft = 1. Must be
        # excluded by the SQL-level operator opt-out predicate
        # (``redact_for_sft = 0`` clause inside ``_build_fetch_query``).
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json, "
                " parent_signal, parent_signal_set_at, "
                " judge_scores_json, judge_run_at, redact_for_sft) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "act-row-b",
                    "2026-05-10T12:00:01Z",
                    "claude",
                    chatml_str,
                    activity_text,
                    1.0,
                    "2026-05-10T12:01:00Z",
                    judge_scores_json,
                    judge_run_at,
                    1,
                ),
            )

        # Row C: identical to Row A EXCEPT parent_signal = -1. Must be
        # excluded by the in-memory quality filter (``_row_passes_filter``
        # rejects parent_signal == -1.0).
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json, "
                " parent_signal, parent_signal_set_at, "
                " judge_scores_json, judge_run_at, redact_for_sft) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "act-row-c",
                    "2026-05-10T12:00:02Z",
                    "claude",
                    chatml_str,
                    activity_text,
                    -1.0,
                    "2026-05-10T12:01:00Z",
                    judge_scores_json,
                    judge_run_at,
                    0,
                ),
            )
    finally:
        conn.close()

    # ---- 4. Invoke the CLI through the production entry point. --------
    # Uses eval_dump.main([...]) — same shape as the existing test_eval_cli
    # callers (eval_dump_main accepts an argv list).
    rc = eval_dump_main(
        [
            "--sft-export",
            "--since",
            "2020-01-01",
            "--db",
            str(db_path),
            "--out",
            str(out_path),
        ]
    )
    assert rc == 0

    # ---- 5. Read the output file. -------------------------------------
    raw = out_path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]

    # Assertion 1: exactly 1 JSONL line (Row A passes, B and C excluded).
    assert len(lines) == 1, (
        f"Expected exactly 1 exported row (Row A); got {len(lines)}. Output: {raw!r}"
    )
    jsonl_line = lines[0]
    record = json.loads(jsonl_line)

    # Assertion 2: "Sage" (child name) NOT in JSONL line.
    assert "Sage" not in jsonl_line, (
        f'"Sage" should not be in output (child-name PII); line: {jsonl_line!r}'
    )

    # Assertion 3: "River" (child name) NOT in JSONL line.
    assert "River" not in jsonl_line, (
        f'"River" should not be in output (child-name PII); line: {jsonl_line!r}'
    )

    # Assertion 4: phone number NOT in JSONL line.
    assert "555-123-4567" not in jsonl_line, (
        f'"555-123-4567" should not be in output (phone PII); line: {jsonl_line!r}'
    )

    # Assertion 5: lowercase "sage advice" IS preserved
    # (title-case-only conservative-match pin from redact.py step 2).
    assert "sage advice" in jsonl_line, (
        '"sage advice" (lowercase) MUST survive redaction — the '
        "redactor is title-case + word-boundary only by design. If "
        "this fails, the conservative-match pin has regressed."
    )

    # Assertion 6: system message at index 0 must survive verbatim.
    assert "persona card here" in jsonl_line, (
        '"persona card here" (system message at index 0) must survive '
        "untouched per the carve-out spec."
    )
    messages = record["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "persona card here"

    # Assertion 7: pii_filter_version in per-row metadata equals constant.
    metadata = record["metadata"]
    assert metadata["pii_filter_version"] == PII_FILTER_VERSION, (
        f"metadata.pii_filter_version mismatch: "
        f"got {metadata['pii_filter_version']!r}, "
        f"expected {PII_FILTER_VERSION!r}"
    )

    # Assertion 8: operational metadata fields preserved verbatim.
    assert metadata["activity_id"] == "act-row-a"
    assert metadata["generated_at"] == "2026-05-10T12:00:00Z"
    assert metadata["generator_path"] == "claude"
    assert metadata["parent_signal"] == 1.0

    # Assertion 9: NEGATIVE-WIRING CATCH. The JSONL must contain
    # [REDACTED] at least 3 times (Sage in user message, River in
    # activity, phone number in activity). If the redactor is wired to
    # be a no-op, this assertion fails immediately.
    redacted_count = jsonl_line.count("[REDACTED]")
    assert redacted_count >= 3, (
        f"Expected at least 3 [REDACTED] tokens in output (Sage in "
        f"user message, River in activity, phone in activity); got "
        f"{redacted_count}. Redactor wiring likely broken. "
        f"Line: {jsonl_line!r}"
    )
