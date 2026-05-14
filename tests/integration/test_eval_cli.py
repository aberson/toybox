"""Coverage for eval_dump + eval_run CLIs.

* eval_dump: round-trips labeled_events rows into ChatML JSONL with
  the metadata block; the SFT filter excludes parent_signal=-1.
* eval_run: load_fixtures parses the shipped 20-fixture file;
  generate_for_fixture is deterministic; evaluate_regression skips
  cleanly on a placeholder baseline; failure modes (safety auto-fail,
  expected_floor violation, mean-score regression) all surface.
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.activities.generator import generate
from toybox.ai.eval_dump import (
    DEFAULT_MEAN_QUALITY_FLOOR,
    DEFAULT_SAFETY_FLOOR,
    EMPTY_CHILDREN_WARNING,
    _build_fetch_query,
    _row_to_jsonl,
    fetch_rows,
    stream_export,
    write_jsonl,
)
from toybox.ai.eval_dump import (
    main as eval_dump_main,
)
from toybox.ai.eval_run import (
    DEFAULT_FIXTURES_PATH,
    DEFAULT_HOLDOUT_PATH,
    Fixture,
    evaluate_regression,
    fixture_to_context,
    generate_for_fixture,
    load_fixtures,
    load_holdout_ids,
    run_fixtures,
    synthesize_placeholder_scores,
    trigger_to_intent,
)
from toybox.ai.labeled_events import (
    GENERATOR_PATH_OFFLINE,
    GeneratorContext,
    record_generation,
    update_judge_scores,
    update_parent_signal,
)
from toybox.ai.redact import PII_FILTER_VERSION
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "toybox.db")
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


# --------------------------------------------------------------- eval_dump


def _seed_event(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    generated_at: str,
    safety: int = 5,
    mean_other: int = 5,
    parent_signal: float | None = None,
) -> None:
    activity = generate(intent="boredom", slot=None, context={"id": activity_id}, hour=10, seed=1)
    # Override the generated id so we can predict it for assertions
    activity_dict = json.loads(activity.model_dump_json())
    activity_dict["id"] = activity_id
    activity_json = json.dumps(activity_dict)
    chatml = json.dumps([{"role": "system", "content": "sys"}, {"role": "user", "content": "{}"}])
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (activity_id, generated_at, GENERATOR_PATH_OFFLINE, chatml, activity_json),
        )
    judge_payload = json.dumps(
        {
            "schema": mean_other,
            "age_appropriateness": mean_other,
            "doability": mean_other,
            "persona_fidelity": mean_other,
            "coherence": mean_other,
            "safety": safety,
            "hallucinated_props": [],
            "judge_notes": "test",
        }
    )
    update_judge_scores(conn, activity_id=activity_id, judge_scores_json=judge_payload)
    if parent_signal is not None:
        update_parent_signal(conn, activity_id=activity_id, signal=parent_signal)


def test_eval_dump_returns_chatml_with_assistant_turn(conn: sqlite3.Connection) -> None:
    _seed_event(conn, activity_id="aa-1", generated_at="2026-05-03T00:00:00Z")
    rows = list(
        stream_export(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            apply_sft_filter=True,
            safety_floor=DEFAULT_SAFETY_FLOOR,
            mean_quality_floor=DEFAULT_MEAN_QUALITY_FLOOR,
        )
    )
    assert len(rows) == 1
    record = rows[0]
    roles = [m["role"] for m in record["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert record["metadata"]["activity_id"] == "aa-1"
    assert record["metadata"]["generator_path"] == "offline"
    assert record["metadata"]["judge_scores"] is not None


def test_eval_dump_sft_filter_excludes_dismissed(conn: sqlite3.Connection) -> None:
    """parent_signal == -1 is excluded by the SFT filter."""
    _seed_event(
        conn,
        activity_id="aa-good",
        generated_at="2026-05-03T00:00:00Z",
    )
    _seed_event(
        conn,
        activity_id="aa-dismissed",
        generated_at="2026-05-03T00:00:01Z",
        parent_signal=-1.0,
    )
    rows = list(
        stream_export(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            apply_sft_filter=True,
            safety_floor=DEFAULT_SAFETY_FLOOR,
            mean_quality_floor=DEFAULT_MEAN_QUALITY_FLOOR,
        )
    )
    activity_ids = [r["metadata"]["activity_id"] for r in rows]
    assert "aa-good" in activity_ids
    assert "aa-dismissed" not in activity_ids


def test_eval_dump_sft_filter_excludes_low_safety(conn: sqlite3.Connection) -> None:
    _seed_event(conn, activity_id="aa-ok", generated_at="2026-05-03T00:00:00Z")
    _seed_event(
        conn,
        activity_id="aa-unsafe",
        generated_at="2026-05-03T00:00:01Z",
        safety=2,  # below floor of 4
    )
    rows = list(
        stream_export(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            apply_sft_filter=True,
            safety_floor=DEFAULT_SAFETY_FLOOR,
            mean_quality_floor=DEFAULT_MEAN_QUALITY_FLOOR,
        )
    )
    activity_ids = [r["metadata"]["activity_id"] for r in rows]
    assert "aa-unsafe" not in activity_ids


def test_eval_dump_all_mode_includes_unscored(conn: sqlite3.Connection) -> None:
    """``--all`` skips the SFT filter and includes rows missing scores."""
    activity = generate(intent="boredom", slot=None, context={"id": "raw"}, hour=10, seed=1)
    record_generation(
        conn,
        activity=activity,
        ctx=GeneratorContext(intent="boredom"),
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    rows = list(
        stream_export(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            apply_sft_filter=False,
            safety_floor=DEFAULT_SAFETY_FLOOR,
            mean_quality_floor=DEFAULT_MEAN_QUALITY_FLOOR,
        )
    )
    assert any(r["metadata"]["activity_id"] == activity.id for r in rows)


def test_write_jsonl_one_per_line(conn: sqlite3.Connection) -> None:
    _seed_event(conn, activity_id="aa-1", generated_at="2026-05-03T00:00:00Z")
    _seed_event(conn, activity_id="aa-2", generated_at="2026-05-03T00:00:01Z")
    out = io.StringIO()
    count = write_jsonl(
        stream_export(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            apply_sft_filter=True,
            safety_floor=DEFAULT_SAFETY_FLOOR,
            mean_quality_floor=DEFAULT_MEAN_QUALITY_FLOOR,
        ),
        out,
    )
    assert count == 2
    lines = out.getvalue().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line is valid JSON


def test_fetch_rows_orders_oldest_first(conn: sqlite3.Connection) -> None:
    _seed_event(conn, activity_id="aa-newer", generated_at="2026-05-03T00:00:01Z")
    _seed_event(conn, activity_id="aa-older", generated_at="2026-05-03T00:00:00Z")
    rows = fetch_rows(conn, since="2026-01-01T00:00:00Z", generator_path=None)
    ids = [r["activity_id"] for r in rows]
    assert ids == ["aa-older", "aa-newer"]


# -------------------------------------------------- eval_dump --sft-export


def _seed_event_with_chatml(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    generated_at: str,
    inputs_chatml_json: str,
    activity_json: str,
    safety: int = 5,
    mean_other: int = 5,
    parent_signal: float | None = None,
    redact_for_sft: int = 0,
) -> None:
    """Seed a labeled_events row with explicit chatml + activity content.

    Used by --sft-export tests that need to verify scrubbing on specific
    PII tokens.
    """
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json, redact_for_sft) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                activity_id,
                generated_at,
                GENERATOR_PATH_OFFLINE,
                inputs_chatml_json,
                activity_json,
                redact_for_sft,
            ),
        )
    judge_payload = json.dumps(
        {
            "schema": mean_other,
            "age_appropriateness": mean_other,
            "doability": mean_other,
            "persona_fidelity": mean_other,
            "coherence": mean_other,
            "safety": safety,
            "hallucinated_props": [],
            "judge_notes": "test",
        }
    )
    update_judge_scores(conn, activity_id=activity_id, judge_scores_json=judge_payload)
    if parent_signal is not None:
        update_parent_signal(conn, activity_id=activity_id, signal=parent_signal)


def _seed_child(conn: sqlite3.Connection, *, child_id: str, display_name: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO children (id, display_name) VALUES (?, ?)",
            (child_id, display_name),
        )


def test_sft_export_and_all_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--sft-export + --all → argparse error, non-zero exit."""
    # argparse exits via SystemExit(2) on parse error.
    with pytest.raises(SystemExit) as exc_info:
        eval_dump_main(
            [
                "--sft-export",
                "--all",
                "--since",
                "2020-01-01",
                "--db",
                str(tmp_path / "missing.db"),
            ]
        )
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "not allowed with" in captured.err


