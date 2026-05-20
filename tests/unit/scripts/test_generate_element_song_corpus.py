"""Phase Q Step Q3 — element-song corpus generator (unit tests).

Coverage for ``scripts/generate_element_song_corpus.py``. The generator
authors one manifest entry per element that is NOT already covered by
M7a's popular-element set (15 elements). Live LLM calls are deferred
to Q7 — this script's ``--dry-run`` path renders synthetic lyrics from
each element's ``story_seed_hooks`` so the structure can be exercised
end-to-end with no network.

Style mirrors :mod:`tests.unit.scripts.test_generate_element_microgames`:
``importlib.util.spec_from_file_location`` to load ``scripts/`` (which
is not a Python package); the ``generator_module`` fixture re-imports
per test for cache hygiene.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate_element_song_corpus.py"
_REAL_ELEMENTS_JSON = _REPO_ROOT / "data" / "elements" / "elements.json"


def _load_generator_module() -> types.ModuleType:
    """Load ``scripts/generate_element_song_corpus.py`` via importlib."""
    spec = importlib.util.spec_from_file_location(
        "_generate_element_song_corpus", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator_module() -> Iterator[types.ModuleType]:
    """Per-test fresh load of the generator script as a module."""
    module = _load_generator_module()
    yield module
    sys.modules.pop("_generate_element_song_corpus", None)


# ---------------------------------------------------------------------
# Fixtures: lightweight synthetic elements (no shipped corpus required)
# ---------------------------------------------------------------------


def _make_element(
    *,
    element_id: str,
    symbol: str,
    name: str,
    atomic_number: int,
    family: str,
    fun_fact: str = "",
    hooks: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal element dict matching elements.json schema."""
    return {
        "id": element_id,
        "symbol": symbol,
        "name": name,
        "atomic_number": atomic_number,
        "family": family,
        "fun_fact": fun_fact,
        "story_seed_hooks": hooks
        or [
            f"{name} is fun and bright",
            f"{name} shines just right",
            f"watch {name} sparkle in the sun",
            f"{name} brings joy to everyone",
        ],
    }


# ---------------------------------------------------------------------
# --help: zero-exit smoke test
# ---------------------------------------------------------------------


def test_help_returns_zero() -> None:
    """``--help`` exits 0 and surfaces the script's purpose."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"--help returncode={result.returncode}; stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "generate_element_song_corpus" in combined.lower() or "element-song" in combined.lower()
    assert "--dry-run" in combined
    assert "--validate" in combined


# ---------------------------------------------------------------------
# --dry-run: no AnthropicClient construction, no network
# ---------------------------------------------------------------------


def test_dry_run_succeeds_without_network(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` exits 0 and never constructs the real AnthropicClient."""

    def _blow_up(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "--dry-run must NOT construct AnthropicClient; "
            "live LLM calls are deferred to Q7"
        )

    monkeypatch.setattr(generator_module, "AnthropicClient", _blow_up)
    rc = generator_module.main(["--dry-run", "--limit", "1"])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"].startswith("element-song-")


def test_dry_run_skips_m7a_popular_elements(
    generator_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The 15 M7a popular element_ids must NOT appear in the dry-run output."""
    rc = generator_module.main(["--dry-run"])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    actual_element_ids = {e["element_id"] for e in payload}

    # No M7a popular element_id should land in the Q3 batch.
    overlap = actual_element_ids & generator_module.M7A_POPULAR_ELEMENT_IDS
    assert overlap == set(), (
        f"Q3 batch must NOT include M7a popular elements; overlap={sorted(overlap)!r}"
    )

    # And we should still cover the rest (118 - 15 = 103).
    assert len(payload) == 118 - len(generator_module.M7A_POPULAR_ELEMENT_IDS)


# ---------------------------------------------------------------------
# parse_llm_response: positive + negative cases
# ---------------------------------------------------------------------


def test_parse_llm_response_extracts_title_theme_lyrics(
    generator_module: types.ModuleType,
) -> None:
    """A well-formed LLM response yields the expected parsed dict."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    raw = (
        "title: Lithium Lullaby\n"
        "theme: music\n"
        "lyrics: Lithium light, lithium small,\n"
        "Hides inside the battery wall.\n"
        "Tiny power, soft and bright,\n"
        "Lithium hums all through the night!\n"
    )
    parsed = generator_module.parse_llm_response(raw, element)
    assert parsed["title"] == "Lithium Lullaby"
    assert parsed["theme"] == "music"
    assert parsed["lyrics"].count("\n") == 3  # 4 lines → 3 newlines
    assert parsed["lyric_line_count"] == 4


