"""Phase W Step W4 — dynamic adventure beat engine.

An *adventure* is an ordinary activity (``activities.adventure=1``) whose
steps are GENERATED beat-by-beat as the child advances, instead of being
read from a fixed template. Generation is HYBRID:

* **ONLINE** (``online=True``) — a capability-gated Claude call takes the
  child's prior choices + the recently-heard transcript window and returns
  a single next beat. On ANY failure / timeout / gate-not-green it falls
  back to the offline assembly. The actual Claude transport lives in the
  API layer (it needs the FastAPI-injected sync client); this module owns
  the *prompt build* + *parse* so the engine is unit-testable with no
  network.
* **OFFLINE** (``online=False``) — a deterministic beat assembled from
  ``(seed, beat_index, prior choice, cast, theme)`` using the existing
  Phase K content building blocks (:mod:`toybox.activities.roles`,
  :mod:`toybox.activities.generic_descriptors`,
  :mod:`toybox.activities.themes`). Same inputs → same beat, so a replay
  is byte-identical. The beat text reflects the prior choice carried in
  ``history``.

When the household ``game_linearity`` dial is ``"linear"`` the engine emits
NO choices (``choices=None``); otherwise it emits 2-3 deterministic choice
labels.

The whole engine is pure + side-effect-free; the API layer (post_advance /
_do_propose in :mod:`toybox.api.activities`) is responsible for persistence
and for actually performing the Claude transport.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .generic_descriptors import GENERIC_DESCRIPTORS
from .themes import Theme

# The kind discriminator the kiosk dispatches on for a generated beat.
# Free-form (no migration for the kind itself — see code-quality.md §3 and
# the activity_steps.kind convention 0016 set). The kiosk StepCard renders
# this kind through its default text/fork path (body + choices + Next).
ADVENTURE_BEAT_KIND: str = "adventure_beat"

# Termination bound. After this many beats the adventure routes to the
# normal reward/terminal/end path (the API layer enforces this). Six keeps
# a hybrid online/offline adventure short enough to hold a young child's
# attention without an explicit ending template.
MAX_ADVENTURE_BEATS: int = 6


@dataclass(frozen=True, slots=True)
class GeneratedBeat:
    """One generated adventure beat.

    * ``body`` — the rendered beat text shown to the child.
    * ``choices`` — 2-3 choice labels for a non-linear beat, or ``None``
      when the adventure is linear (no branching buttons).
    * ``kind`` — always :data:`ADVENTURE_BEAT_KIND`; carried on the object
      so the API layer can persist it verbatim onto ``activity_steps.kind``.
    """

    body: str
    choices: tuple[str, ...] | None
    kind: str = ADVENTURE_BEAT_KIND


# Deterministic opening + transition phrasing. Kept as plain tuples (no
# new corpus) so the offline assembly is fully reproducible per seed. The
# cast names + theme word are spliced in; the prior choice (from history)
# is echoed so the beat visibly builds on what the child picked.
_OPENERS: tuple[str, ...] = (
    "Your {theme} adventure begins! {hero} stands at the edge of something new.",
    "Once upon a time, {hero} set off on a great {theme} adventure.",
    "The {theme} adventure starts now — {hero} takes a deep breath and steps forward.",
)

_TRANSITIONS: tuple[str, ...] = (
    "Because you chose “{choice}”, {hero} heads onward.",
    "After “{choice}”, a new part of the {theme} adventure opens up for {hero}.",
    "“{choice}” it is! {hero} keeps going, and {ally} comes along too.",
    "Following “{choice}”, {hero} and {ally} find something surprising.",
)

# Choice-label fragments. Combined with the theme + cast so the same
# (seed, beat_index) always yields the same labels.
_CHOICE_FRAGMENTS: tuple[str, ...] = (
    "Explore the {theme} path",
    "Ask {ally} for help",
    "Look for a hidden door",
    "Call out and listen",
    "Take the brave shortcut",
    "Search the {theme} forest",
)

# Default cast when the household has no toys resolved — drawn from the
# generic-descriptor table so we never invent a new corpus.
_DEFAULT_HERO: str = GENERIC_DESCRIPTORS["friend"]
_DEFAULT_ALLY: str = GENERIC_DESCRIPTORS["sidekick"]


def _stable_index(seed: int, beat_index: int, salt: str, modulo: int) -> int:
    """Deterministic index in ``[0, modulo)`` from (seed, beat_index, salt).

    Uses a SHA-256 digest rather than Python's salted ``hash`` so the
    result is stable across processes (``PYTHONHASHSEED`` independence) —
    a replay of the same adventure must produce byte-identical beats.
    """
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(f"{seed}:{beat_index}:{salt}".encode()).hexdigest()
    return int(digest, 16) % modulo


def _theme_for(seed: int) -> str:
    """Pick a deterministic theme word for the whole adventure.

    Keyed on the seed only (NOT the beat index or cast) so a single
    adventure keeps one theme across all its beats. Returns the theme's
    string value (e.g. ``"adventure"``).
    """
    themes = tuple(Theme)
    return str(themes[_stable_index(seed, 0, "theme", len(themes))].value)


def _cast_names(cast: tuple[str, ...]) -> tuple[str, str]:
    """Resolve (hero, ally) display names from the supplied cast.

    Falls back to generic descriptors when the cast is empty or short, so
    a household with no toys still gets a coherent, deterministic beat.
    """
    hero = cast[0] if len(cast) >= 1 and cast[0] else _DEFAULT_HERO
    ally = cast[1] if len(cast) >= 2 and cast[1] else _DEFAULT_ALLY
    return hero, ally


def _offline_choices(seed: int, beat_index: int, theme: str, ally: str) -> tuple[str, ...]:
    """Deterministically pick 2-3 distinct choice labels.

    The count (2 or 3) and the specific fragments are both derived from
    (seed, beat_index) so a replay yields identical buttons.
    """
    count = 2 + _stable_index(seed, beat_index, "choice_count", 2)  # 2 or 3
    labels: list[str] = []
    used: set[int] = set()
    # Walk deterministic offsets until we have ``count`` distinct fragments.
    offset = 0
    while len(labels) < count and offset < len(_CHOICE_FRAGMENTS) * 2:
        idx = _stable_index(seed, beat_index, f"choice_{offset}", len(_CHOICE_FRAGMENTS))
        if idx not in used:
            used.add(idx)
            labels.append(_CHOICE_FRAGMENTS[idx].format(theme=theme, ally=ally))
        offset += 1
    # Defensive: if dedup somehow starved us, top up by linear scan.
    for frag in _CHOICE_FRAGMENTS:
        if len(labels) >= count:
            break
        rendered = frag.format(theme=theme, ally=ally)
        if rendered not in labels:
            labels.append(rendered)
    return tuple(labels[:count])


def _prior_choice(history: tuple[str, ...]) -> str | None:
    """Return the most-recent choice label from ``history`` (or None)."""
    for entry in reversed(history):
        if entry:
            return entry
    return None


def _assemble_offline_beat(
    history: tuple[str, ...],
    cast: tuple[str, ...],
    *,
    beat_index: int,
    linear: bool,
    seed: int,
) -> GeneratedBeat:
    """Deterministic offline beat assembly.

    Same inputs → same beat. The body reflects the prior choice (the most
    recent non-empty entry in ``history``) when one exists; the opening
    beat (no prior choice) uses an opener phrase instead.
    """
    theme = _theme_for(seed)
    hero, ally = _cast_names(cast)
    prior = _prior_choice(history)

    if prior is None:
        template = _OPENERS[_stable_index(seed, beat_index, "opener", len(_OPENERS))]
        body = template.format(theme=theme, hero=hero, ally=ally)
    else:
        template = _TRANSITIONS[_stable_index(seed, beat_index, "transition", len(_TRANSITIONS))]
        body = template.format(theme=theme, hero=hero, ally=ally, choice=prior)

    choices: tuple[str, ...] | None
    if linear:
        choices = None
    else:
        choices = _offline_choices(seed, beat_index, theme, ally)
    return GeneratedBeat(body=body, choices=choices, kind=ADVENTURE_BEAT_KIND)


# ---------------------------------------------------------------------------
# Online prompt build + parse. These live here (NOT in the API layer) so the
# engine is unit-testable; the API layer performs the actual transport using
# the FastAPI-injected sync client and the W3 timeout/breaker pattern.
# ---------------------------------------------------------------------------

_ONLINE_SYSTEM: str = (
    "You are a gentle storyteller writing ONE short beat of an interactive "
    "adventure for a young child (ages 4-7). Keep it to 1-3 simple sentences, "
    "warm and never scary. You will be given the story so far (the child's "
    "prior choices) and a few words the child recently said out loud. "
    "Reply with STRICT JSON only: "
    '{"body": "<the beat text>", "choices": ["<choice 1>", "<choice 2>"]}. '
    "Provide 2 or 3 short choice labels when choices are requested, or an "
    "empty list when they are not. No prose outside the JSON."
)


def build_online_prompt(
    history: tuple[str, ...],
    transcript_window: str,
    cast: tuple[str, ...],
    *,
    beat_index: int,
    linear: bool,
    seed: int,
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for the online beat call.

    The user payload is JSON so the model has a stable, parseable input
    contract. ``linear`` is surfaced so the model knows whether to emit
    choices. Returns ``(system, user)``.
    """
    theme = _theme_for(seed)
    user = json.dumps(
        {
            "theme": theme,
            "cast": list(cast),
            "story_so_far": list(history),
            "child_recently_said": transcript_window,
            "beat_number": beat_index + 1,
            "max_beats": MAX_ADVENTURE_BEATS,
            "wants_choices": not linear,
        },
        ensure_ascii=False,
    )
    return _ONLINE_SYSTEM, user


