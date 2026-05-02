"""Offline activity generator.

The generator is fully deterministic given ``(intent, slot, context,
hour, seed)``: same inputs produce the byte-for-byte identical
:class:`Activity` (including the ``id`` field). It does not call
Claude, does not touch the database, and does not depend on the wall
clock. It is the fallback used by listening modes 1, 3 (when Claude
is not capable), and 4-5 when the breaker is open.

Template selection algorithm
----------------------------

1. Load the template library for the given intent (one JSON file per
   intent under :mod:`toybox.activities.templates`). The library is
   schema-validated and cached after first load.
2. Filter templates whose ``buckets`` declaration is eligible at the
   given ``hour`` (see :mod:`toybox.activities.time_of_day`).
3. If no templates remain, fall back to the intent's ``"always"``
   templates regardless of bucket. If still empty, fall back to the
   ``boredom`` intent's ``"always"`` templates. If THAT is empty (it
   should never be — the shipped library has at least one ``always``
   template per intent), raise.
4. Sort the eligible list by ``template_id`` for determinism, then
   pick one with a seeded ``random.Random(seed)``.

Slot substitution
-----------------

Templates may include ``{slot}`` and ``{toy}`` placeholders.

* ``{slot}`` is replaced by the caller's ``slot`` argument when
  provided. When ``slot is None``, ``{slot}`` is replaced by the
  literal string ``"adventure"`` so step text remains coherent —
  templates that absolutely require a slot should include their
  ``{slot}`` placeholder anyway, since substitution defaults are
  Phase A scaffolding (Phase C step 18 wires real toys / rooms /
  banned themes).
* ``{toy}`` is replaced by the literal Phase A placeholder
  ``"Mr. Unicorn"`` per ``documentation/plan.md`` issue #7 notes.

The set of substituted slot values is collected in sorted order into
``Activity.metadata["slot_values"]`` as a ``tuple[str, ...]`` — this
is the load-bearing input to Phase D step 19's ``signature``
computation, and both the sort and the tuple immutability make the
signature stable against slot ordering and post-construction
mutation.

Determinism of ``Activity.id``
------------------------------

``uuid.uuid4()`` is non-deterministic, so the generator derives a UUID
from the SHA-256 of a canonical input string and forces the version
nibble to ``4`` plus the variant bits to RFC-4122. Same inputs +
seed + selected template_id → same UUID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .models import Activity, ActivityStep
from .time_of_day import ALWAYS_BUCKET, hour_bucket, is_eligible

_logger = logging.getLogger(__name__)

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
TEMPLATES_DIR: Final[Path] = _PACKAGE_DIR / "templates"
SCHEMA_FILENAME: Final[str] = "_schema.json"

# Phase A placeholder per plan.md issue #7: "toys = ['Mr. Unicorn']".
DEFAULT_TOY_NAME: Final[str] = "Mr. Unicorn"

# Used when caller passes slot=None and a template still has {slot}.
DEFAULT_SLOT_FILLER: Final[str] = "adventure"

SUPPORTED_INTENTS: Final[tuple[str, ...]] = (
    "request_play",
    "request_story",
    "request_activity",
    "boredom",
)

# Final fallback intent if everything else fails to match.
FALLBACK_INTENT: Final[str] = "boredom"


@dataclass(frozen=True, slots=True)
class _StepTemplate:
    text: str
    sfx: str | None
    expected_action: str | None


@dataclass(frozen=True, slots=True)
class _Template:
    id: str
    title: str
    buckets: frozenset[str]
    steps: tuple[_StepTemplate, ...]


# Cache: (templates_dir, intent) -> list of loaded templates.
# Populated lazily on first call and re-used for the lifetime of the
# process. Keying on the directory path means a test that monkeypatches
# ``TEMPLATES_DIR`` to a fresh fixture path automatically gets fresh
# loads — previously the cache leaked stale templates across runs if
# the caller forgot to call ``clear_template_cache``.
_TEMPLATE_CACHE: dict[tuple[Path, str], list[_Template]] = {}
_SCHEMA_VALIDATOR_CACHE: dict[Path, Draft202012Validator] = {}


def _load_schema() -> Draft202012Validator:
    cached = _SCHEMA_VALIDATOR_CACHE.get(TEMPLATES_DIR)
    if cached is not None:
        return cached
    schema_path = TEMPLATES_DIR / SCHEMA_FILENAME
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    _SCHEMA_VALIDATOR_CACHE[TEMPLATES_DIR] = validator
    return validator


def _parse_template(raw: dict[str, Any]) -> _Template:
    steps_raw = raw["steps"]
    steps = tuple(
        _StepTemplate(
            text=str(s["text"]),
            sfx=s.get("sfx"),
            expected_action=s.get("expected_action"),
        )
        for s in steps_raw
    )
    return _Template(
        id=str(raw["id"]),
        title=str(raw["title"]),
        buckets=frozenset(raw.get("buckets", []) or []),
        steps=steps,
    )


def _load_intent_templates(intent: str) -> list[_Template]:
    """Load and validate templates for ``intent``. Caches per-(dir, intent)."""
    cache_key = (TEMPLATES_DIR, intent)
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if intent not in SUPPORTED_INTENTS:
        # Caller passed an unknown intent string — treat as empty so the
        # caller's fallback path takes over.
        _TEMPLATE_CACHE[cache_key] = []
        return []

    path = TEMPLATES_DIR / f"{intent}.json"
    if not path.is_file():
        _logger.warning("intent template file missing: %s", path)
        _TEMPLATE_CACHE[cache_key] = []
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _logger.warning("intent template JSON malformed, skipping: %s (%s)", path, exc)
        _TEMPLATE_CACHE[cache_key] = []
        return []

    validator = _load_schema()
    try:
        validator.validate(payload)
    except ValidationError as exc:
        _logger.warning(
            "intent template file failed schema validation, skipping: %s (%s)",
            path,
            exc.message,
        )
        _TEMPLATE_CACHE[cache_key] = []
        return []

    if payload["intent"] != intent:
        _logger.warning(
            "intent template file %s has mismatched intent %r (expected %r), skipping",
            path,
            payload["intent"],
            intent,
        )
        _TEMPLATE_CACHE[cache_key] = []
        return []

    templates = [_parse_template(t) for t in payload["templates"]]
    # Sort by id so that downstream selection is deterministic
    # regardless of file order.
    templates.sort(key=lambda t: t.id)
    _TEMPLATE_CACHE[cache_key] = templates
    return templates


def _filter_eligible(templates: list[_Template], hour: int) -> list[_Template]:
    return [t for t in templates if is_eligible(set(t.buckets), hour)]


def _filter_always(templates: list[_Template]) -> list[_Template]:
    return [t for t in templates if ALWAYS_BUCKET in t.buckets]


def _select_template(
    intent: str,
    hour: int,
    rng: random.Random,
) -> tuple[_Template, str]:
    """Pick one template. Returns ``(template, source_intent)``.

    ``source_intent`` is the intent whose template pool was actually
    used — usually equal to ``intent``, but distinct after a fallback.
    """
    primary = _load_intent_templates(intent)
    eligible = _filter_eligible(primary, hour)
    if eligible:
        eligible.sort(key=lambda t: t.id)
        return rng.choice(eligible), intent

    # Fallback 1: any "always" template within the requested intent.
    always_primary = _filter_always(primary)
    if always_primary:
        always_primary.sort(key=lambda t: t.id)
        _logger.info(
            "no eligible templates for intent=%s hour=%d; falling back to intent's always pool",
            intent,
            hour,
        )
        return rng.choice(always_primary), intent

    # Fallback 2: boredom intent's always pool (final safety net).
    if intent != FALLBACK_INTENT:
        boredom = _load_intent_templates(FALLBACK_INTENT)
        always_boredom = _filter_always(boredom)
        if always_boredom:
            always_boredom.sort(key=lambda t: t.id)
            _logger.info(
                "no templates for intent=%s; falling back to boredom always pool",
                intent,
            )
            return rng.choice(always_boredom), FALLBACK_INTENT

    raise RuntimeError(
        f"no offline templates available for intent={intent!r} hour={hour} "
        f"and no fallback templates either"
    )


def _substitute(text: str, *, slot: str | None, toy: str) -> tuple[str, list[str]]:
    """Substitute ``{slot}`` and ``{toy}`` placeholders.

    Returns ``(substituted_text, used_slots)`` where ``used_slots`` is
    the list of NON-DEFAULT slot values that were materially injected
    into the text. The ``{toy}`` placeholder uses the Phase A literal
    and does NOT contribute to ``used_slots`` — toys are tracked
    separately via ``activities.toy_ids`` in Phase C.
    """
    used: list[str] = []
    out = text

    if "{slot}" in out:
        if slot is not None and slot.strip():
            out = out.replace("{slot}", slot)
            used.append(slot)
        else:
            out = out.replace("{slot}", DEFAULT_SLOT_FILLER)

    if "{toy}" in out:
        out = out.replace("{toy}", toy)

    return out, used


def _derive_uuid(
    *,
    intent: str,
    slot: str | None,
    context: dict[str, Any] | None,
    hour: int,
    seed: int,
    template_id: str,
) -> str:
    """Deterministically derive a UUID4-shaped string from the inputs.

    Uses SHA-256 of a canonical JSON encoding (sorted keys), then
    forces the standard UUID-v4 variant + version bits. ``uuid.UUID``
    constructed from 16 bytes after these adjustments produces a
    well-formed v4 UUID string.

    ``context`` MUST be JSON-serialisable (its values must be only
    ``None``, ``bool``, ``int``, ``float``, ``str``, ``list``, or
    ``dict`` — i.e. things ``json.dumps`` accepts in default mode).
    Non-serialisable values raise :class:`TypeError` with the offending
    key in the message.
    """
    payload = {
        "intent": intent,
        "slot": slot,
        "context": context if context is not None else {},
        "hour": hour,
        "seed": seed,
        "template_id": template_id,
    }
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except TypeError as exc:
        # json.dumps raises plain TypeError without naming the offending
        # key; walk context to find the first non-serialisable entry so
        # the caller's error message is actionable.
        offending_key: str | None = None
        if context is not None:
            for k, v in context.items():
                try:
                    json.dumps(v)
                except TypeError:
                    offending_key = k
                    break
        raise TypeError(
            f"generate() context must be JSON-serialisable; offending key={offending_key!r} ({exc})"
        ) from exc
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    raw = bytearray(digest[:16])
    # Set version to 4 (random) — high nibble of byte 6.
    raw[6] = (raw[6] & 0x0F) | 0x40
    # Set variant to RFC 4122 — top two bits of byte 8 are 10.
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def generate(
    intent: str,
    slot: str | None,
    context: dict[str, Any] | None,
    hour: int,
    seed: int,
    *,
    persona_id: str | None = None,
) -> Activity:
    """Generate a deterministic 5-step :class:`Activity`.

    Args:
        intent: One of :data:`SUPPORTED_INTENTS`. Unknown intents are
            tolerated and fall through to the boredom always pool.
        slot: Optional slot value (e.g. ``"unicorns"``). When ``None``
            the placeholder ``"adventure"`` is substituted into
            ``{slot}`` occurrences and ``metadata.slot_values`` is
            empty.
        context: Optional caller-supplied context dict. Currently only
            used as part of the determinism key (so two calls with
            different contexts produce different UUIDs even if all
            other inputs match). Phase C step 18 will grow this to
            carry real toys, rooms, banned themes. Values MUST be
            JSON-serialisable (``None``, ``bool``, ``int``, ``float``,
            ``str``, ``list``, ``dict``) — otherwise :class:`TypeError`
            is raised with the offending key.
        hour: Current local hour (0..23). Drives template eligibility
            via :func:`toybox.activities.time_of_day.is_eligible`.
        seed: Integer seed for the internal :class:`random.Random`.
            Same ``(intent, slot, context, hour, seed)`` MUST produce
            byte-identical output.
        persona_id: Optional persona id to record on the activity.

    Returns:
        An immutable :class:`Activity` with exactly five
        :class:`ActivityStep` items.

    Raises:
        ValueError: if ``hour`` is outside ``0..23``.
        TypeError: if ``context`` contains a non-JSON-serialisable
            value.
        RuntimeError: if no offline templates can be loaded at all
            (indicates a packaging error — the shipped library always
            has at least one boredom-always template).
    """
    rng = random.Random(seed)

    template, _source_intent = _select_template(intent, hour, rng)

    # Substitute placeholders in title and steps.
    title_text, title_slots = _substitute(template.title, slot=slot, toy=DEFAULT_TOY_NAME)

    steps: list[ActivityStep] = []
    used_slots: list[str] = list(title_slots)
    for idx, step_tpl in enumerate(template.steps):
        body, step_slots = _substitute(step_tpl.text, slot=slot, toy=DEFAULT_TOY_NAME)
        used_slots.extend(step_slots)
        steps.append(
            ActivityStep(
                step_index=idx,
                text=body,
                sfx=step_tpl.sfx,
                expected_action=step_tpl.expected_action,
            )
        )

    activity_id = _derive_uuid(
        intent=intent,
        slot=slot,
        context=context,
        hour=hour,
        seed=seed,
        template_id=template.id,
    )

    metadata: dict[str, Any] = {
        # Sorted + deduped to keep determinism: callers depend on this
        # ordering for the Phase D signature. We use a tuple (not a
        # list) so the value is immutable — Pydantic respects tuples
        # as-is, and callers who try to ``.append`` will get an error
        # rather than silently corrupting a future signature.
        "slot_values": tuple(sorted(set(used_slots))),
        "hour_bucket": hour_bucket(hour).value,
    }

    return Activity(
        id=activity_id,
        template_id=template.id,
        persona_id=persona_id,
        title=title_text,
        steps=steps,
        version=1,
        metadata=metadata,
    )


def clear_template_cache() -> None:
    """Clear the in-process template + schema caches (test hook).

    Called automatically between tests via the ``_reset_cache``
    autouse fixture in ``test_offline_generator.py``. Production
    callers do not need to call this — the cache is keyed on
    ``TEMPLATES_DIR`` so a same-process change of the templates
    directory is already handled correctly.
    """
    _TEMPLATE_CACHE.clear()
    _SCHEMA_VALIDATOR_CACHE.clear()


__all__ = [
    "DEFAULT_SLOT_FILLER",
    "DEFAULT_TOY_NAME",
    "FALLBACK_INTENT",
    "SUPPORTED_INTENTS",
    "TEMPLATES_DIR",
    "clear_template_cache",
    "generate",
]
