"""Mode-aware Claude escalation dispatcher.

The dispatcher is the join point between Step 13's transcript pipeline
and the listening-mode contract from Step 4. For each above-floor
transcript + the matching :class:`Intent` list, it decides between:

* the offline activity generator (mode 1/2, or any mode whose Claude
  gate is closed),
* a Claude call (mode 3-5 when capability + breaker + throttle all
  pass),
* nothing at all (no triggers in modes 1-3 — there is no activity to
  generate).

A separate :meth:`maybe_fire_spontaneous` entry point is the seam Step
4's spontaneous timer plugs into for mode 4. It is identical to a
trigger-driven mode 3/4 dispatch except the intent is synthesized
("boredom") because no transcript fired it.

429 detection
-------------

The Anthropic SDK is not a hard dependency of this module — production
wires :class:`AnthropicClient`, tests wire :class:`StubClient` or a
custom fake. The dispatcher treats any exception with a ``status_code``
attribute equal to ``429``, OR any exception named
``RateLimitError`` / ``APIStatusError`` (with a 429 status), as a
rate-limit signal. ``Retry-After`` is read from the exception's
``response.headers`` when available, else falls through to the
breaker's default cooldown via ``record_429(retry_after=None)``.

This loose detection means we never have to import the SDK at module
load time, and tests can raise plain ``RateLimitedError`` instances
from a tiny fake without dragging in ``anthropic``.

Malformed Claude output
-----------------------

Claude is asked to produce an :class:`Activity` JSON. Any
:class:`pydantic.ValidationError` (or a non-JSON response) triggers an
offline fallback AND a ``Topic.system`` envelope with
``code='claude_output_invalid'``. The breaker is **not** opened —
malformed output is a content failure, not a transport failure, and
opening the breaker would punish the user for a one-off stochastic
bad response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import zlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Final

from pydantic import ValidationError

from ..activities.content_resolver import (
    ResolvedChildren,
    ResolvedRoom,
    ResolvedToy,
    build_claude_directive,
    resolve_child_profiles,
    resolve_rooms,
    resolve_toys,
)
from ..activities.generator import FALLBACK_INTENT, build_generator_context, generate
from ..activities.models import Activity
from ..ai.breaker import BreakerState, CircuitBreaker
from ..ai.client import AIClient, AIMessage, text_model
from ..ai.labeled_events import (
    GENERATOR_PATH_CLAUDE,
    GENERATOR_PATH_OFFLINE,
    GeneratorContext,
    schedule_judge_sample,
)
from ..audio.stt import Transcript
from ..core.capability import CapabilityReason
from ..core.listening import ListeningMode, Publisher
from ..core.throttle import MinIntervalThrottle
from ..triggers.registry import Intent
from ..ws.envelope import build_envelope
from ..ws.topics import Topic

_logger = logging.getLogger(__name__)

SPONTANEOUS_INTERVAL_SEC_ENV: Final[str] = "TOYBOX_SPONTANEOUS_INTERVAL_SEC"
DEFAULT_SPONTANEOUS_INTERVAL_SEC: Final[float] = 180.0

# Default intent used when mode 5 receives a transcript with no
# trigger match (the dispatcher still escalates) and when the
# spontaneous timer fires with no recent context.
SPONTANEOUS_INTENT: Final[str] = "boredom"

# Bytes of the malformed Claude response we surface in the system
# warning envelope. Big enough to be diagnostic, small enough to
# never accidentally leak a long PII string.
INVALID_PREVIEW_LIMIT: Final[int] = 200

# Type alias for the capability check the dispatcher consumes.
# Matches :func:`toybox.ai.capability.is_capable` after binding the
# breaker — production passes a partial, tests pass a small async
# stub returning a fixed tuple.
CapabilityCheck = Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]

# Type alias for the offline generator entry point. Constructed so
# tests can substitute a deterministic stub that records calls
# without touching the real templates dir.
OfflineGenerator = Callable[..., Activity]

# Type alias for the labeled_events recorder injected into the
# dispatcher. Production wires
# :func:`toybox.ai.labeled_events.record_generation` partial'd with a
# DB connection; tests pass a recording stub. The recorder is
# best-effort — failures inside it MUST NOT propagate (the dispatcher
# wraps invocations in try/except). It returns the new labeled_events
# row id (or 0 on failure) so the dispatcher can pass it to the judge
# sampler.
LabeledEventRecorder = Callable[[Activity, GeneratorContext, str], int]

# Type alias for the judge-call factory injected into the dispatcher.
# Production wires :func:`toybox.ai.judge.judge_and_persist` partial'd
# with an :class:`AIClient` + ``db_path_resolver``. The dispatcher
# passes this through to
# :func:`toybox.ai.labeled_events.schedule_judge_sample` after each
# successful ``record`` call. ``None`` disables judge sampling
# entirely (tests + smoke).
JudgeCallFactory = Callable[..., Awaitable[Any]]

# Type alias for the SQLite connection factory injected into the
# dispatcher so it can resolve real catalog content (toys, rooms,
# child profiles) per-event. Production wires a partial that opens a
# fresh connection at the resolved DB path; tests pass a small lambda
# returning an in-memory test DB. The dispatcher takes ownership of
# the connection it receives — it closes it after each resolve. When
# ``None`` (existing tests + the smoke harness pre-step-19), the
# dispatcher skips resolution and falls back to the Phase A
# placeholders, which is the documented degraded mode.
ConnectionFactory = Callable[[], sqlite3.Connection]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a float; using %s", name, raw, default)
        return default


def spontaneous_interval_from_env() -> float:
    """Resolve the mode-4 spontaneous interval from the environment.

    Falls back to :data:`DEFAULT_SPONTANEOUS_INTERVAL_SEC`. Negative or
    zero values are clamped to the default since a non-positive
    spontaneous cadence would fire continuously.
    """
    value = _env_float(SPONTANEOUS_INTERVAL_SEC_ENV, DEFAULT_SPONTANEOUS_INTERVAL_SEC)
    if value <= 0.0:
        _logger.warning(
            "%s=%s must be > 0; falling back to %s",
            SPONTANEOUS_INTERVAL_SEC_ENV,
            value,
            DEFAULT_SPONTANEOUS_INTERVAL_SEC,
        )
        return DEFAULT_SPONTANEOUS_INTERVAL_SEC
    return value


def _is_rate_limit_error(exc: BaseException) -> tuple[bool, float | None]:
    """Return ``(is_429, retry_after_seconds)`` for a Claude call exception.

    The Anthropic SDK isn't imported here — we duck-type on
    ``status_code`` and the class name so a stub exception can stand in
    for the SDK's :class:`anthropic.RateLimitError` /
    :class:`anthropic.APIStatusError`. Either of:

    * ``exc.status_code == 429`` (most SDK errors expose this), OR
    * class name in ``{"RateLimitError", "APIStatusError"}`` AND
      ``status_code == 429`` if present.

    counts as a 429. ``Retry-After`` is sourced from
    ``exc.response.headers["retry-after"]`` (case-insensitive) when
    available; non-numeric values yield ``None``.
    """
    status = getattr(exc, "status_code", None)
    if status != 429:
        # Some exception classes only encode 429 in the class name (the
        # SDK's RateLimitError pre-dates a stable status_code on every
        # instance). APIStatusError covers cases where the SDK wraps a
        # 429 in a more general status-bearing exception -- but only
        # accept it when status_code is missing or also 429, otherwise a
        # 5xx APIStatusError would be misclassified as a rate limit.
        cls_name = type(exc).__name__
        if cls_name not in {"RateLimitError", "APIStatusError"}:
            return False, None
        if status is not None:
            return False, None

    # Try to extract Retry-After from a typical SDK shape:
    # exc.response.headers (a dict-ish), or exc.headers.
    headers: Any = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)

    retry_after: float | None = None
    if headers is not None:
        raw: Any = None
        # dict-style headers (StubError tests pass plain dicts)
        if hasattr(headers, "get"):
            raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is not None:
            try:
                retry_after = float(raw)
            except (TypeError, ValueError):
                retry_after = None
    return True, retry_after


def _claude_user_prompt(
    *,
    intent: str,
    slot: str | None,
    transcript_text: str | None,
) -> str:
    """Build the user message for the structured-Activity request.

    Intentionally terse — the system prompt carries the schema +
    response-format contract, so the user message just provides the
    minimum context (intent, slot, optional transcript). Phase C step
    18 will widen this with toy + room + child context; for now the
    offline-fallback path means a thin prompt is fine.
    """
    parts = [
        f"Intent: {intent}",
        f"Slot: {slot if slot else '(none)'}",
    ]
    if transcript_text:
        parts.append(f"Transcript: {transcript_text!r}")
    return "\n".join(parts)


class ResolvedContent:
    """Per-dispatch catalog snapshot.

    Holds the toys, rooms, and aggregated child constraints resolved
    for a SINGLE escalation dispatch. Both the offline path and the
    Claude directive path read from the same instance so a parent edit
    that lands mid-dispatch can't surface a different banned-themes
    set to the offline fallback vs. the Claude prompt.

    The empty instance (zero toys, rooms, default ResolvedChildren) is
    the documented degraded mode when the connection_factory is None
    or the resolver raised — both call sites tolerate empty inputs and
    surface Phase A placeholders.
    """

    __slots__ = ("toys", "rooms", "children")

    def __init__(
        self,
        *,
        toys: tuple[ResolvedToy, ...] = (),
        rooms: tuple[ResolvedRoom, ...] = (),
        children: ResolvedChildren | None = None,
    ) -> None:
        self.toys = toys
        self.rooms = rooms
        self.children = children if children is not None else ResolvedChildren()


def _claude_system_prompt(directive: str | None = None) -> str:
    """The system prompt asking Claude to emit a strict :class:`Activity` JSON.

    Args:
        directive: Optional banned-themes + reading-level guidance from
            :func:`toybox.activities.content_resolver.build_claude_directive`.
            Appended verbatim to the bottom of the system prompt when
            non-empty. The empty string and ``None`` are both treated
            as "no extra guidance" so the call site can build the
            directive unconditionally.
    """
    base = (
        "You generate short play activities for a child's interactive toy. "
        "Reply with EXACTLY one JSON object matching this schema and nothing else "
        "(no prose, no code fences):\n"
        "{\n"
        '  "id": "<uuid string>",\n'
        '  "template_id": "claude_dynamic",\n'
        '  "persona_id": null,\n'
        '  "title": "<short title>",\n'
        '  "steps": [ five {"step_index": 0..4, "text": "<one line>", '
        '"sfx": null, "expected_action": null, "action_slot": "<slot>"} '
        "entries in order ],\n"
        '  "version": 1,\n'
        '  "metadata": {}\n'
        "}\n"
        "Steps MUST be exactly five, indexed 0 through 4 in order. "
        "Each `text` is a single sentence the toy speaks aloud. "
        'Each `action_slot` is one of "idle", "pointing", "looking", '
        '"jumping", "cheering", "thinking", "waving", "running", '
        '"sleeping", "confused" — pick the slot that best matches the '
        'verb in the step text. If no clear match, pick "idle". '
        "Do not include trailing commentary."
    )
    if directive:
        return base + "\n" + directive
    return base


class EscalationDispatcher:
    """Mode-aware Claude escalation entry point.

    The dispatcher is constructible from any composition root — Phase
    B Step 14b wires it into the running daemon, but for tests + the
    ``--check`` CLI it can be built standalone. It does NOT own a task
    loop; callers invoke :meth:`on_transcript` / :meth:`maybe_fire_spontaneous`
    directly.

    Args:
        ai_client: Anything implementing :class:`AIClient` (production
            passes :class:`AnthropicClient`, tests pass
            :class:`StubClient` or a custom fake).
        breaker: The shared in-process :class:`CircuitBreaker`.
        throttle: A :class:`MinIntervalThrottle` configured with
            ``TOYBOX_CLAUDE_MIN_INTERVAL_SEC``. The throttle is global
            to Claude (one instance per dispatcher).
        capability_check: Async callable returning the
            ``(is_capable, reason)`` tuple. Production binds
            :func:`toybox.ai.capability.is_capable` to the breaker;
            tests pass a stub.
        offline_generator: Callable matching :func:`generate` —
            ``generate(intent, slot, context, hour, seed, *, persona_id=None)``.
            Defaults to the real generator. Tests pass a recording stub
            so per-mode call-counts can be asserted against both Claude
            and the offline path.
        publisher: Optional ws publisher. Used to emit
            ``Topic.system`` warnings for malformed Claude output.
        spontaneous_interval_sec: Override the
            ``TOYBOX_SPONTANEOUS_INTERVAL_SEC`` env default. Currently
            unused inside the dispatcher (the spontaneous timer is
            owned by Step 4's listening-mode state machine), but
            stored so a future scheduler can read it back without
            re-resolving the env.
        clock: Time source for the spontaneous-interval bookkeeping
            and the ``hour`` argument fed to the offline generator.
            Defaults to :func:`time.time`.
    """

    def __init__(
        self,
        *,
        ai_client: AIClient,
        breaker: CircuitBreaker,
        throttle: MinIntervalThrottle,
        capability_check: CapabilityCheck,
        offline_generator: OfflineGenerator | None = None,
        publisher: Publisher | None = None,
        spontaneous_interval_sec: float | None = None,
        clock: Callable[[], float] | None = None,
        labeled_event_recorder: LabeledEventRecorder | None = None,
        judge_call_factory: JudgeCallFactory | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._ai_client = ai_client
        self._breaker = breaker
        self._throttle = throttle
        self._capability_check = capability_check
        self._offline_generator: OfflineGenerator = (
            offline_generator if offline_generator is not None else generate
        )
        self._publisher = publisher
        self._spontaneous_interval_sec = (
            spontaneous_interval_sec
            if spontaneous_interval_sec is not None
            else spontaneous_interval_from_env()
        )
        self._clock = clock if clock is not None else time.time
        # ``labeled_event_recorder`` is the Phase C step 15 hook that
        # writes the labeled_events row. Optional so existing tests + the
        # smoke harness can construct the dispatcher without a DB;
        # production wires it in the daemon startup.
        self._labeled_event_recorder = labeled_event_recorder
        # ``judge_call_factory`` is the awaitable factory the judge
        # sampler invokes when a row is in-sample. Production wires
        # :func:`toybox.ai.judge.judge_and_persist` partial'd with an
        # :class:`AIClient` + ``db_path_resolver``. ``None`` disables
        # judge sampling — the row still records, just without scores.
        self._judge_call_factory = judge_call_factory
        # ``connection_factory`` is the Step 19 hook that lets the
        # dispatcher resolve real catalog content (toys, rooms, child
        # profiles) for both the offline AND Claude paths. ``None``
        # disables resolution — the dispatcher falls back to the Phase A
        # placeholders + an empty Claude directive, which keeps existing
        # tests working without a DB and matches the smoke-harness
        # behaviour (smoke is offline-only and resolves in its own
        # persist hook).
        self._connection_factory = connection_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def spontaneous_interval_sec(self) -> float:
        return self._spontaneous_interval_sec

    async def on_transcript(
        self,
        transcript: Transcript,
        mode: ListeningMode,
        intents: list[Intent],
    ) -> Activity | None:
        """Dispatch an above-floor transcript per the active listening mode.

        Returns ``None`` when the mode + state combination produces no
        activity (e.g. mode 1 with no intent matches). Otherwise returns
        the chosen :class:`Activity` — whether from Claude or the
        offline generator.
        """
        if mode is ListeningMode.OFFLINE or mode is ListeningMode.LOW:
            # Modes 1-2: trigger match → offline only. NEVER call Claude.
            if not intents:
                return None
            primary = intents[0]
            content = self._resolve_content()
            activity = self._offline_activity(primary.name, primary.slot, content)
            self._record(
                activity=activity,
                intent=primary.name,
                slot=primary.slot,
                transcript_text=transcript.text,
                generator_path=GENERATOR_PATH_OFFLINE,
                content=content,
            )
            return activity

        if mode is ListeningMode.DEFAULT or mode is ListeningMode.HIGH:
            # Mode 3 + mode 4 (transcript-driven path): curated trigger
            # + capability + breaker + throttle gate Claude; else offline.
            if not intents:
                return None
            primary = intents[0]
            return await self._maybe_claude_with_offline_fallback(
                intent=primary.name,
                slot=primary.slot,
                transcript_text=transcript.text,
            )

        # Mode 5: every above-floor transcript escalates. If no trigger
        # matched, synthesize a default intent so the offline fallback
        # has somewhere to land.
        if mode is ListeningMode.INTENSE:
            if intents:
                intent_name: str = intents[0].name
                slot: str | None = intents[0].slot
            else:
                intent_name = SPONTANEOUS_INTENT
                slot = None
            return await self._maybe_claude_with_offline_fallback(
                intent=intent_name,
                slot=slot,
                transcript_text=transcript.text,
            )

        # Defensive: an unrecognized mode means a contract bug
        # somewhere upstream. Log + no-op rather than raise — a single
        # bad mode value should not bring the daemon down.
        _logger.warning("on_transcript: unknown mode %r; no-op", mode)  # pragma: no cover
        return None  # pragma: no cover

    async def maybe_fire_spontaneous(self, mode: ListeningMode) -> Activity | None:
        """Spontaneous-timer hook for mode 4.

        Called by Step 4's listening-mode scheduler at the
        ``TOYBOX_SPONTANEOUS_INTERVAL_SEC`` cadence. Returns ``None``
        for any non-HIGH mode (the scheduler should already gate on
        mode, but defensive double-check keeps the contract crisp).

        For mode 4, the dispatch is identical to a trigger-driven mode
        3/4 path with a synthesized "boredom" intent: capability +
        breaker + throttle gate Claude; closed gates fall through to
        the offline generator.
        """
        if mode is not ListeningMode.HIGH:
            return None
        return await self._maybe_claude_with_offline_fallback(
            intent=SPONTANEOUS_INTENT,
            slot=None,
            transcript_text=None,
        )

    # ------------------------------------------------------------------
    # Claude path
    # ------------------------------------------------------------------

    async def _maybe_claude_with_offline_fallback(
        self,
        *,
        intent: str,
        slot: str | None,
        transcript_text: str | None,
    ) -> Activity:
        """Run the gated Claude path, falling back to offline on any miss.

        Gates evaluated in order:

        1. Capability check (mode + token + breaker + network). False
           reason short-circuits to offline.
        2. Breaker open. Short-circuits to offline.
        3. Throttle. Failed acquire short-circuits to offline.

        If all gates pass we call Claude; on success we return its
        Activity, on failure (transport, malformed, 429) we still
        return an offline Activity so the caller always gets something
        playable.
        """
        # Resolve real catalog content ONCE per dispatch. Both the
        # offline path and the Claude directive share the same
        # ``ResolvedContent`` so a single dispatch can't surface
        # different toy/room/child constraints to the offline fallback
        # vs. Claude (race-free w.r.t. parent edits during a single
        # dispatch).
        content = self._resolve_content()

        def _offline_with_record() -> Activity:
            activity = self._offline_activity(intent, slot, content)
            self._record(
                activity=activity,
                intent=intent,
                slot=slot,
                transcript_text=transcript_text,
                generator_path=GENERATOR_PATH_OFFLINE,
                content=content,
            )
            return activity

        try:
            capable, reason = await self._capability_check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- defensive; treat any failure as not capable
            _logger.warning(
                "capability_check raised (%s: %s); treating as offline",
                type(exc).__name__,
                exc,
            )
            capable, reason = False, None
        if not capable:
            _logger.debug(
                "claude gate closed (capability=False reason=%s); offline path",
                reason.value if reason is not None else "<none>",
            )
            return _offline_with_record()

        # Reading state advances open→half_open if the cooldown elapsed.
        # The breaker's documented single-flight contract is via
        # try_half_open(): only the first concurrent caller claims the
        # probe slot; subsequent callers see the breaker re-opened and
        # fall back to offline. Bypassing try_half_open() under
        # concurrency would let multiple "probes" fire after cooldown.
        breaker_state = self._breaker.state
        if breaker_state is BreakerState.open:
            _logger.info("claude gate closed (breaker open); offline path")
            return _offline_with_record()
        if breaker_state is BreakerState.half_open and not self._breaker.try_half_open():
            _logger.info("claude gate closed (half_open probe slot already taken); offline path")
            return _offline_with_record()

        if not self._throttle.try_acquire():
            _logger.info(
                "claude gate closed (throttled, %.2fs remaining); offline path",
                self._throttle.time_until_next(),
            )
            return _offline_with_record()

        # All gates passed → call Claude. Any failure routes to offline.
        claude_activity = await self._try_claude(
            intent=intent,
            slot=slot,
            transcript_text=transcript_text,
            content=content,
        )
        if claude_activity is not None:
            self._record(
                activity=claude_activity,
                intent=intent,
                slot=slot,
                transcript_text=transcript_text,
                generator_path=GENERATOR_PATH_CLAUDE,
                content=content,
            )
            return claude_activity
        return _offline_with_record()

    async def _try_claude(
        self,
        *,
        intent: str,
        slot: str | None,
        transcript_text: str | None,
        content: ResolvedContent,
    ) -> Activity | None:
        """Single Claude call; returns the parsed Activity or None on failure.

        On non-429 transport failure: ``breaker.record_failure()``,
        return None (caller falls back to offline).
        On 429: ``breaker.record_429(retry_after)``, return None.
        On malformed output: emit ``Topic.system`` warning, return None.
        On success: ``breaker.record_success()``, return the Activity.

        Step 19 wires the banned-themes + reading-level directive into
        the system prompt: when ``content.children`` carries any
        constraints, :func:`build_claude_directive` produces the
        ``"Do NOT include any of: ..."`` plus reading-level guidance
        and we append it to the schema-shape system prompt. Empty
        constraints produce an empty directive (no prompt change).
        """
        directive = build_claude_directive(
            content.children.banned_themes,
            content.children.reading_level,
        )
        system = _claude_system_prompt(directive=directive)
        user = _claude_user_prompt(intent=intent, slot=slot, transcript_text=transcript_text)
        try:
            response = await self._ai_client.complete_text(
                [AIMessage(role="user", content=user)],
                system=system,
            )
        except asyncio.CancelledError:
            # Cancellation must propagate so daemon shutdown / task
            # cancellation isn't silently converted into a transport
            # failure that debits the breaker.
            raise
        except Exception as exc:  # noqa: BLE001 -- 429 detection by duck typing
            is_429, retry_after = _is_rate_limit_error(exc)
            if is_429:
                _logger.warning(
                    "claude returned 429; opening breaker (retry_after=%s)",
                    retry_after,
                )
                self._breaker.record_429(retry_after=retry_after)
                return None
            _logger.warning(
                "claude call failed (%s: %s); recording breaker failure",
                type(exc).__name__,
                exc,
            )
            self._breaker.record_failure()
            return None

        # Try to parse the response as an Activity. On any parse error
        # (non-JSON, schema violation, missing fields) emit a system
        # warning and fall through to offline. We do NOT trip the
        # breaker on malformed output — a single bad sample is not a
        # transport failure.
        try:
            activity = Activity.model_validate_json(response.text)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _logger.warning(
                "claude output failed validation (%s); offline fallback; preview=%r",
                type(exc).__name__,
                response.text[:INVALID_PREVIEW_LIMIT],
            )
            self._emit_invalid_warning(response.text)
            # Recording a success here is correct: the transport
            # worked, we got bytes back. The breaker is for
            # transport-layer health; content quality is a separate
            # signal surfaced via the system topic.
            self._breaker.record_success()
            return None

        self._breaker.record_success()
        return activity

    def _emit_invalid_warning(self, raw_text: str) -> None:
        """Publish a ``Topic.system`` envelope describing the bad payload."""
        if self._publisher is None:
            return
        preview = raw_text[:INVALID_PREVIEW_LIMIT]
        envelope = build_envelope(
            topic=Topic.system,
            payload={
                "code": "claude_output_invalid",
                "model": text_model(),
                "preview": preview,
            },
        )
        try:
            self._publisher(envelope)
        except Exception as exc:  # noqa: BLE001 -- publisher is foreign code
            _logger.warning(
                "system warning publisher raised; continuing (exc=%s: %s)",
                type(exc).__name__,
                exc,
            )

    # ------------------------------------------------------------------
    # Offline path
    # ------------------------------------------------------------------

    def _record(
        self,
        *,
        activity: Activity,
        intent: str,
        slot: str | None,
        transcript_text: str | None,
        generator_path: str,
        content: ResolvedContent | None = None,
    ) -> None:
        """Best-effort labeled_events write + judge schedule.

        The recorder is optional (None in tests + the smoke harness),
        so this is a no-op when not wired. Production failures
        (sqlite IO, judge scheduling) MUST NOT propagate to the
        caller — the kid-facing path is unchanged whether the recorder
        succeeds or fails. After the row lands we hand its ``row_id``
        to :func:`toybox.ai.labeled_events.schedule_judge_sample`; the
        judge runs detached on the event loop and never blocks dispatch.

        ``content`` carries the resolved toys/rooms/child constraints
        for this dispatch (Step 19) so they show up in the labeled_events
        ``inputs_chatml_json`` payload and Phase E SFT exports see real
        catalog data, not the empty placeholders pre-step-19 rows
        carried.
        """
        if self._labeled_event_recorder is None:
            return
        ctx: GeneratorContext | None = None
        row_id: int | None = None
        try:
            toy_names: tuple[str, ...] = ()
            room_names: tuple[str, ...] = ()
            child_profile_payload: dict[str, Any] | None = None
            if content is not None:
                toy_names = tuple(t.display_name for t in content.toys)
                room_names = tuple(r.display_name for r in content.rooms)
                if content.children.banned_themes or content.children.reading_level:
                    child_profile_payload = {
                        "banned_themes": list(content.children.banned_themes),
                        "reading_level": content.children.reading_level,
                    }
            ctx = build_generator_context(
                intent=intent,
                slot=slot,
                transcript_window=transcript_text,
                persona_id=activity.persona_id,
                available_toys=toy_names,
                available_rooms=room_names,
                child_profile=child_profile_payload,
            )
            row_id = self._labeled_event_recorder(activity, ctx, generator_path)
        except Exception as exc:  # noqa: BLE001 -- eval scaffold must never break dispatch
            _logger.warning(
                "labeled_events recorder failed for activity %s (%s: %s); skipping",
                activity.id,
                type(exc).__name__,
                exc,
            )
            return

        if row_id is None or row_id <= 0 or ctx is None:
            return
        try:
            schedule_judge_sample(
                row_id=row_id,
                activity=activity,
                ctx=ctx,
                judge_call=self._judge_call_factory,
            )
        except Exception as exc:  # noqa: BLE001 -- judge scheduling must never break dispatch
            _logger.warning(
                "judge sample scheduling failed for activity %s (%s: %s); continuing",
                activity.id,
                type(exc).__name__,
                exc,
            )

    def _offline_activity(
        self,
        intent: str,
        slot: str | None,
        content: ResolvedContent,
    ) -> Activity:
        """Generate a deterministic offline :class:`Activity`.

        Maps unknown intent names (e.g. ``mention_toy``) to the
        :data:`SPONTANEOUS_INTENT` ("boredom") fallback so the offline
        generator's template loader always finds a pool. The generator
        itself also tolerates unknown intents, but normalising here
        keeps the deterministic seed input clean.

        ``hour`` is read from the configured clock so the generator's
        time-of-day bucket logic is real-clock-aware. ``seed`` is
        derived from ``(intent, slot, hour)`` via :func:`zlib.crc32` so
        repeated dispatches in the same hour for the same intent stay
        deterministic across process restarts (Python's built-in
        ``hash()`` is randomized per-process via PYTHONHASHSEED).

        ``content`` carries Step 19 resolved catalog data — passing it
        through the generator means trigger-driven offline activities
        substitute real toy names (instead of the legacy
        ``Mr. Unicorn`` placeholder) AND apply the banned-themes
        filter to template selection.
        """
        hour = datetime.fromtimestamp(self._clock()).hour
        seed = zlib.crc32(repr((intent, slot, hour)).encode("utf-8"))
        try:
            return self._offline_generator(
                intent,
                slot,
                None,  # context — generator's UUID input; not the resolver
                hour,
                seed,
                available_toys=content.toys,
                available_rooms=content.rooms,
                resolved_children=content.children,
            )
        except Exception as exc:  # noqa: BLE001 -- last-ditch fallback
            # The offline generator should never fail for a shipped
            # intent, but if it does (e.g. a custom intent with no
            # template) try the documented fallback intent before
            # raising so we never deadlock the dispatcher.
            _logger.warning(
                "offline generator raised for intent=%r slot=%r (%s: %s); retrying with %r",
                intent,
                slot,
                type(exc).__name__,
                exc,
                FALLBACK_INTENT,
            )
            return self._offline_generator(
                FALLBACK_INTENT,
                None,
                None,
                hour,
                seed,
                available_toys=content.toys,
                available_rooms=content.rooms,
                resolved_children=content.children,
            )

    def _resolve_content(self) -> ResolvedContent:
        """Resolve real catalog content for the next dispatch.

        Returns an empty :class:`ResolvedContent` when no
        ``connection_factory`` is wired (existing tests + smoke
        harness path) — the generator + Claude directive both tolerate
        empty inputs and degrade to Phase A placeholders.

        Resolver failures (sqlite errors, missing tables) log WARNING
        and degrade to the same empty content rather than 500'ing a
        kid-facing dispatch.

        Multi-child policy: trigger-driven activities have no explicit
        child_ids (no API caller specified them), so we resolve ALL
        children in the table. Phase H Step H4 (migration 0009)
        promoted ``banned_themes`` from a per-child column to a single
        household-global setting (``settings.banned_themes_global``);
        :func:`toybox.activities.content_resolver.resolve_child_profiles`
        is the single seam that splices that value onto the per-child
        ``reading_level`` aggregate. ``aggregate_child_constraints``
        therefore only computes ``reading_level`` (MIN — most
        restrictive wins) now. The ``children`` table doesn't have an
        ``archived`` column today, so "all children" is literal — a
        future migration that adds archive semantics will need to
        filter here too.
        """
        if self._connection_factory is None:
            return ResolvedContent()
        try:
            conn = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 -- defensive
            _logger.warning(
                "connection_factory raised (%s: %s); resolver disabled for this dispatch",
                type(exc).__name__,
                exc,
            )
            return ResolvedContent()
        try:
            try:
                toys = tuple(resolve_toys(conn))
                rooms = tuple(resolve_rooms(conn))
                child_id_rows = conn.execute("SELECT id FROM children").fetchall()
                child_ids = [str(row["id"]) for row in child_id_rows]
                children = resolve_child_profiles(conn, child_ids)
            except sqlite3.Error as exc:
                _logger.warning(
                    "content_resolver query failed in dispatcher (%s); "
                    "falling back to placeholders",
                    exc,
                )
                return ResolvedContent()
            return ResolvedContent(toys=toys, rooms=rooms, children=children)
        finally:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 -- never leak a close error
                _logger.warning(
                    "resolver connection close failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )


__all__ = [
    "DEFAULT_SPONTANEOUS_INTERVAL_SEC",
    "ConnectionFactory",
    "EscalationDispatcher",
    "INVALID_PREVIEW_LIMIT",
    "JudgeCallFactory",
    "LabeledEventRecorder",
    "ResolvedContent",
    "SPONTANEOUS_INTENT",
    "SPONTANEOUS_INTERVAL_SEC_ENV",
    "spontaneous_interval_from_env",
]