@pytest.mark.parametrize(
    ("generator_path", "sft_export", "expect_redact", "expect_genpath", "params"),
    [
        pytest.param(None, False, False, False, (), id="no-export-no-genpath"),
        pytest.param(None, True, True, False, (), id="export-no-genpath"),
        pytest.param("offline", True, True, True, ("offline",), id="export-with-genpath"),
        pytest.param("offline", False, False, True, ("offline",), id="no-export-with-genpath"),
    ],
)
def test_build_fetch_query(
    generator_path: str | None,
    sft_export: bool,
    expect_redact: bool,
    expect_genpath: bool,
    params: tuple[str, ...],
) -> None:
    """SQL builder honors --sft-export (adds redact_for_sft=0) and --generator-path.

    The four parametrized cases cover the cartesian product of the two
    optional predicates so a regression in either branch surfaces with
    one descriptive id.
    """
    sql, actual_params = _build_fetch_query(generator_path=generator_path, sft_export=sft_export)
    if expect_redact:
        assert "redact_for_sft = 0" in sql
    else:
        assert "redact_for_sft" not in sql
    if expect_genpath:
        assert "generator_path = ?" in sql
    else:
        assert "generator_path = ?" not in sql
    assert actual_params == params


def test_fetch_rows_sft_export_excludes_flagged_row(conn: sqlite3.Connection) -> None:
    """SQL-level filter: ``redact_for_sft = 1`` row never reaches Python."""
    _seed_event(conn, activity_id="ok", generated_at="2026-05-03T00:00:00Z")
    _seed_event(conn, activity_id="opt-out", generated_at="2026-05-03T00:00:01Z")
    with conn:
        conn.execute(
            "UPDATE labeled_events SET redact_for_sft = 1 WHERE activity_id = ?",
            ("opt-out",),
        )
    rows = list(
        fetch_rows(
            conn,
            since="2026-01-01T00:00:00Z",
            generator_path=None,
            sft_export=True,
        )
    )
    ids = [r["activity_id"] for r in rows]
    assert ids == ["ok"]


