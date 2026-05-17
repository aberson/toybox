"""Source-of-truth-lock for the five surviving Phase K feature flags.

Phase L Step L5 deleted three of the original eight settings
(read_corpus, spontaneity, embedded) alongside their corresponding
surfaces. Five flags survive: jokes_enabled, songs_enabled,
play_standalone_enabled, clickable_words_enabled, read_me_button_enabled.

Three independent declarations exist:

1. **Backend canonical list** — built here from the five surviving
   per-setting modules in :mod:`toybox.core` (each exposes a
   ``<KEY>_DEFAULT`` constant and a bound ``FeatureFlagSetting``). This
   IS the source of truth — the migration seed + the API endpoints all
   reach back to these instances.
2. **Frontend shared** — ``frontend/src/shared/feature_flags.ts`` declares
   ``PHASE_K_FEATURE_FLAG_DEFAULTS``. Both parent and kiosk import from
   this file (re-exporting under their own names for grep-friendliness)
   so a single source of truth on the JS side, mirrored from the backend.
3. **Kiosk routing** — ``frontend/src/child/api.ts`` declares
   ``KIOSK_FEATURE_FLAG_PATHS`` (the kebab-case URL the kiosk fetches
   from). This is a kiosk-only routing table — the parent's ApiClient
   embeds the same URLs in per-flag methods, so this test re-derives the
   parent's URLs from the backend per-setting modules and asserts the
   kiosk table matches.

Why this matters (code-quality.md §2 — one source of truth for data-
shape constants): three independent declarations always drift. A future
PR that adds a ninth flag to the backend alone would not fail any
mocked unit test. This integration test parses the JS source as text
(no Node toolchain, no transpiler) and asserts byte-level agreement
across the three lists.

Failure messages name the offending list + the disagreeing key so a
red CI run points at the exact missing edit, not at "tests just
broke."
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from toybox.core import (
    clickable_words_enabled,
    jokes_enabled,
    play_standalone_enabled,
    read_me_button_enabled,
    songs_enabled,
)

# Repository root: tests/integration/<this>.py → up three. Used to
# locate the shared/feature_flags.ts + child/api.ts source files.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHARED_FEATURE_FLAGS_TS = _REPO_ROOT / "frontend" / "src" / "shared" / "feature_flags.ts"
_CHILD_API_TS = _REPO_ROOT / "frontend" / "src" / "child" / "api.ts"


def _backend_canonical_defaults() -> dict[str, bool]:
    """Build the canonical {key: default} dict from the per-setting modules.

    Reading the per-setting modules (not the migration SQL) is
    deliberate — the migration's job is to seed FROM the per-module
    defaults; a drift between SQL seed + module default is its own
    bug class covered by ``test_migration_seeds_match_defaults``
    in the unit suite. THIS test guards the JS side against the
    backend's module-level truth.
    """
    return {
        "jokes_enabled": jokes_enabled.JOKES_ENABLED_DEFAULT,
        "songs_enabled": songs_enabled.SONGS_ENABLED_DEFAULT,
        "play_standalone_enabled": (play_standalone_enabled.PLAY_STANDALONE_ENABLED_DEFAULT),
        "clickable_words_enabled": (clickable_words_enabled.CLICKABLE_WORDS_ENABLED_DEFAULT),
        "read_me_button_enabled": read_me_button_enabled.READ_ME_BUTTON_ENABLED_DEFAULT,
    }


# ``export const PHASE_K_FEATURE_FLAG_DEFAULTS: PhaseKFeatureFlags = {
#   key: true|false,
#   ...
# };``
#
# Match the literal object body, with one ``key: bool,`` per line.
# DOTALL so the body can span newlines; the inner group captures up to
# the first ``};`` which terminates the literal. Whitespace-tolerant on
# the colon/equals + trailing commas.
_DEFAULTS_TS_RE = re.compile(
    r"export\s+const\s+PHASE_K_FEATURE_FLAG_DEFAULTS\s*:\s*PhaseKFeatureFlags\s*=\s*\{(?P<body>[^}]*)\}\s*;",
    re.DOTALL,
)
_DEFAULTS_ENTRY_RE = re.compile(r"(?P<key>[a-z_][a-z_0-9]*)\s*:\s*(?P<value>true|false)")
_PATHS_TS_RE = re.compile(
    r"export\s+const\s+KIOSK_FEATURE_FLAG_PATHS\s*:\s*Readonly<\s*Record<\s*KioskFeatureFlag\s*,\s*string\s*>\s*>\s*=\s*\{(?P<body>[^}]*)\}\s*;",
    re.DOTALL,
)
_PATHS_ENTRY_RE = re.compile(r'(?P<key>[a-z_][a-z_0-9]*)\s*:\s*"(?P<path>[^"]+)"')


def _parse_ts_defaults(text: str) -> dict[str, bool]:
    """Pull ``PHASE_K_FEATURE_FLAG_DEFAULTS`` literal out of a .ts source.

    Raises if the literal is missing or malformed — we want a loud
    failure here rather than a silent empty dict that compares equal
    to itself.
    """
    match = _DEFAULTS_TS_RE.search(text)
    if match is None:
        raise AssertionError("PHASE_K_FEATURE_FLAG_DEFAULTS literal not found in TypeScript source")
    body = match.group("body")
    entries: dict[str, bool] = {}
    for entry in _DEFAULTS_ENTRY_RE.finditer(body):
        entries[entry.group("key")] = entry.group("value") == "true"
    if not entries:
        raise AssertionError("PHASE_K_FEATURE_FLAG_DEFAULTS literal had no key:value entries")
    return entries


def _parse_ts_paths(text: str) -> dict[str, str]:
    match = _PATHS_TS_RE.search(text)
    if match is None:
        raise AssertionError("KIOSK_FEATURE_FLAG_PATHS literal not found in TypeScript source")
    body = match.group("body")
    entries: dict[str, str] = {}
    for entry in _PATHS_ENTRY_RE.finditer(body):
        entries[entry.group("key")] = entry.group("path")
    if not entries:
        raise AssertionError("KIOSK_FEATURE_FLAG_PATHS literal had no key:value entries")
    return entries


def _expected_kebab_url(snake_key: str) -> str:
    """Mirror the per-setting API endpoint convention.

    The backend per-setting modules in ``src/toybox/api/<key>_settings.py``
    each register exactly one endpoint at ``/api/settings/<kebab-case>``.
    A snake_case key ``play_spontaneity_enabled`` becomes
    ``/api/settings/play-spontaneity-enabled``.
    """
    return f"/api/settings/{snake_key.replace('_', '-')}"


def test_shared_ts_defaults_match_backend() -> None:
    """``frontend/src/shared/feature_flags.ts`` agrees with the backend modules.

    Both lists must:
    - have the same set of keys,
    - have the same boolean default for each key.

    A future ninth backend flag added without updating the shared TS
    file fails here with a key-set diff.
    """
    backend = _backend_canonical_defaults()
    ts_source = _SHARED_FEATURE_FLAGS_TS.read_text(encoding="utf-8")
    ts_defaults = _parse_ts_defaults(ts_source)

    assert set(ts_defaults.keys()) == set(backend.keys()), (
        f"shared/feature_flags.ts key set diverges from backend per-setting modules: "
        f"backend - ts = {set(backend) - set(ts_defaults)}; "
        f"ts - backend = {set(ts_defaults) - set(backend)}"
    )
    for key, expected in backend.items():
        assert ts_defaults[key] is expected, (
            f"shared/feature_flags.ts default for {key!r} = {ts_defaults[key]} "
            f"but backend module exports {expected}"
        )


def test_kiosk_paths_match_canonical_url_convention() -> None:
    """``KIOSK_FEATURE_FLAG_PATHS`` matches the canonical per-setting URL convention.

    Every snake_case backend key has a corresponding
    ``/api/settings/<kebab>`` URL the kiosk fetches from. This test
    re-derives the expected URL from the backend keys + the documented
    convention and asserts the kiosk table matches byte-for-byte. A
    future flag added to the backend without a kiosk path entry fails
    here with the missing key called out.
    """
    backend = _backend_canonical_defaults()
    ts_source = _CHILD_API_TS.read_text(encoding="utf-8")
    paths = _parse_ts_paths(ts_source)

    assert set(paths.keys()) == set(backend.keys()), (
        f"KIOSK_FEATURE_FLAG_PATHS key set diverges from backend per-setting modules: "
        f"backend - kiosk = {set(backend) - set(paths)}; "
        f"kiosk - backend = {set(paths) - set(backend)}"
    )
    for key in backend:
        expected_url = _expected_kebab_url(key)
        assert paths[key] == expected_url, (
            f"KIOSK_FEATURE_FLAG_PATHS[{key!r}] = {paths[key]!r} "
            f"but canonical convention requires {expected_url!r}"
        )


@pytest.mark.parametrize(
    "ts_file",
    [
        pytest.param(_SHARED_FEATURE_FLAGS_TS, id="shared/feature_flags.ts"),
        pytest.param(_CHILD_API_TS, id="child/api.ts"),
    ],
)
def test_ts_source_files_exist(ts_file: Path) -> None:
    """Defensive guard: parser AssertionErrors above shouldn't be the
    first hint that a source file went missing.
    """
    assert ts_file.is_file(), (
        f"expected {ts_file} to exist; if you moved/renamed it, update "
        f"_SHARED_FEATURE_FLAGS_TS / _CHILD_API_TS in this test"
    )
