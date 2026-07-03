"""Shared fixtures for the tts unit suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from toybox.tts.engine import reset_engine_cache_for_tests


@pytest.fixture(autouse=True)
def _fresh_engine_cache() -> Iterator[None]:
    """Drop the module-level cached engine around every test.

    No stub-mode test constructs a real engine, but the cache is
    process-global state — reset defensively so a future test that
    monkeypatches ``_build_engine`` can never leak its fake into a
    neighbour.
    """
    reset_engine_cache_for_tests()
    yield
    reset_engine_cache_for_tests()
