"""OAuth token storage for the Claude subscription flow.

The token lives at ``~/.toybox/secrets.json`` (Windows:
``%USERPROFILE%\\.toybox\\secrets.json``). The shape is intentionally
flat so the ``--check`` entrypoint and the background refresh task can
read/write it without a DB round-trip:

.. code-block:: json

    {
        "access_token":  "...",
        "refresh_token": "...",
        "expires_at":    1730000000
    }

Refresh-flow specifics (POST to Anthropic, parse new bearer/refresh
tokens, etc.) live with the SDK wrapper in :mod:`toybox.ai.client`; this
module is purely the on-disk persistence shim.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

# Module-level constant for the canonical secrets path. Tests that need
# to redirect storage point ``secrets_path()`` at a tmp dir via the
# ``TOYBOX_SECRETS_PATH`` env var rather than monkeypatching this
# attribute directly.
SECRETS_PATH: Path = Path.home() / ".toybox" / "secrets.json"

_SECRETS_PATH_ENV = "TOYBOX_SECRETS_PATH"


@dataclass(frozen=True, slots=True)
class OAuthToken:
    """In-memory representation of the persisted OAuth token."""

    access_token: str
    refresh_token: str
    expires_at: int

    def is_expired(self, now_epoch: int) -> bool:
        """Return True iff ``now_epoch >= expires_at``.

        Equality is treated as expired so the breaker's clock-skew
        envelope errs on the safe side.
        """
        return now_epoch >= self.expires_at


def secrets_path() -> Path:
    """Return the secrets-file path, honoring ``TOYBOX_SECRETS_PATH``.

    The env override exists so unit + integration tests can redirect
    storage at a ``tmp_path`` without monkeypatching ``Path.home``.
    """
    raw = os.environ.get(_SECRETS_PATH_ENV)
    return Path(raw) if raw else SECRETS_PATH


def load_token(path: Path | None = None) -> OAuthToken | None:
    """Return the persisted token, or ``None`` if absent / unreadable.

    A malformed JSON file is logged at WARNING and treated as missing —
    this matches the capability gate's ``token_missing`` behavior so the
    operator only has one knob to fix.
    """
    target = path if path is not None else secrets_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("secrets.json unreadable, treating as missing: %s", exc)
        return None
    if not isinstance(raw, dict):
        _logger.warning("secrets.json is not a JSON object, treating as missing")
        return None
    try:
        access = raw["access_token"]
        refresh = raw["refresh_token"]
        expires = raw["expires_at"]
    except KeyError as exc:
        _logger.warning("secrets.json missing key %s, treating as missing", exc)
        return None
    if not isinstance(access, str) or not isinstance(refresh, str):
        _logger.warning("secrets.json access/refresh token is not a string")
        return None
    if not isinstance(expires, int) or isinstance(expires, bool):
        # bool is an int subclass — reject explicitly.
        _logger.warning("secrets.json expires_at is not an int")
        return None
    return OAuthToken(access_token=access, refresh_token=refresh, expires_at=expires)


def save_token(token: OAuthToken, path: Path | None = None) -> None:
    """Atomically persist ``token`` to ``path`` (or the default location).

    The parent directory is created if missing. On POSIX the file is
    chmod'd to ``0o600`` so the token isn't world-readable; on Windows
    the chmod is a no-op (Windows ACLs aren't reachable from stdlib in a
    portable way — leave that to the OS user-profile defaults).
    """
    target = path if path is not None else secrets_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(token), indent=2), encoding="utf-8")
    os.replace(tmp, target)

    if sys.platform != "win32":
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:  # pragma: no cover - filesystem edge
            _logger.warning("could not chmod %s to 0600: %s", target, exc)


__all__ = [
    "OAuthToken",
    "SECRETS_PATH",
    "load_token",
    "save_token",
    "secrets_path",
]