def test_parse_llm_response_rejects_invalid_theme(
    generator_module: types.ModuleType,
) -> None:
    """A theme outside {silly, music} is rejected with ValueError."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    raw = (
        "title: Lithium Adventure\n"
        "theme: adventure\n"
        "lyrics: Line one,\nLine two,\nLine three,\nLine four\n"
    )
    with pytest.raises(ValueError, match=r"theme"):
        generator_module.parse_llm_response(raw, element)


def test_parse_llm_response_rejects_too_long_lyrics(
    generator_module: types.ModuleType,
) -> None:
    """Lyrics whose total length exceeds 500 chars are rejected."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    long_line = "x" * 80  # 80 chars
    # 8 lines of 80 chars + 7 newlines = 647 chars → over the 500 cap.
    raw = (
        "title: Big Lithium\n"
        "theme: silly\n"
        "lyrics: " + long_line + "\n" + ("\n".join([long_line] * 7)) + "\n"
    )
    with pytest.raises(ValueError, match=r"lyrics length"):
        generator_module.parse_llm_response(raw, element)


def test_parse_llm_response_rejects_too_few_lyric_lines(
    generator_module: types.ModuleType,
) -> None:
    """Lyric blocks under 4 lines are rejected."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    raw = (
        "title: Short Song\n"
        "theme: silly\n"
        "lyrics: only line one\n"
        "only line two\n"
        "only line three\n"
    )
    with pytest.raises(ValueError, match=r"lyric line count"):
        generator_module.parse_llm_response(raw, element)


def test_parse_llm_response_rejects_missing_title(
    generator_module: types.ModuleType,
) -> None:
    """A response missing the ``title:`` label is rejected."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    raw = (
        "theme: silly\n"
        "lyrics: line one\nline two\nline three\nline four\n"
    )
    with pytest.raises(ValueError, match=r"title"):
        generator_module.parse_llm_response(raw, element)


# ---------------------------------------------------------------------
# build_entry: shape contract for the manifest insert
# ---------------------------------------------------------------------


def test_build_entry_sets_required_fields(
    generator_module: types.ModuleType,
) -> None:
    """A synthetic parsed response → manifest entry with all Q3 required fields."""
    element = _make_element(
        element_id="li-3", symbol="Li", name="Lithium", atomic_number=3, family="alkali_metal"
    )
    parsed = {
        "title": "Lithium Lullaby",
        "theme": "music",
        "lyrics": "L1\nL2\nL3\nL4",
        "lyric_line_count": 4,
    }
    entry = generator_module.build_entry(element, parsed)

    assert entry["id"] == "element-song-li-3"
    assert entry["element_id"] == "li-3"
    assert entry["family"] == "alkali_metal"
    assert entry["audio_path"] == "audio/element-song-li-3.mp3"
    assert entry["persona_compat"] == ["periodic_table", "all"]
    assert entry["age_band"] == "3-5"
    assert entry["theme"] == "music"
    assert entry["title"] == "Lithium Lullaby"
    assert entry["license"] == "CC-BY-4.0"
    assert "Coqui" in entry["credit"]
    assert entry["lyrics"] == "L1\nL2\nL3\nL4"
    # Duration estimated to land in the configured floor/ceiling range.
    assert 12 <= entry["duration_seconds"] <= 18


def test_build_entry_audio_path_format(
    generator_module: types.ModuleType,
) -> None:
    """audio_path follows ``audio/element-song-<sym>-<n>.mp3`` exactly.

    The Phase K K11 Coqui renderer lands MP3s at this path; a non-empty
    correctly-prefixed value is REQUIRED for Q7's downstream render step
    to find the file. Locking the format here prevents a silent rename.
    """
    element = _make_element(
        element_id="au-79", symbol="Au", name="Gold", atomic_number=79, family="transition_metal"
    )
    parsed = {
        "title": "Gold Glow",
        "theme": "music",
        "lyrics": "A\nB\nC\nD",
        "lyric_line_count": 4,
    }
    entry = generator_module.build_entry(element, parsed)
    assert entry["audio_path"]
    assert entry["audio_path"] == "audio/element-song-au-79.mp3"
    assert entry["audio_path"].startswith("audio/")
    assert entry["audio_path"].endswith(".mp3")


# ---------------------------------------------------------------------
# strip_existing: surgical prefix-strip
# ---------------------------------------------------------------------


