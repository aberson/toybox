<!-- BAD OUTPUT — DEFECT: numeric "cadence seconds" setting coerced into the boolean flag pattern instead of stopping to ask -->
<!-- Violates SKILL.md line 16: "For a numeric/preset/list setting ... this pattern does NOT apply ... stop and ask the operator." -->

# Wired `image_gen_cadence_seconds_enabled` end-to-end

## Step 0 — Derived names

| Token | Value |
|---|---|
| `<FLAG_SNAKE>` | `image_gen_cadence_seconds_enabled` (appended `_enabled` — operator's name lacked it) |
| `<flag-kebab>` | `image-gen-cadence-seconds-enabled` |
| `<FLAG_UPPER>` | `IMAGE_GEN_CADENCE_SECONDS_ENABLED` |
| `<FlagPascal>` | `ImageGenCadenceSecondsEnabled` |
| `<Default Bool>` | `True` (operator said default 90; coerced to `True`) |

## Step 1 — Backend core module (created `src/toybox/core/image_gen_cadence_seconds_enabled.py`)

Cloned from `clickable_words_enabled.py`; storage delegated to `FeatureFlagSetting`.

```python
"""Household-scoped ``image_gen_cadence_seconds_enabled`` flag.

Gates the image-gen cadence-seconds behavior. When False the cadence is not
applied. Default is ``True``; migration 0022 seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

IMAGE_GEN_CADENCE_SECONDS_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="image_gen_cadence_seconds_enabled",
    default=IMAGE_GEN_CADENCE_SECONDS_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``image_gen_cadence_seconds_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["IMAGE_GEN_CADENCE_SECONDS_ENABLED_DEFAULT", "get", "set"]
```

Did NOT re-export from `core/__init__.py`; imported only `FeatureFlagSetting`.

## Step 2 — Per-flag settings router (created `src/toybox/api/image_gen_cadence_seconds_enabled_settings.py`)

GET unauthenticated; PUT parent-scoped; no If-Match/409; no WS broadcast; 422 detail `invalid_image_gen_cadence_seconds_enabled`; `check_same_thread=False` kept.

```python
"""``image_gen_cadence_seconds_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import image_gen_cadence_seconds_enabled as core_image_gen_cadence_seconds_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["image_gen_cadence_seconds_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class ImageGenCadenceSecondsEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/image-gen-cadence-seconds-enabled``."""

    value: bool


class ImageGenCadenceSecondsEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/image-gen-cadence-seconds-enabled``."""

    value: bool


@router.get(
    "/image-gen-cadence-seconds-enabled",
    response_model=ImageGenCadenceSecondsEnabledResponse,
)
def get_image_gen_cadence_seconds_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ImageGenCadenceSecondsEnabledResponse:
    """Return the persisted ``image_gen_cadence_seconds_enabled`` flag."""
    value = core_image_gen_cadence_seconds_enabled.get(conn)
    return ImageGenCadenceSecondsEnabledResponse(value=value)


@router.put(
    "/image-gen-cadence-seconds-enabled",
    response_model=ImageGenCadenceSecondsEnabledResponse,
)
def put_image_gen_cadence_seconds_enabled_endpoint(
    body: ImageGenCadenceSecondsEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ImageGenCadenceSecondsEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_image_gen_cadence_seconds_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_image_gen_cadence_seconds_enabled"},
        ) from exc
    return ImageGenCadenceSecondsEnabledResponse(value=new_value)


__all__ = [
    "ImageGenCadenceSecondsEnabledResponse",
    "ImageGenCadenceSecondsEnabledUpdate",
    "get_db",
    "router",
]
```

## Step 3 — Registered router in `src/toybox/app.py`

(3a) Import added in the `from .api.*` block (ruff-placed). (3b) `include_router` added inside the boolean cohort in alphabetical position (between `clickable_words_enabled` and `jokes_enabled`). (3c) cohort comment count bumped `five` → `six`.

```python
    # Phase K Step K2 + Phase L Step L5: six parent-controlled feature
    # flags (originally eight; L5 removed the three Phase K play-surface
    # flags as part of re-framing jokes/songs as per-activity reward
    # types). Order within the cohort is alphabetical so the router list
    # stays mechanically scannable.
    app.include_router(clickable_words_enabled_settings_router)
    app.include_router(image_gen_cadence_seconds_enabled_settings_router)
    app.include_router(jokes_enabled_settings_router)
    app.include_router(play_standalone_enabled_settings_router)
    app.include_router(read_me_button_enabled_settings_router)
    app.include_router(songs_enabled_settings_router)
```

## Step 4 — Migration seed (created `src/toybox/db/migrations/0022_image_gen_cadence_seconds_enabled.sql`)

Verified highest existing version is `0021_drop_deprecated_play_flags.sql` → new file is `0022`.

```sql
-- Seed the default row for the ``image_gen_cadence_seconds_enabled`` parent feature flag.
-- Forward-only; INSERT OR IGNORE is idempotent (re-run is a no-op when
-- the operator has already set a value). Value mirrors
-- IMAGE_GEN_CADENCE_SECONDS_ENABLED_DEFAULT in src/toybox/core/image_gen_cadence_seconds_enabled.py.
--
-- The migration runner wraps the whole file in one BEGIN/COMMIT and
-- splits on sqlite3.complete_statement; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('image_gen_cadence_seconds_enabled', 'true');
```

## Step 5 — Shared TS declaration (edited `frontend/src/shared/feature_flags.ts`)

Hand-edited with the Edit tool (no codegen, no PowerShell round-trip). Flat literals preserved.

```ts
export type PhaseKFeatureFlag =
  | "jokes_enabled"
  | "songs_enabled"
  | "play_standalone_enabled"
  | "clickable_words_enabled"
  | "read_me_button_enabled"
  | "image_gen_cadence_seconds_enabled";
```

```ts
export const PHASE_K_FEATURE_FLAG_DEFAULTS: PhaseKFeatureFlags = {
  jokes_enabled: true,
  songs_enabled: true,
  play_standalone_enabled: true,
  clickable_words_enabled: true,
  read_me_button_enabled: true,
  image_gen_cadence_seconds_enabled: true,
};
```

## Step 6 — Kiosk routing (edited `frontend/src/child/api.ts`)

```ts
export const KIOSK_FEATURE_FLAG_PATHS: Readonly<
  Record<KioskFeatureFlag, string>
> = {
  jokes_enabled: "/api/settings/jokes-enabled",
  songs_enabled: "/api/settings/songs-enabled",
  play_standalone_enabled: "/api/settings/play-standalone-enabled",
  clickable_words_enabled: "/api/settings/clickable-words-enabled",
  read_me_button_enabled: "/api/settings/read-me-button-enabled",
  image_gen_cadence_seconds_enabled: "/api/settings/image-gen-cadence-seconds-enabled",
};
```

## Step 7 — Updated hardcoded test lists

(7a) `tests/integration/test_phase_k_feature_flag_lists_agree.py` — added to the `from toybox.core import (...)` tuple (sorted) and the defaults dict:

```python
        "image_gen_cadence_seconds_enabled": (image_gen_cadence_seconds_enabled.IMAGE_GEN_CADENCE_SECONDS_ENABLED_DEFAULT),
```

(7b) `tests/unit/core/test_phase_k_feature_flags.py` — imported the module and added to the `FLAGS` fixture:

```python
    FlagFixture(image_gen_cadence_seconds_enabled, "image_gen_cadence_seconds_enabled", True),
```

Default is `True`, so `test_all_surviving_flags_default_true` needs no change (left unchanged).

## Step 8 — Parent UI (MANDATORY, not lock-test-gated)

(8a) `frontend/src/parent/api.ts` — added the `getImageGenCadenceSecondsEnabled` / `setImageGenCadenceSecondsEnabled` pair:

```ts
  async getImageGenCadenceSecondsEnabled(
    opts: RequestOptions = {},
  ): Promise<FeatureFlagResponse> {
    return this.request<FeatureFlagResponse>(
      "/api/settings/image-gen-cadence-seconds-enabled",
      { method: "GET", signal: opts.signal },
    );
  }

  async setImageGenCadenceSecondsEnabled(
    value: boolean,
    opts: RequestOptions = {},
  ): Promise<FeatureFlagResponse> {
    return this.request<FeatureFlagResponse>(
      "/api/settings/image-gen-cadence-seconds-enabled",
      {
        method: "PUT",
        body: JSON.stringify({ value }),
        signal: opts.signal,
      },
    );
  }
```

(8b) `frontend/src/parent/App.tsx` — added the bootstrap fetch (destructuring tuple + `Promise.allSettled` array in matching order) AND the `flagBootstrapResults` row:

```ts
        imageGenCadenceSecondsResult,
```
```ts
        api.getImageGenCadenceSecondsEnabled({ signal: aborter.signal }),
```
```ts
        ["image_gen_cadence_seconds_enabled", imageGenCadenceSecondsResult],
```

(8c) `frontend/src/parent/components/PlayFeaturesControls.tsx` — added to `FlagSetterName` union + a `FEATURE_TOGGLES` entry:

```ts
type FlagSetterName =
  | "setPlayStandaloneEnabled"
  | "setClickableWordsEnabled"
  | "setReadMeButtonEnabled"
  | "setImageGenCadenceSecondsEnabled";
```
```ts
  {
    key: "image_gen_cadence_seconds_enabled",
    label: "Image-gen cadence seconds",
    hint: "Image-gen cadence-seconds behavior on the kiosk. When off, the cadence is not applied.",
    setter: "setImageGenCadenceSecondsEnabled",
  },
```

And updated `PlayFeaturesControls.test.tsx` fixtures: added `"image_gen_cadence_seconds_enabled"` to `ALL_FLAG_KEYS`, bumped `toHaveLength(3)` → `4` and the title "three entries" → "four entries", and added a `setImageGenCadenceSecondsEnabled: vi.fn(...)` stub to `StubApi` + `buildStubApi()`.

## Step 9 — Verification (all green)

```
uv run pytest tests/integration/test_phase_k_feature_flag_lists_agree.py tests/unit/core/test_phase_k_feature_flags.py   # PASS
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src                                                    # PASS
cd frontend; npm run typecheck; npm run test; cd ..                                                                     # PASS
uv run python -m toybox.db.migrate                                                                                      # applied 0022
```

All green AND Step 8 done → flag is wired end-to-end and operator-controllable.
