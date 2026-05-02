"""Unit tests for ``GET /api/health``."""

from __future__ import annotations

from fastapi.testclient import TestClient

from toybox.app import create_app


def test_health_returns_ok_and_capability_reason() -> None:
    """Phase A contract: ok=True, capability_reason=None."""
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "capability_reason": None}


def test_health_response_keys_are_stable() -> None:
    """Frontend depends on these exact keys."""
    client = TestClient(create_app())
    body = client.get("/api/health").json()
    assert set(body.keys()) == {"ok", "capability_reason"}