def test_strip_existing_removes_only_element_song_prefix(
    generator_module: types.ModuleType,
) -> None:
    """Only entries with the ``element-song-`` prefix are removed."""
    manifest = [
        {"id": "element-song-li-3", "title": "old Q3 entry"},
        {"id": "element-song-be-4", "title": "old Q3 entry 2"},
        {"id": "gold-shiny-rhyme", "title": "M7a entry — must stay"},
        {"id": "hydrogen-tiny-cheer", "title": "M7a entry — must stay"},
        {"id": "silly-noodle-dance", "title": "Phase K entry — must stay"},
    ]
    stripped = generator_module.strip_existing(manifest)
    stripped_ids = {s["id"] for s in stripped}
    assert "element-song-li-3" not in stripped_ids
    assert "element-song-be-4" not in stripped_ids
    assert "gold-shiny-rhyme" in stripped_ids
    assert "hydrogen-tiny-cheer" in stripped_ids
    assert "silly-noodle-dance" in stripped_ids
    assert len(stripped) == 3


def test_idempotent_strip_then_append_preserves_count(
    generator_module: types.ModuleType,
) -> None:
    """strip + append + strip + append → same total, same id set, same order."""
    pre_existing = [
        {"id": "gold-shiny-rhyme", "title": "M7a"},
        {"id": "silly-noodle-dance", "title": "Phase K"},
    ]
    fresh_batch = [
        {"id": "element-song-li-3", "title": "Q3"},
        {"id": "element-song-be-4", "title": "Q3"},
    ]

    first_round = generator_module.strip_existing(pre_existing) + fresh_batch
    second_round = generator_module.strip_existing(first_round) + fresh_batch

    assert len(first_round) == len(second_round)
    assert [e["id"] for e in first_round] == [e["id"] for e in second_round]


# ---------------------------------------------------------------------
# pick_theme: heuristic boundary
# ---------------------------------------------------------------------


def test_pick_theme_music_on_glow_keyword(
    generator_module: types.ModuleType,
) -> None:
    """fun_fact containing ``glow`` → theme=music."""
    element = _make_element(
        element_id="u-92",
        symbol="U",
        name="Uranium",
        atomic_number=92,
        family="actinide",
        fun_fact="Uranium can glow softly under special light.",
    )
    assert generator_module.pick_theme(element) == "music"


def test_pick_theme_silly_default(
    generator_module: types.ModuleType,
) -> None:
    """A fun_fact with no music keywords → theme=silly."""
    element = _make_element(
        element_id="pb-82",
        symbol="Pb",
        name="Lead",
        atomic_number=82,
        family="post_transition_metal",
        fun_fact="Lead is a very heavy metal often used as a fishing weight.",
    )
    assert generator_module.pick_theme(element) == "silly"


# ---------------------------------------------------------------------
# select_target_elements: M7a skip-set application
# ---------------------------------------------------------------------


def test_select_target_elements_skips_m7a_set(
    generator_module: types.ModuleType,
) -> None:
    """select_target_elements drops every M7a element_id from the input."""
    elements = generator_module.load_elements(_REAL_ELEMENTS_JSON)
    targets = generator_module.select_target_elements(elements)
    target_ids = {t["id"] for t in targets}
    assert generator_module.M7A_POPULAR_ELEMENT_IDS.isdisjoint(target_ids)
    assert len(targets) == len(elements) - len(generator_module.M7A_POPULAR_ELEMENT_IDS)


def test_select_target_elements_limit_caps_count(
    generator_module: types.ModuleType,
) -> None:
    """``limit=N`` returns the first N skip-filtered elements."""
    elements = generator_module.load_elements(_REAL_ELEMENTS_JSON)
    targets = generator_module.select_target_elements(elements, limit=3)
    assert len(targets) == 3
    assert all(t["id"] not in generator_module.M7A_POPULAR_ELEMENT_IDS for t in targets)


# ---------------------------------------------------------------------
# Live-mode resilience (HIGH #1 + MEDIUM #3): per-element drop on
# failure, rate-limit retry, partial-batch survival.
#
# These tests inject a fake AnthropicClient whose ``complete_text``
# raises or returns canned text; they NEVER touch the network. We
# disable the synthetic-response path by NOT passing ``--dry-run`` and
# instead monkeypatch ``_load_live_token`` + ``AnthropicClient`` so the
# generator's live-mode branch runs end-to-end in-process.
# ---------------------------------------------------------------------


def _wellformed_lyrics(name: str) -> str:
    """Return a 4-line lyrics payload that always parses cleanly."""
    return (
        f"title: A Song About {name}\n"
        f"theme: silly\n"
        f"lyrics: {name} is fun and bright\n"
        f"{name} shines just right\n"
        f"watch {name} sparkle in the sun\n"
        f"{name} brings joy to everyone\n"
    )


