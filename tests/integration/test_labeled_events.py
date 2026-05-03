"""ChatML serialization + labeled_events recorder integration tests.

Covers ``toybox.ai.labeled_events``: build_chatml_messages,
serialize_chatml, record_generation, update_parent_signal,
update_judge_scores, and the should_judge sampler.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.activities.generator import generate
from toybox.ai.labeled_events import (
    DEFAULT_JUDGE_RATE,
    GENERATOR_PATH_CLAUDE,
    GENERATOR_PATH_OFFLINE,
    JUDGE_RATE_ENV,
    PARENT_SIGNAL_DISMISS,
    PARENT_SIGNAL_END_EARLY,
    PARENT_SIGNAL_THUMBS_UP,
    GeneratorContext,
    build_chatml_messages,
    record_generation,
    resolve_judge_rate,
    serialize_chatml,
    should_judge,
    update_judge_scores,
    update_parent_signal,
)
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


# --------------------------------------------------------------------- ChatML


def test_build_chatml_includes_system_and_user() -> None:
    ctx = GeneratorContext(
        intent="boredom",
        slot=None,
        transcript_window="I'm bored",
        persona_id="mr_unicorn",
        persona_card="Mr. Unicorn — playful, gentle.",
        available_toys=("stuffed_unicorn",),
        available_rooms=("living_room",),
        child_profile={"age": 5},
    )
    messages = build_chatml_messages(ctx)
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert "Persona card" in messages[0].content
    assert messages[1].role == "user"
    user_payload = json.loads(messages[1].content)
    assert user_payload["intent"] == "boredom"
    assert user_payload["available_toys"] == ["stuffed_unicorn"]
    assert user_payload["child_profile"] == {"age": 5}


def test_chatml_user_content_is_canonical_json() -> None:
    """Sorted-keys + same input → byte-identical output (Phase E dedup)."""
    ctx = GeneratorContext(
        intent="boredom",
        available_toys=("a", "b"),
    )
    a = build_chatml_messages(ctx)[1].content
    b = build_chatml_messages(ctx)[1].content
    assert a == b
    # sorted keys: "available_rooms" comes before "available_toys"
    assert a.index('"available_rooms"') < a.index('"available_toys"')


def test_serialize_chatml_round_trips() -> None:
    ctx = GeneratorContext(intent="boredom")
    messages = build_chatml_messages(ctx)
    text = serialize_chatml(messages)
    decoded = json.loads(text)
    assert isinstance(decoded, list)
    assert decoded[0]["role"] == "system"


# --------------------------------------------------------------------- recorder


def _sample_activity() -> object:
    return generate(
        intent="boredom",
        slot=None,
        context={"unit": "test"},
        hour=12,
        seed=1,
    )


def test_record_generation_inserts_row(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    ctx = GeneratorContext(intent="boredom")
    row_id = record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=ctx,
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    assert row_id > 0
    row = conn.execute(
        "SELECT * FROM labeled_events WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["activity_id"] == activity.id  # type: ignore[attr-defined]
    assert row["generator_path"] == GENERATOR_PATH_OFFLINE
    # ChatML payload is a JSON list with at least one system message
    chatml = json.loads(row["inputs_chatml_json"])
    assert chatml[0]["role"] == "system"
    # Activity payload round-trips
    activity_payload = json.loads(row["activity_json"])
    assert activity_payload["id"] == activity.id  # type: ignore[attr-defined]
    # Signal + judge fields start NULL
    assert row["parent_signal"] is None
    assert row["judge_scores_json"] is None


def test_record_generation_rejects_bad_path(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    ctx = GeneratorContext(intent="boredom")
    with pytest.raises(ValueError):
        record_generation(
            conn,
            activity=activity,  # type: ignore[arg-type]
            ctx=ctx,
            generator_path="bogus",
        )


def test_record_generation_accepts_claude_path(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    ctx = GeneratorContext(intent="boredom")
    row_id = record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=ctx,
        generator_path=GENERATOR_PATH_CLAUDE,
    )
    row = conn.execute(
        "SELECT generator_path FROM labeled_events WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["generator_path"] == GENERATOR_PATH_CLAUDE


def test_update_parent_signal_thumbs_up(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=GeneratorContext(intent="boredom"),
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    updated = update_parent_signal(
        conn,
        activity_id=activity.id,  # type: ignore[attr-defined]
        signal=PARENT_SIGNAL_THUMBS_UP,
    )
    assert updated is True
    row = conn.execute(
        "SELECT parent_signal, parent_signal_set_at, ended_at_step "
        "FROM labeled_events WHERE activity_id = ?",
        (activity.id,),  # type: ignore[attr-defined]
    ).fetchone()
    assert row["parent_signal"] == PARENT_SIGNAL_THUMBS_UP
    assert row["parent_signal_set_at"] is not None
    assert row["ended_at_step"] is None


def test_update_parent_signal_dismiss(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=GeneratorContext(intent="boredom"),
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    update_parent_signal(
        conn,
        activity_id=activity.id,  # type: ignore[attr-defined]
        signal=PARENT_SIGNAL_DISMISS,
    )
    row = conn.execute(
        "SELECT parent_signal FROM labeled_events WHERE activity_id = ?",
        (activity.id,),  # type: ignore[attr-defined]
    ).fetchone()
    assert row["parent_signal"] == PARENT_SIGNAL_DISMISS


def test_update_parent_signal_end_early_persists_step(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=GeneratorContext(intent="boredom"),
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    update_parent_signal(
        conn,
        activity_id=activity.id,  # type: ignore[attr-defined]
        signal=PARENT_SIGNAL_END_EARLY,
        ended_at_step=3,
    )
    row = conn.execute(
        "SELECT parent_signal, ended_at_step FROM labeled_events WHERE activity_id = ?",
        (activity.id,),  # type: ignore[attr-defined]
    ).fetchone()
    assert row["parent_signal"] == PARENT_SIGNAL_END_EARLY
    assert row["ended_at_step"] == 3


def test_update_parent_signal_missing_row_is_safe(conn: sqlite3.Connection) -> None:
    """No labeled_events row for this id → returns False, doesn't raise."""
    updated = update_parent_signal(
        conn,
        activity_id="never-seen",
        signal=PARENT_SIGNAL_THUMBS_UP,
    )
    assert updated is False