def test_row_to_jsonl_sft_export_scrubs_user_and_activity(conn: sqlite3.Connection) -> None:
    """Under sft_export: user message + activity_json scrubbed; system + metadata verbatim."""
    chatml = json.dumps(
        [
            {"role": "system", "content": "<persona card stay verbatim>"},
            {"role": "user", "content": "Sage played with the ball"},
        ]
    )
    activity_json = json.dumps(
        {
            "id": "act-1",
            "instruction": "Call River at 555-123-4567",
        }
    )
    _seed_event_with_chatml(
        conn,
        activity_id="aa-redact",
        generated_at="2026-05-03T00:00:00Z",
        inputs_chatml_json=chatml,
        activity_json=activity_json,
    )
    row = conn.execute(
        "SELECT * FROM labeled_events WHERE activity_id = ?", ("aa-redact",)
    ).fetchone()
    record = _row_to_jsonl(row, sft_export=True, child_names=["Sage", "River"])
    # System message untouched.
    assert record["messages"][0]["role"] == "system"
    assert record["messages"][0]["content"] == "<persona card stay verbatim>"
    # User message scrubbed.
    user_content = record["messages"][1]["content"]
    assert "Sage" not in user_content
    assert "[REDACTED]" in user_content
    # Assistant content scrubbed (River + phone number).
    assistant_content = record["messages"][2]["content"]
    assert "River" not in assistant_content
    assert "555-123-4567" not in assistant_content
    assert "[REDACTED]" in assistant_content
    # Metadata verbatim except for new pii_filter_version field.
    metadata = record["metadata"]
    assert metadata["activity_id"] == "aa-redact"
    assert metadata["generator_path"] == "offline"
    assert metadata["pii_filter_version"] == PII_FILTER_VERSION


def test_row_to_jsonl_non_export_omits_pii_filter_version(
    conn: sqlite3.Connection,
) -> None:
    """Non-export path: metadata.pii_filter_version key absent; scrub does NOT run."""
    _seed_event(conn, activity_id="aa-plain", generated_at="2026-05-03T00:00:00Z")
    row = conn.execute(
        "SELECT * FROM labeled_events WHERE activity_id = ?", ("aa-plain",)
    ).fetchone()
    record = _row_to_jsonl(row)
    assert "pii_filter_version" not in record["metadata"]


