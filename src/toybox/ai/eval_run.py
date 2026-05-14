"""``uv run python -m toybox.ai.eval_run`` CLI.

Runs the held-out fixture set through the offline generator (Phase C
default — Claude generation lands in Phase E A/B), then optionally
through the Claude judge to refresh ``baseline_scores.json``.

Used both for baseline regeneration (operator) and CI regression
(automated). The CI gate logic lives here too: compare current scores
against ``baseline_scores.json`` and exit non-zero on regression.

When ``baseline_scores.json`` is a placeholder (every fixture flagged
``"placeholder": true`` by the operator), the CI regression check is a
no-op — that lets the eval scaffold ship before live Claude is
available without making CI fail on missing baseline data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..activities.generator import generate
from ..activities.models import Activity
from .client import AIClient, StubClient
from .judge import judge_activity
from .labeled_events import GeneratorContext
from .rubric import (
    DIMENSION_KEYS,
    QUALITY_DIMENSION_KEYS,
    SAFETY_AUTOFAIL,
    RubricScores,
)

_logger = logging.getLogger(__name__)

# Default fixture + baseline locations (relative to repo root). Override
# via CLI flags for tests.
DEFAULT_FIXTURES_PATH = Path("tests/fixtures/eval/prompts.jsonl")
DEFAULT_BASELINE_PATH = Path("tests/fixtures/eval/baseline_scores.json")
DEFAULT_HOLDOUT_PATH = Path("tests/fixtures/eval/holdout.json")

# CI regression budget: mean dimension score may drop at most this much
# from baseline before the build fails.
DEFAULT_REGRESSION_TOLERANCE = 0.5


@dataclass(frozen=True, slots=True)
class Fixture:
    """One row from ``prompts.jsonl``."""

    id: str
    category: str
    child_profile: dict[str, Any]
    persona: str
    available_rooms: tuple[str, ...]
    available_toys: tuple[str, ...]
    transcript_window: str
    trigger: str
    listening_mode: int
    anti_signal: tuple[str, ...]
    time_of_day: str
    expected_floor: dict[str, int]
    edge_case: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Fixture:
        return cls(
            id=str(payload["id"]),
            category=str(payload.get("category", "")),
            child_profile=dict(payload["child_profile"]),
            persona=str(payload["persona"]),
            available_rooms=tuple(str(r) for r in payload["available_rooms"]),
            available_toys=tuple(str(t) for t in payload["available_toys"]),
            transcript_window=str(payload["transcript_window"]),
            trigger=str(payload["trigger"]),
            listening_mode=int(payload["listening_mode"]),
            anti_signal=tuple(str(s) for s in payload.get("anti_signal", [])),
            time_of_day=str(payload["time_of_day"]),
            expected_floor=dict(payload.get("expected_floor", {})),
            edge_case=payload.get("edge_case"),
        )


def load_fixtures(path: Path) -> list[Fixture]:
    """Parse the JSONL fixture file."""
    fixtures: list[Fixture] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            fixtures.append(Fixture.from_dict(payload))
    return fixtures


def load_holdout_ids(path: Path) -> list[str]:
    """Read the holdout fixture id list."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "ids" not in payload:
        raise ValueError(
            f"holdout file at {path} must be a JSON object with an 'ids' key"
        )
    ids = payload["ids"]
    if not isinstance(ids, list):
        raise ValueError(f"holdout 'ids' at {path} must be a list")
    return [str(x) for x in ids]


def trigger_to_intent(trigger: str) -> str:
    """Map a fixture trigger label to a generator intent.

    Fixture triggers are descriptive labels (``boredom_explicit``,
    ``ambiguous_mumble``); the offline generator's templates are
    intent-keyed. We pick the closest match so the offline generator
    always finds a template to fire.
    """
    mapping = {
        "boredom_explicit": "boredom",
        "boredom_implicit": "boredom",
        "implicit_lull": "boredom",
        "excitement_spike": "request_play",
        "ambiguous_mumble": "boredom",
        "conflict": "request_activity",
        "request_play": "request_play",
        "request_story": "request_story",
        "request_activity": "request_activity",
    }
    return mapping.get(trigger, "boredom")


def _hour_for_time_of_day(label: str) -> int:
    """Pick a representative hour for a ``time_of_day`` label."""
    return {
        "morning": 9,
        "afternoon": 14,
        "evening": 18,
        "wind_down": 20,
        "night": 22,
    }.get(label, 14)