def test_update_judge_scores(conn: sqlite3.Connection) -> None:
    activity = _sample_activity()
    record_generation(
        conn,
        activity=activity,  # type: ignore[arg-type]
        ctx=GeneratorContext(intent="boredom"),
        generator_path=GENERATOR_PATH_OFFLINE,
    )
    payload = json.dumps({"safety": 5, "schema": 4})
    updated = update_judge_scores(
        conn,
        activity_id=activity.id,  # type: ignore[attr-defined]
        judge_scores_json=payload,
    )
    assert updated is True
    row = conn.execute(
        "SELECT judge_scores_json, judge_run_at FROM labeled_events WHERE activity_id = ?",
        (activity.id,),  # type: ignore[attr-defined]
    ).fetchone()
    assert row["judge_scores_json"] == payload
    assert row["judge_run_at"] is not None


# --------------------------------------------------------------------- sampler


def test_resolve_judge_rate_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JUDGE_RATE_ENV, "10")
    assert resolve_judge_rate() == 10


def test_resolve_judge_rate_clamps_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JUDGE_RATE_ENV, "-3")
    assert resolve_judge_rate() == 0


def test_resolve_judge_rate_falls_back_on_garbage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(JUDGE_RATE_ENV, "fish")
    assert resolve_judge_rate() == DEFAULT_JUDGE_RATE


def test_should_judge_sampling() -> None:
    # rate=5 → ids 5, 10, 15 are judged; others not
    assert should_judge(5, rate=5) is True
    assert should_judge(10, rate=5) is True
    assert should_judge(7, rate=5) is False
    # rate=1 → every id is judged
    assert should_judge(1, rate=1) is True
    assert should_judge(2, rate=1) is True
    # rate=0 → never
    assert should_judge(5, rate=0) is False