def test_sft_export_empty_children_emits_warning(
    tmp_path: Path,
    conn: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty children table → one stderr warning at startup, continues."""
    # Persist seeded DB to a path the CLI can open by --db.
    db_path = tmp_path / "toybox.db"
    # Re-init a fresh DB at the given path (the fixture-provided conn
    # points elsewhere; we control the file directly here).
    fresh = connect(db_path)
    try:
        run_migrations(fresh)
        _seed_event_with_chatml(
            fresh,
            activity_id="aa-1",
            generated_at="2026-05-03T00:00:00Z",
            inputs_chatml_json=json.dumps(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"},
                ]
            ),
            activity_json=json.dumps({"id": "aa-1"}),
        )
    finally:
        fresh.close()

    out_path = tmp_path / "out.jsonl"
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
    captured = capsys.readouterr()
    assert EMPTY_CHILDREN_WARNING in captured.err
    # Warning fires exactly once.
    assert captured.err.count(EMPTY_CHILDREN_WARNING) == 1


def test_row_to_jsonl_malformed_chatml_raises(conn: sqlite3.Connection) -> None:
    """Malformed inputs_chatml_json must still raise json.JSONDecodeError."""
    _seed_event_with_chatml(
        conn,
        activity_id="aa-broken",
        generated_at="2026-05-03T00:00:00Z",
        inputs_chatml_json="not-json {{{",
        activity_json=json.dumps({"id": "aa-broken"}),
    )
    row = conn.execute(
        "SELECT * FROM labeled_events WHERE activity_id = ?", ("aa-broken",)
    ).fetchone()
    with pytest.raises(json.JSONDecodeError):
        _row_to_jsonl(row, sft_export=True, child_names=[])


def test_sft_export_stderr_summary_includes_flag_and_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stderr summary contains sft_export=True and pii_filter_version=1.0."""
    db_path = tmp_path / "toybox.db"
    fresh = connect(db_path)
    try:
        run_migrations(fresh)
        _seed_child(fresh, child_id="c1", display_name="Sage")
        _seed_event_with_chatml(
            fresh,
            activity_id="aa-1",
            generated_at="2026-05-03T00:00:00Z",
            inputs_chatml_json=json.dumps(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"},
                ]
            ),
            activity_json=json.dumps({"id": "aa-1"}),
        )
    finally:
        fresh.close()

    out_path = tmp_path / "out.jsonl"
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
    captured = capsys.readouterr()
    assert "sft_export=True" in captured.err
    assert f"pii_filter_version={PII_FILTER_VERSION}" in captured.err


# --------------------------------------------------------------- eval_run


def test_load_fixtures_parses_shipped_set() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    assert len(fixtures) == 20
    assert all(isinstance(f, Fixture) for f in fixtures)
    ids = {f.id for f in fixtures}
    assert {"f001", "f002", "f003", "f004", "f005"}.issubset(ids)


def test_load_holdout_pins_five_ids() -> None:
    ids = load_holdout_ids(DEFAULT_HOLDOUT_PATH)
    assert len(ids) == 5
    assert all(i.startswith("f") for i in ids)


def test_trigger_to_intent_known_mappings() -> None:
    assert trigger_to_intent("boredom_explicit") == "boredom"
    assert trigger_to_intent("excitement_spike") == "request_play"
    # Unknown trigger falls back to boredom (offline always has a template)
    assert trigger_to_intent("alien_invasion") == "boredom"


def test_generate_for_fixture_is_deterministic() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    fx = fixtures[0]
    a1 = generate_for_fixture(fx)
    a2 = generate_for_fixture(fx)
    assert a1.id == a2.id
    assert len(a1.steps) == 5


def test_fixture_to_context_carries_inventory() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    fx = next(f for f in fixtures if f.id == "f001")
    ctx = fixture_to_context(fx)
    assert ctx.intent == "boredom"
    assert "stuffed_unicorn" in ctx.available_toys
    assert ctx.persona_id == "mr_unicorn"


def test_run_fixtures_only_filters() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    out = run_fixtures(fixtures, fixtures_only=["f001", "f005"])
    assert set(out) == {"f001", "f005"}