class _FakeResponse:
    """Mimics :class:`toybox.ai.client.AIResponse` (text + model)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake-test-model"


class _ScriptedClient:
    """Test double for AnthropicClient with per-call scripted behaviour.

    ``behaviours`` is a list of strings or exceptions consumed in order
    by ``complete_text``; strings are returned as response text and
    exceptions are raised. Tracks ``calls`` for assertions.
    """

    def __init__(self, behaviours: list[object]) -> None:
        self._behaviours = list(behaviours)
        self.calls: list[str] = []

    async def complete_text(
        self,
        messages: object,
        *,
        max_tokens: int = 1024,
        system: object = None,
    ) -> _FakeResponse:
        # Capture the user content for log-style debugging.
        try:
            self.calls.append(messages[-1].content)  # type: ignore[index, attr-defined]
        except Exception:  # noqa: BLE001 — test-only defensive log capture
            self.calls.append("<unparseable>")
        if not self._behaviours:
            raise RuntimeError("ScriptedClient exhausted")
        nxt = self._behaviours.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return _FakeResponse(str(nxt))


def _install_live_mode_stubs(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    client: _ScriptedClient,
) -> None:
    """Replace token loader + AnthropicClient so live-mode runs in-process."""

    class _FakeToken:
        access_token = "fake-bearer"

    monkeypatch.setattr(generator_module, "_load_live_token", lambda: _FakeToken())
    monkeypatch.setattr(generator_module, "AnthropicClient", lambda _token: client)
    # Make retry backoff instant so 429 tests don't actually sleep.
    monkeypatch.setattr(generator_module, "_LLM_RETRY_BASE_DELAY_SEC", 0.0)


def _write_mini_elements(tmp_path: Path, count: int) -> Path:
    """Write a tiny elements.json with ``count`` non-M7a-overlapping elements.

    Symbols are letter-only single chars (a, b, c, …) and atomic numbers
    are in the 200+ range so the synthetic ids (``a-200``) satisfy
    ``Song.element_id``'s ``^[a-z]{1,3}-[0-9]{1,3}$`` pattern but never
    collide with a real element.
    """
    elements = [
        _make_element(
            element_id=f"{chr(ord('a') + i)}-{200 + i}",
            symbol=chr(ord('A') + i),
            name=f"Element{i}",
            atomic_number=200 + i,
            family="alkali_metal",
        )
        for i in range(count)
    ]
    path = tmp_path / "elements.json"
    path.write_text(json.dumps(elements), encoding="utf-8")
    return path


def _seed_empty_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text("[]\n", encoding="utf-8")
    return path


def test_live_mode_drops_element_on_llm_failure_and_continues(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A single per-element exception drops THAT element, batch continues, exit 0."""
    elements_path = _write_mini_elements(tmp_path, count=5)
    manifest_path = _seed_empty_manifest(tmp_path)

    # Element index 2 (the 3rd) fails with a non-retryable HTTP 401
    # (auth expiry). Everything else succeeds. Expected: 4 entries in
    # the final batch, exit 0, WARN log mentions the failure.
    behaviours: list[object] = [
        _wellformed_lyrics("Element0"),
        _wellformed_lyrics("Element1"),
        urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "Unauthorized", {}, None  # type: ignore[arg-type]
        ),
        _wellformed_lyrics("Element3"),
        _wellformed_lyrics("Element4"),
    ]
    client = _ScriptedClient(behaviours)
    _install_live_mode_stubs(generator_module, monkeypatch, client)

    caplog.set_level("INFO")
    rc = generator_module.main(
        ["--elements", str(elements_path), "--output", str(manifest_path)]
    )
    assert rc == 0

    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    element_song_ids = [e["id"] for e in written if e["id"].startswith("element-song-")]
    assert len(element_song_ids) == 4, (
        f"expected 4 element-song entries after one drop; got {element_song_ids!r}"
    )
    # The 3rd element (index 2 → symbol c, Z=202) is the one that failed.
    assert not any("element-song-c-202" == i for i in element_song_ids), (
        "the failing element must NOT appear in the output"
    )

    warn_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("live LLM call failed" in m for m in warn_messages), (
        f"expected a per-element drop WARN; got {warn_messages!r}"
    )

    # End-of-loop generation summary must be present and accurate.
    info_messages = [r.message for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "generation summary" in m and "succeeded=4" in m and "dropped=1" in m
        for m in info_messages
    ), f"missing accurate generation-summary line; got {info_messages!r}"