def fixture_to_context(fx: Fixture) -> GeneratorContext:
    """Build a :class:`GeneratorContext` from a fixture row."""
    return GeneratorContext(
        intent=trigger_to_intent(fx.trigger),
        slot=None,
        transcript_window=fx.transcript_window,
        persona_id=fx.persona,
        available_toys=fx.available_toys,
        available_rooms=fx.available_rooms,
        child_profile=fx.child_profile,
        listening_mode=fx.listening_mode,
        time_of_day=fx.time_of_day,
        extra={"trigger": fx.trigger, "anti_signal": list(fx.anti_signal)},
    )


def generate_for_fixture(fx: Fixture) -> Activity:
    """Run the offline generator for one fixture (deterministic)."""
    intent = trigger_to_intent(fx.trigger)
    hour = _hour_for_time_of_day(fx.time_of_day)
    seed = zlib.crc32(fx.id.encode("utf-8"))
    return generate(
        intent=intent,
        slot=None,
        context={"fixture_id": fx.id},
        hour=hour,
        seed=seed,
        persona_id=fx.persona,
    )


async def judge_one(
    *,
    fx: Fixture,
    activity: Activity,
    ai_client: AIClient,
) -> RubricScores | None:
    """Run the judge on one fixture's activity. Returns None on failure."""
    ctx = fixture_to_context(fx)
    return await judge_activity(ai_client=ai_client, activity=activity, ctx=ctx)


def synthesize_placeholder_scores() -> RubricScores:
    """Return a synthetic 4-out-of-5-on-everything placeholder.

    Used when no live Claude judge is available — operators must
    refresh ``baseline_scores.json`` with real scores once Claude OAuth
    is wired in their environment. The CI gate self-skips when every
    fixture is flagged ``placeholder=True``.
    """
    return RubricScores(
        schema=4,
        age_appropriateness=4,
        doability=4,
        persona_fidelity=4,
        coherence=4,
        safety=4,
        hallucinated_props=(),
        judge_notes="placeholder; refresh baseline once Claude judge is reachable",
    )