def test_evaluate_regression_skips_on_placeholder_baseline() -> None:
    """Placeholder-only baseline must override any failure that would otherwise fire.

    To prove the placeholder gate is what triggers the skip (not just
    "everything matched"), we set CURRENT scores to a values that would
    normally fail loudly: a 3-point mean drop and a safety auto-fail.
    Both would cause a non-skipped run to fail; the placeholder baseline
    is the only thing that can save it.
    """
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    holdout = ["f001", "f002"]
    current_records = run_fixtures(fixtures, fixtures_only=holdout)
    # Real-looking current scores, deliberately low: would fail safety
    # auto-fail AND mean-drop checks if the regression actually ran.
    failing_current: dict[str, Any] = {
        "schema": 1,
        "age_appropriateness": 1,
        "doability": 1,
        "persona_fidelity": 1,
        "coherence": 1,
        "safety": 1,  # safety auto-fail trigger
        "hallucinated_props": [],
        "judge_notes": "would fail safety + mean drop",
    }
    for r in current_records.values():
        r["scores"] = failing_current
    # Baseline: every fixture is placeholder-flagged.
    baseline = {
        "fixtures": {
            "f001": {
                "placeholder": True,
                "scores": synthesize_placeholder_scores().to_mapping(),
            },
            "f002": {
                "placeholder": True,
                "scores": synthesize_placeholder_scores().to_mapping(),
            },
        }
    }
    result = evaluate_regression(
        current_records=current_records, baseline=baseline, holdout_ids=holdout
    )
    # Despite the would-fail current scores, the placeholder baseline
    # must short-circuit to a clean skip.
    assert result.passed is True
    assert result.skipped_reason is not None
    assert "placeholder" in result.skipped_reason
    assert result.failures == ()


def test_evaluate_regression_fails_on_safety_autofail() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    holdout = ["f001"]
    current_records = run_fixtures(fixtures, fixtures_only=holdout)
    # Score the fixture with safety=1 → auto-fail
    bad = synthesize_placeholder_scores().to_mapping()
    bad["safety"] = 1
    current_records["f001"]["scores"] = bad
    baseline = {
        "fixtures": {
            "f001": {"placeholder": False, "scores": bad},
        }
    }
    result = evaluate_regression(
        current_records=current_records, baseline=baseline, holdout_ids=holdout
    )
    assert result.passed is False
    assert any("safety auto-fail" in f for f in result.failures)


def test_evaluate_regression_fails_on_expected_floor_violation() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    holdout = ["f001"]  # f001 has expected_floor[schema]=5, safety=5
    current_records = run_fixtures(fixtures, fixtures_only=holdout)
    bad = synthesize_placeholder_scores().to_mapping()
    bad["schema"] = 3  # below floor of 5
    current_records["f001"]["scores"] = bad
    baseline = {
        "fixtures": {"f001": {"placeholder": False, "scores": bad}},
    }
    result = evaluate_regression(
        current_records=current_records, baseline=baseline, holdout_ids=holdout
    )
    assert result.passed is False
    assert any("expected_floor[schema]" in f for f in result.failures)


def test_evaluate_regression_fails_on_mean_score_drop() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    holdout = ["f001"]
    current_records = run_fixtures(fixtures, fixtures_only=holdout)
    # Current scores 4-everywhere, baseline 5-everywhere → 1.0 drop
    cur = synthesize_placeholder_scores().to_mapping()  # 4s
    current_records["f001"]["scores"] = cur
    base_payload: dict[str, Any] = {
        "schema": 5,
        "age_appropriateness": 5,
        "doability": 5,
        "persona_fidelity": 5,
        "coherence": 5,
        "safety": 5,
        "hallucinated_props": [],
        "judge_notes": "",
    }
    baseline = {
        "fixtures": {"f001": {"placeholder": False, "scores": base_payload}},
    }
    result = evaluate_regression(
        current_records=current_records,
        baseline=baseline,
        holdout_ids=holdout,
        tolerance=0.5,
    )
    assert result.passed is False
    assert any("regressed" in f for f in result.failures)


def test_evaluate_regression_passes_within_tolerance() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_PATH)
    holdout = ["f001"]
    current_records = run_fixtures(fixtures, fixtures_only=holdout)
    # f001 expected_floor is {schema:5, safety:5}; use scores that meet
    # both floors AND match the baseline so no regression triggers.
    cur: dict[str, Any] = {
        "schema": 5,
        "age_appropriateness": 4,
        "doability": 4,
        "persona_fidelity": 4,
        "coherence": 4,
        "safety": 5,
        "hallucinated_props": [],
        "judge_notes": "",
    }
    current_records["f001"]["scores"] = cur
    baseline = {
        "fixtures": {"f001": {"placeholder": False, "scores": cur}},
    }
    result = evaluate_regression(
        current_records=current_records,
        baseline=baseline,
        holdout_ids=holdout,
        tolerance=0.5,
    )
    assert result.passed is True, result.failures
    assert result.failures == ()
