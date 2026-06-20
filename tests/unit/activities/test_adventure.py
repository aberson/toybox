"""Phase W Step W4 — unit tests for the dynamic adventure beat engine.

These exercise the pure engine in :mod:`toybox.activities.adventure` with no
network and no DB:

* offline ``generate_next_beat`` is deterministic for a fixed seed,
* the offline beat reflects the prior choice carried in ``history``,
* linear → no choices; non-linear → 2-3 choices,
* the online path parses a stubbed reply and degrades to offline on any
  failure (malformed reply / raising call).
"""

from __future__ import annotations

import json

from toybox.activities.adventure import (
    ADVENTURE_BEAT_KIND,
    MAX_ADVENTURE_BEATS,
    GeneratedBeat,
    generate_next_beat,
    parse_online_beat,
)

CAST = ("Penguin", "Robot")


def _offline(
    history: tuple[str, ...], *, beat_index: int, linear: bool, seed: int
) -> GeneratedBeat:
    return generate_next_beat(
        history,
        "",
        CAST,
        online=False,
        beat_index=beat_index,
        linear=linear,
        seed=seed,
    )


def test_offline_beat_is_deterministic_for_fixed_seed() -> None:
    """Same (seed, beat_index, history, cast) → byte-identical beat."""
    a = _offline((), beat_index=0, linear=False, seed=7)
    b = _offline((), beat_index=0, linear=False, seed=7)
    assert a == b
    assert a.kind == ADVENTURE_BEAT_KIND


def test_offline_beat_varies_with_seed() -> None:
    """Different seeds generally yield different opening text."""
    seeds = {_offline((), beat_index=0, linear=False, seed=s).body for s in range(8)}
    # Not all seeds must differ, but the engine must not collapse to one
    # single body across the whole range.
    assert len(seeds) > 1


def test_offline_beat_reflects_prior_choice() -> None:
    """The beat body echoes the most-recent choice from history."""
    beat = _offline(("Ask Robot for help",), beat_index=1, linear=False, seed=7)
    assert "Ask Robot for help" in beat.body


def test_offline_opening_beat_has_no_prior_choice() -> None:
    """Beat 0 (empty history) uses an opener, not a transition."""
    beat = _offline((), beat_index=0, linear=False, seed=3)
    assert "adventure" in beat.body.lower()


def test_linear_offline_beat_has_no_choices() -> None:
    beat = _offline(("X",), beat_index=1, linear=True, seed=11)
    assert beat.choices is None


def test_nonlinear_offline_beat_has_2_or_3_choices() -> None:
    for beat_index in range(MAX_ADVENTURE_BEATS):
        beat = _offline(("X",), beat_index=beat_index, linear=False, seed=11)
        assert beat.choices is not None
        assert 2 <= len(beat.choices) <= 3
        # Choices must be distinct labels.
        assert len(set(beat.choices)) == len(beat.choices)


def test_empty_cast_falls_back_to_generic_descriptors() -> None:
    """An empty cast still produces a coherent, non-empty beat."""
    beat = generate_next_beat(
        (),
        "",
        (),
        online=False,
        beat_index=0,
        linear=False,
        seed=2,
    )
    assert beat.body
    assert beat.choices is not None


# ---------------------------------------------------------------------------
# Online path — parse + degrade-to-offline.
# ---------------------------------------------------------------------------


def test_online_path_parses_stubbed_reply() -> None:
    """A well-formed JSON reply is parsed into the GeneratedBeat."""

    def _call(_system: str, _user: str) -> str:
        return json.dumps({"body": "The dragon smiled kindly.", "choices": ["Wave hello", "Hide"]})

    beat = generate_next_beat(
        ("Open the door",),
        "the kid said dragon",
        CAST,
        online=True,
        beat_index=1,
        linear=False,
        seed=5,
        online_call=_call,
    )
    assert beat.body == "The dragon smiled kindly."
    assert beat.choices == ("Wave hello", "Hide")
    assert beat.kind == ADVENTURE_BEAT_KIND


def test_online_path_degrades_to_offline_on_malformed_reply() -> None:
    """A non-JSON reply falls back to the deterministic offline assembly."""

    def _bad_call(_system: str, _user: str) -> str:
        return "not json at all"

    online = generate_next_beat(
        (),
        "",
        CAST,
        online=True,
        beat_index=0,
        linear=False,
        seed=9,
        online_call=_bad_call,
    )
    offline = _offline((), beat_index=0, linear=False, seed=9)
    assert online == offline


def test_online_path_degrades_to_offline_when_call_raises() -> None:
    """A raising transport falls back to the offline assembly."""

    def _raise(_system: str, _user: str) -> str:
        raise RuntimeError("timeout")

    online = generate_next_beat(
        ("Go left",),
        "",
        CAST,
        online=True,
        beat_index=2,
        linear=True,
        seed=4,
        online_call=_raise,
    )
    offline = _offline(("Go left",), beat_index=2, linear=True, seed=4)
    assert online == offline
    assert online.choices is None


def test_online_path_degrades_to_offline_on_empty_reply() -> None:
    """An empty-string reply (and an empty-body JSON object) both fall back
    to the deterministic offline assembly — the parser rejects an empty
    ``body`` so ``generate_next_beat`` degrades rather than rendering blank.
    """
    offline = _offline((), beat_index=0, linear=False, seed=9)

    def _empty_string(_system: str, _user: str) -> str:
        return ""

    def _empty_body(_system: str, _user: str) -> str:
        return json.dumps({"body": ""})

    for call in (_empty_string, _empty_body):
        beat = generate_next_beat(
            (),
            "",
            CAST,
            online=True,
            beat_index=0,
            linear=False,
            seed=9,
            online_call=call,
        )
        assert beat == offline


def test_online_no_call_uses_offline() -> None:
    """online=True but online_call=None → offline assembly (no network)."""
    online = generate_next_beat(
        (),
        "",
        CAST,
        online=True,
        beat_index=0,
        linear=False,
        seed=1,
        online_call=None,
    )
    assert online == _offline((), beat_index=0, linear=False, seed=1)


def test_parse_online_beat_linear_drops_choices() -> None:
    """When linear, parse forces choices=None even if the model sent some."""
    raw = json.dumps({"body": "Onward!", "choices": ["a", "b"]})
    beat = parse_online_beat(raw, linear=True)
    assert beat.choices is None
    assert beat.body == "Onward!"


def test_parse_online_beat_rejects_too_few_choices() -> None:
    """A non-linear reply with <2 choices raises (caller degrades to offline)."""
    raw = json.dumps({"body": "x", "choices": ["only one"]})
    try:
        parse_online_beat(raw, linear=False)
    except ValueError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError for <2 choices")