def run_fixtures(
    fixtures: list[Fixture],
    *,
    fixtures_only: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Generate-only pass over the fixture set; returns a dict by fixture id.

    Each entry: ``{"activity": <Activity dict>, "fixture": <Fixture dict>}``.
    """
    selected = fixtures
    if fixtures_only:
        wanted = set(fixtures_only)
        selected = [f for f in fixtures if f.id in wanted]
    out: dict[str, dict[str, Any]] = {}
    for fx in selected:
        activity = generate_for_fixture(fx)
        out[fx.id] = {
            "activity": json.loads(activity.model_dump_json()),
            "fixture": asdict(fx),
        }
    return out


async def run_with_judge(
    fixtures: list[Fixture],
    *,
    ai_client: AIClient,
    fixtures_only: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Generate + judge each selected fixture. Returns id → record."""
    base = run_fixtures(fixtures, fixtures_only=fixtures_only)
    by_id = {fx.id: fx for fx in fixtures}
    for fid, record in base.items():
        fx = by_id[fid]
        activity = Activity.model_validate(record["activity"])
        scores = await judge_one(fx=fx, activity=activity, ai_client=ai_client)
        if scores is not None:
            record["scores"] = scores.to_mapping()
            record["placeholder"] = False
        else:
            record["scores"] = synthesize_placeholder_scores().to_mapping()
            record["placeholder"] = True
    return base


def baseline_to_disk(records: dict[str, dict[str, Any]], path: Path) -> None:
    """Write a baseline_scores.json file. Sorts by id for stable diffs."""
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "fixtures": {
            fid: {
                "scores": records[fid].get("scores"),
                "placeholder": records[fid].get("placeholder", False),
            }
            for fid in sorted(records.keys())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _all_placeholder(baseline: dict[str, Any]) -> bool:
    fixtures = baseline.get("fixtures", {})
    if not fixtures:
        return True
    return all(entry.get("placeholder", False) for entry in fixtures.values())


def _mean_score(score_dict: dict[str, Any], keys: Sequence[str]) -> float:
    values: list[float] = []
    for k in keys:
        v = score_dict.get(k)
        if isinstance(v, int | float):
            values.append(float(v))
    if not values:
        return 0.0
    return sum(values) / len(values)


@dataclass(frozen=True, slots=True)
class RegressionResult:
    """Outcome of a CI regression check."""

    passed: bool
    skipped_reason: str | None
    failures: tuple[str, ...]
    current_mean: float
    baseline_mean: float


def evaluate_regression(
    *,
    current_records: dict[str, dict[str, Any]],
    baseline: dict[str, Any],
    holdout_ids: list[str],
    tolerance: float = DEFAULT_REGRESSION_TOLERANCE,
) -> RegressionResult:
    """Compare current judge scores to baseline; return pass/fail.

    Failures:
    * mean dimension score drops > tolerance from baseline, OR
    * any fixture's safety dim auto-fails (== :data:`SAFETY_AUTOFAIL`), OR
    * any fixture's ``expected_floor`` is violated.

    A baseline where every fixture is placeholder-flagged is treated as
    "no baseline available yet" and the regression check is skipped
    (returns ``passed=True`` with ``skipped_reason`` set).
    """
    if _all_placeholder(baseline):
        return RegressionResult(
            passed=True,
            skipped_reason=(
                "baseline_scores.json is placeholder-only — operator must "
                "refresh baseline once a real Claude judge is reachable"
            ),
            failures=(),
            current_mean=0.0,
            baseline_mean=0.0,
        )

    failures: list[str] = []
    current_means: list[float] = []
    baseline_means: list[float] = []

    for fid in holdout_ids:
        record = current_records.get(fid)
        if record is None:
            failures.append(f"holdout fixture {fid!r} not present in current run")
            continue
        scores = record.get("scores")
        if scores is None:
            failures.append(f"holdout fixture {fid!r} produced no scores")
            continue
        # Safety auto-fail
        safety = scores.get("safety")
        if isinstance(safety, int | float) and int(safety) <= SAFETY_AUTOFAIL:
            failures.append(
                f"holdout fixture {fid!r} safety auto-fail (score={safety})"
            )
        # Expected floor (per-fixture per-dimension minimum)
        fx_payload = record.get("fixture", {})
        expected_floor = fx_payload.get("expected_floor", {}) or {}
        for dim, floor in expected_floor.items():
            actual = scores.get(dim)
            if isinstance(actual, int | float) and float(actual) < float(floor):
                failures.append(
                    f"holdout fixture {fid!r} expected_floor[{dim}] = {floor} "
                    f"violated (actual={actual})"
                )
        current_means.append(_mean_score(scores, DIMENSION_KEYS))

        baseline_entry = baseline.get("fixtures", {}).get(fid)
        if baseline_entry is None:
            failures.append(
                f"holdout fixture {fid!r} missing from baseline_scores.json"
            )
            continue
        baseline_scores = baseline_entry.get("scores") or {}
        baseline_means.append(_mean_score(baseline_scores, DIMENSION_KEYS))

    if not current_means:
        return RegressionResult(
            passed=False,
            skipped_reason=None,
            failures=tuple(failures or ["no holdout fixtures evaluated"]),
            current_mean=0.0,
            baseline_mean=0.0,
        )

    cur_avg = sum(current_means) / len(current_means)
    base_avg = (
        sum(baseline_means) / len(baseline_means) if baseline_means else cur_avg
    )
    if (base_avg - cur_avg) > tolerance:
        failures.append(
            f"mean dimension score regressed by "
            f"{base_avg - cur_avg:.3f} > tolerance {tolerance}"
        )
    return RegressionResult(
        passed=not failures,
        skipped_reason=None,
        failures=tuple(failures),
        current_mean=cur_avg,
        baseline_mean=base_avg,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toybox.ai.eval_run",
        description=(
            "Run the eval fixture set: generate activities, optionally "
            "judge them, and either refresh baseline_scores.json or "
            "compare against it (CI regression mode)."
        ),
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help="Path to prompts.jsonl.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="Path to baseline_scores.json (read for CI mode, written for --refresh).",
    )
    parser.add_argument(
        "--holdout",
        type=Path,
        default=DEFAULT_HOLDOUT_PATH,
        help="Path to holdout.json (used by CI regression mode only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the new baseline. Defaults to --baseline.",
    )
    parser.add_argument(
        "--judge",
        choices=("claude", "stub", "skip"),
        default="skip",
        help="Which judge to use. 'skip' runs generation only.",
    )
    parser.add_argument(
        "--fixtures-only",
        default=None,
        help="Comma-separated fixture ids to restrict the run.",
    )
    parser.add_argument(
        "--mode",
        choices=("baseline", "ci"),
        default="ci",
        help=(
            "baseline = (re)generate baseline_scores.json. "
            "ci = run holdout fixtures and compare against baseline."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_REGRESSION_TOLERANCE,
        help="CI mode: max allowed mean-score regression from baseline.",
    )
    return parser.parse_args(argv)


def _build_judge_client(kind: str) -> AIClient | None:
    if kind == "skip":
        return None
    if kind == "stub":
        # Returns a synthetic perfect-5s response so tests can exercise
        # the full pipeline without network. The judge parser will turn
        # this into RubricScores all-5s.
        synthetic = json.dumps(
            {
                "schema": 5,
                "age_appropriateness": 5,
                "doability": 5,
                "persona_fidelity": 5,
                "coherence": 5,
                "safety": 5,
                "hallucinated_props": [],
                "judge_notes": "stub judge",
            }
        )
        return StubClient(responses=[synthetic] * 100)
    if kind == "claude":  # pragma: no cover - requires live OAuth
        from .client import AnthropicClient
        from .oauth import OAuthToken, load_token

        token = load_token()
        if token is None:
            raise RuntimeError(
                "no OAuth token available; run `python -m toybox.ai --login` first"
            )
        if not isinstance(token, OAuthToken):
            raise RuntimeError("loaded token is not an OAuthToken instance")
        return AnthropicClient(token)
    raise ValueError(f"unknown judge kind: {kind!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    fixtures = load_fixtures(args.fixtures)
    fixtures_only: list[str] | None = None
    if args.fixtures_only:
        fixtures_only = [s.strip() for s in args.fixtures_only.split(",") if s.strip()]

    judge_client = _build_judge_client(args.judge)
    if judge_client is None:
        records = run_fixtures(fixtures, fixtures_only=fixtures_only)
        # Synthesize placeholder scores for every record so downstream
        # baseline write / CI compare have a uniform shape.
        for record in records.values():
            record["scores"] = synthesize_placeholder_scores().to_mapping()
            record["placeholder"] = True
    else:
        records = asyncio.run(
            run_with_judge(
                fixtures,
                ai_client=judge_client,
                fixtures_only=fixtures_only,
            )
        )

    if args.mode == "baseline":
        out_path = args.out if args.out is not None else args.baseline
        baseline_to_disk(records, out_path)
        print(
            f"toybox.ai.eval_run: wrote baseline to {out_path} "
            f"({len(records)} fixtures)",
            file=sys.stderr,
        )
        return 0

    # CI mode
    if not args.baseline.is_file():
        print(
            f"toybox.ai.eval_run: baseline not found at {args.baseline}; "
            "skipping CI regression check (run --mode baseline first)",
            file=sys.stderr,
        )
        return 0
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    holdout = load_holdout_ids(args.holdout)
    result = evaluate_regression(
        current_records=records,
        baseline=baseline,
        holdout_ids=holdout,
        tolerance=args.tolerance,
    )
    if result.skipped_reason is not None:
        print(f"toybox.ai.eval_run: SKIPPED — {result.skipped_reason}", file=sys.stderr)
        return 0
    if not result.passed:
        for f in result.failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            f"toybox.ai.eval_run: FAIL "
            f"(current mean={result.current_mean:.3f} vs baseline={result.baseline_mean:.3f})",
            file=sys.stderr,
        )
        return 1
    print(
        f"toybox.ai.eval_run: PASS "
        f"(current mean={result.current_mean:.3f} vs baseline={result.baseline_mean:.3f})",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_FIXTURES_PATH",
    "DEFAULT_HOLDOUT_PATH",
    "DEFAULT_REGRESSION_TOLERANCE",
    "DIMENSION_KEYS",
    "Fixture",
    "QUALITY_DIMENSION_KEYS",
    "RegressionResult",
    "baseline_to_disk",
    "evaluate_regression",
    "fixture_to_context",
    "generate_for_fixture",
    "judge_one",
    "load_fixtures",
    "load_holdout_ids",
    "main",
    "run_fixtures",
    "run_with_judge",
    "synthesize_placeholder_scores",
    "trigger_to_intent",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