def test_live_mode_rate_limit_retries_then_succeeds(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """429 on attempts 1+2, success on attempt 3 → element lands + 2 retry logs."""
    elements_path = _write_mini_elements(tmp_path, count=1)
    manifest_path = _seed_empty_manifest(tmp_path)

    rate_limit = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        429,
        "Too Many Requests",
        {},  # type: ignore[arg-type]
        None,
    )
    behaviours: list[object] = [
        rate_limit,
        urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 429, "Too Many Requests", {}, None  # type: ignore[arg-type]
        ),
        _wellformed_lyrics("Element0"),
    ]
    client = _ScriptedClient(behaviours)
    _install_live_mode_stubs(generator_module, monkeypatch, client)

    caplog.set_level("INFO")
    rc = generator_module.main(
        ["--elements", str(elements_path), "--output", str(manifest_path)]
    )
    assert rc == 0
    assert len(client.calls) == 3, "expected exactly 3 LLM attempts (2 retries + success)"

    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    element_song_ids = [e["id"] for e in written if e["id"].startswith("element-song-")]
    assert len(element_song_ids) == 1, (
        f"expected 1 element-song entry after retry-then-succeed; got {element_song_ids!r}"
    )

    retry_logs = [
        r.message for r in caplog.records if "retrying" in r.message and "429" in r.message
    ]
    assert len(retry_logs) == 2, (
        f"expected 2 retry INFO logs (after attempts 1 and 2); got {retry_logs!r}"
    )


def test_live_mode_rate_limit_retries_then_drops_after_max_attempts(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """429 on every attempt → element dropped after _LLM_RETRY_ATTEMPTS, run continues."""
    elements_path = _write_mini_elements(tmp_path, count=2)
    manifest_path = _seed_empty_manifest(tmp_path)

    def _rl() -> urllib.error.HTTPError:
        # Fresh instance each call so the script can re-raise without
        # re-using a consumed traceback.
        return urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            429,
            "Too Many Requests",
            {},  # type: ignore[arg-type]
            None,
        )

    # Element 0 fails 3 times (dropped); element 1 succeeds.
    behaviours: list[object] = [
        _rl(),
        _rl(),
        _rl(),
        _wellformed_lyrics("Element1"),
    ]
    client = _ScriptedClient(behaviours)
    _install_live_mode_stubs(generator_module, monkeypatch, client)

    caplog.set_level("INFO")
    rc = generator_module.main(
        ["--elements", str(elements_path), "--output", str(manifest_path)]
    )
    assert rc == 0
    # 3 attempts for element 0 + 1 attempt for element 1 = 4 total.
    assert len(client.calls) == 4, (
        f"expected 4 LLM attempts (3 retries + 1 success); got {len(client.calls)}"
    )

    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    element_song_ids = [e["id"] for e in written if e["id"].startswith("element-song-")]
    assert len(element_song_ids) == 1, (
        f"expected 1 element-song entry (element 0 dropped); got {element_song_ids!r}"
    )

    info_messages = [r.message for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "generation summary" in m and "succeeded=1" in m and "dropped=1" in m
        for m in info_messages
    ), f"missing accurate generation-summary line; got {info_messages!r}"


# ---------------------------------------------------------------------
# Atomic write (HIGH #2)
# ---------------------------------------------------------------------


def test_atomic_write_uses_temp_path_and_rename(
    generator_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_write_payload`` writes to ``<name>.json.tmp`` then ``os.replace``s into place.

    Locks the atomicity contract: a Ctrl-C / OOM between the write and
    the rename leaves the original manifest untouched (the .tmp file is
    discarded). Any future refactor that drops back to a direct
    ``write_text`` would break this test.
    """
    target = tmp_path / "manifest.json"
    target.write_text('[{"id": "original-entry"}]\n', encoding="utf-8")

    replace_calls: list[tuple[str, str]] = []
    real_replace = generator_module.os.replace

    def _capturing_replace(src: object, dst: object) -> None:
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(generator_module.os, "replace", _capturing_replace)

    payload = [{"id": "new-entry", "value": 1}]
    generator_module._write_payload(target, payload)

    assert len(replace_calls) == 1, (
        f"expected exactly one os.replace call; got {replace_calls!r}"
    )
    src, dst = replace_calls[0]
    assert src.endswith("manifest.json.tmp"), f"src should be the .tmp path; got {src!r}"
    assert dst.endswith("manifest.json") and not dst.endswith(".tmp"), (
        f"dst should be the final manifest path; got {dst!r}"
    )

    # Final file contains the new payload; the .tmp file was renamed
    # away and no longer exists on disk.
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == payload
    assert not (tmp_path / "manifest.json.tmp").exists(), (
        "temp file must be consumed by os.replace, not left behind"
    )
