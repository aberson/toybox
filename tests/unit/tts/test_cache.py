"""Phase Z Z4 — clip-cache keying / URL derivation + the one-constant grep gate.

The cache module owns the entire (voice, text) → clip mapping; these
tests pin (a) the on-disk key layout, (b) that the URL and the path are
derived from the SAME key (producer/consumer can't drift), and (c) the
grep gate that keeps ``/api/static/tts`` a single-source-of-truth
constant — the enforcement mechanism that prevents a repeat of the
songs two-constants wart (``_SONG_AUDIO_URL_PREFIX`` duplicated across
``api/activities.py`` and ``activities/interjection.py``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from toybox.tts import cache

# Deliberately an independent literal (NOT ``cache.TTS_AUDIO_URL_PREFIX``)
# so a constant edit that forgets the mount/tests fails loudly here.
_URL_PREFIX_LITERAL = "/api/static/tts"


# ---------------------------------------------------------------------
# Keying + derivation
# ---------------------------------------------------------------------


def test_clip_key_is_sha256_prefix() -> None:
    text = "What does Miss Maple think?"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[: cache.KEY_HEX_LEN]
    assert cache.clip_key(text) == expected
    assert len(cache.clip_key(text)) == 16


def test_clip_path_layout_under_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    text = "Step one of the adventure."
    path = cache.clip_path("af_heart", text)
    assert path == tmp_path / "tts" / "af_heart" / f"{cache.clip_key(text)}.wav"


def test_data_dir_defaults_to_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOYBOX_DATA_DIR", raising=False)
    assert cache.clips_root() == Path("data") / "tts"


def test_url_and_path_derive_from_the_same_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer persists ``clip_url``; the static mount serves
    ``clip_path`` — both MUST resolve to the same basename or the kiosk
    404s forever. This is the producer→consumer relationship test."""
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    text = "Pick a path now!"
    url = cache.clip_url("am_michael", text)
    path = cache.clip_path("am_michael", text)
    assert url == f"{_URL_PREFIX_LITERAL}/am_michael/{path.name}"
    # The URL's mount-relative suffix is exactly the path relative to
    # clips_root (what StaticFiles resolves against the mount dir).
    relative = url.removeprefix(f"{_URL_PREFIX_LITERAL}/")
    assert (cache.clips_root() / relative) == path


def test_same_text_different_voice_shares_filename_not_dir() -> None:
    text = "Hello there, adventurer!"
    a = cache.clip_path("af_heart", text)
    b = cache.clip_path("am_puck", text)
    assert a.name == b.name
    assert a.parent != b.parent


def test_different_text_different_key() -> None:
    assert cache.clip_key("one") != cache.clip_key("two")


def test_clip_exists_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    text = "A clip that gets rendered."
    assert cache.clip_exists("bf_emma", text) is False
    path = cache.clip_path("bf_emma", text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF....WAVE")
    assert cache.clip_exists("bf_emma", text) is True


@pytest.mark.parametrize(
    "voice",
    [
        "",
        " ",
        "../evil",
        "af/heart",
        "AF_HEART",
        "af heart",
        "af.heart",
        "voice\\traversal",
        # ``re.match`` + ``$`` would accept this ($ matches before a
        # terminal newline) — pinned to fullmatch semantics.
        "af_heart\n",
    ],
)
def test_unsafe_voice_ids_rejected(voice: str) -> None:
    """The voice id becomes a path + URL segment — anything outside the
    Kokoro id alphabet must raise, never touch the filesystem."""
    assert cache.is_safe_voice_id(voice) is False
    with pytest.raises(ValueError):
        cache.clip_path(voice, "text")
    with pytest.raises(ValueError):
        cache.clip_url(voice, "text")


def test_kokoro_voice_ids_accepted() -> None:
    for voice in ("af_heart", "am_michael", "af_bella", "am_puck", "bf_emma"):
        assert cache.is_safe_voice_id(voice) is True


# ---------------------------------------------------------------------
# One-constant grep gate (code-quality.md §2)
# ---------------------------------------------------------------------


def test_url_prefix_literal_appears_only_in_cache_py() -> None:
    """Grep gate: ``/api/static/tts`` must appear in ``tts/cache.py``
    and NOWHERE else under ``src/``. Every producer (activities.py
    enqueue hooks) and the app mount must import
    ``TTS_AUDIO_URL_PREFIX`` / ``clip_url`` instead of restating the
    literal — the mistake that gave the songs prefix two sources of
    truth. A future re-duplication fails THIS test, not a UAT."""
    src_root = Path(__file__).resolve().parents[3] / "src"
    assert src_root.is_dir(), src_root
    offenders: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if _URL_PREFIX_LITERAL not in py_file.read_text(encoding="utf-8"):
            continue
        if py_file.name == "cache.py" and py_file.parent.name == "tts":
            continue
        offenders.append(str(py_file))
    assert offenders == [], (
        f"literal {_URL_PREFIX_LITERAL!r} restated outside toybox/tts/cache.py — "
        f"import TTS_AUDIO_URL_PREFIX/clip_url instead: {offenders}"
    )
    # And the constant itself matches the literal this gate greps for.
    assert cache.TTS_AUDIO_URL_PREFIX == _URL_PREFIX_LITERAL