def parse_online_beat(raw: str, *, linear: bool) -> GeneratedBeat:
    """Parse a model reply into a :class:`GeneratedBeat`.

    Raises :class:`ValueError` on any malformed reply (non-JSON, missing /
    empty body, wrong types) so the API caller falls back to the offline
    assembly. When ``linear`` the choices are forced to ``None`` regardless
    of what the model returned (the household dial is authoritative).
    """
    try:
        payload: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"adventure beat reply not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("adventure beat reply is not a JSON object")
    body_raw = payload.get("body")
    if not isinstance(body_raw, str) or not body_raw.strip():
        raise ValueError("adventure beat reply missing a non-empty 'body'")
    body = body_raw.strip()

    if linear:
        return GeneratedBeat(body=body, choices=None, kind=ADVENTURE_BEAT_KIND)

    choices_raw = payload.get("choices")
    if not isinstance(choices_raw, list):
        raise ValueError("adventure beat reply 'choices' is not a list")
    choices = tuple(str(c).strip() for c in choices_raw if isinstance(c, str) and str(c).strip())
    if len(choices) < 2:
        raise ValueError("non-linear adventure beat needs at least 2 choices")
    # Cap at 3 to match the offline assembly + StepCard layout budget.
    return GeneratedBeat(body=body, choices=choices[:3], kind=ADVENTURE_BEAT_KIND)


def generate_next_beat(
    history: tuple[str, ...],
    transcript_window: str,
    cast: tuple[str, ...],
    *,
    online: bool,
    beat_index: int,
    linear: bool,
    seed: int,
    online_call: Any = None,
) -> GeneratedBeat:
    """Generate the next adventure beat.

    Args:
        history: The child's prior choice labels, oldest-first. The most
            recent non-empty entry drives the offline beat's transition
            text. Empty on the opening beat.
        transcript_window: Recently-heard speech (used online only).
        cast: Resolved character display names (toy names + generic
            descriptors). ``cast[0]`` is the hero, ``cast[1]`` the ally;
            both fall back to generic descriptors when absent.
        online: When True, attempt the Claude path via ``online_call``;
            fall back to the offline assembly on ANY failure. When False,
            assemble deterministically offline.
        beat_index: 0-based index of the beat being generated.
        linear: When True, emit NO choices.
        seed: Determinism seed — same (seed, beat_index, history, cast)
            yields the same offline beat.
        online_call: Optional callable ``(system: str, user: str) -> str``
            performing the Claude transport (the API layer injects this,
            wrapped in the W3 timeout + circuit-breaker pattern). When
            ``online`` is True and this is provided, its reply is parsed by
            :func:`parse_online_beat`; any exception (transport, timeout,
            malformed reply) degrades to the offline assembly. When
            ``online`` is True but ``online_call`` is None, the offline
            assembly is used (no network is reachable from this pure
            module).

    Returns:
        The next :class:`GeneratedBeat`.
    """
    if online and online_call is not None:
        try:
            system, user = build_online_prompt(
                history,
                transcript_window,
                cast,
                beat_index=beat_index,
                linear=linear,
                seed=seed,
            )
            raw = online_call(system, user)
            return parse_online_beat(raw, linear=linear)
        except Exception:  # noqa: BLE001 -- ANY online failure degrades to offline
            pass
    return _assemble_offline_beat(
        history,
        cast,
        beat_index=beat_index,
        linear=linear,
        seed=seed,
    )


def stable_index(seed: int, beat_index: int, salt: str, modulo: int) -> int:
    """Public wrapper over :func:`_stable_index`.

    Exposed so the API layer can derive a stable per-adventure seed from the
    activity id using the SAME SHA-256-mod algorithm (single source of
    truth — code-quality.md §2) instead of re-implementing it.
    """
    return _stable_index(seed, beat_index, salt, modulo)


__all__ = [
    "ADVENTURE_BEAT_KIND",
    "MAX_ADVENTURE_BEATS",
    "GeneratedBeat",
    "build_online_prompt",
    "generate_next_beat",
    "parse_online_beat",
    "stable_index",
]
