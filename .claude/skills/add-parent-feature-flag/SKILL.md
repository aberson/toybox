---
name: add-parent-feature-flag
description: Add a new parent-controlled boolean feature flag to toybox end-to-end (backend core module + per-flag settings router + app registration + migration seed + shared TS declaration + kiosk routing + parent-UI toggle/fetch + oracle/unit tests). Use when the operator says "add a parent feature flag <name>", "add a feature flag", "gate <feature> behind a parent toggle", or "wire up a new enabled/disabled setting" for the toybox project.
user-invocable: true
---

# Add a parent feature flag (toybox)

Wire one new parent-controlled **boolean** feature flag through every touchpoint a Phase K2 flag spans. The shape is a leaf-helper pattern: storage logic lives ONCE in `src/toybox/core/_feature_flag.py`; each flag is a thin clone.

Two classes of touchpoint, and you must understand the difference or you will ship a half-wired flag that LOOKS done (green CI) but has no operator control:

- **Lock-/typecheck-gated touchpoints** (Steps 1-7) — a source-of-truth-lock integration test + `tsc` cross-check three independent declarations and fail CI if any drift. Skip one and the build goes red, so you can't miss them.
- **Silently-optional touchpoints** (Step 8, the parent UI) — the parent React app's flag-fetch and toggle surface is a *curated subset*, NOT a `Record<Flag,...>`, so TypeScript does NOT force a new entry and the lock test never reads it. Skip Step 8 and you ship a flag that passes every test, is persisted correctly, drives the kiosk correctly — but the **parent UI never fetches it and offers no toggle**, so the operator can never see or change it. This is the exact silent-wiring trap toybox `code-quality.md` §3/§4 warns about. Step 8 is mandatory for an operator-controllable flag.

This skill is for **boolean** enabled/disabled flags only. For a numeric/preset/list setting (e.g. a cadence-seconds or banned-themes setting), this pattern does NOT apply — those live in a separate cohort and have a different shape; stop and ask the operator.

---

## Step 0: Derive the names (do this first, write them down)

From the operator's flag name, derive the canonical tokens. **The convention requires the literal `_enabled` suffix** for boolean flags (`jokes_enabled`, `clickable_words_enabled`, `read_me_button_enabled`). If the operator's name lacks it, append `_enabled`.

Then derive every form from the snake_case key. **The TS union members + defaults keys + kiosk-path keys are snake_case** quoted string literals, identical to the Pydantic key — do NOT camelCase those. The ONLY camelCase forms are (a) the Pydantic model class names (`<FlagPascal>Response`/`<FlagPascal>Update`) and (b) the parent-UI `ApiClient` method names (`get<FlagPascal>` / `set<FlagPascal>`, Step 8).

| Token | Derivation | Worked example: operator says "starfield background" |
|---|---|---|
| `<FLAG_SNAKE>` | lowercase, `_`-joined, `+_enabled` if missing | `starfield_background_enabled` |
| `<flag-kebab>` | `<FLAG_SNAKE>` with every `_` → `-` | `starfield-background-enabled` |
| `<FLAG_UPPER>` | `<FLAG_SNAKE>`.upper() | `STARFIELD_BACKGROUND_ENABLED` |
| `<FlagPascal>` | PascalCase of `<FLAG_SNAKE>` | `StarfieldBackgroundEnabled` |
| `<Default Bool>` | ask operator; default `True` unless told otherwise | `True` |

Used where:
- `<FLAG_SNAKE>` — core module filename + setting key + TS union member + TS defaults key + kiosk map key + parent bootstrap row + 422 `invalid_<FLAG_SNAKE>` + migration row key.
- `<flag-kebab>` — HTTP route leaf + kiosk path value + parent `ApiClient` URL literal (`/api/settings/<flag-kebab>`).
- `<FLAG_UPPER>` — `<FLAG_UPPER>_DEFAULT` constant. **The unit test derives this name as `f"{key.upper()}_DEFAULT"`, so it MUST be exactly `<FLAG_SNAKE>.upper() + "_DEFAULT"`.**
- `<FlagPascal>` — Pydantic model class names + parent `ApiClient` method names (`get<FlagPascal>` / `set<FlagPascal>`).

Kebab derivation is **mechanical and test-enforced** (`/api/settings/{snake.replace('_','-')}`). `read_me_button_enabled` → `/api/settings/read-me-button-enabled` (NOT `read-me-enabled`). Do not abbreviate.

---

## Step 1: Backend core module (source of truth)

**Create** `src/toybox/core/<FLAG_SNAKE>.py`. Clone this verbatim from `clickable_words_enabled.py`; substitute only the marked tokens. Do NOT re-implement storage — `FeatureFlagSetting` (in `_feature_flag.py`) owns string `'true'`/`'false'` storage, defensive read, and `ValueError`-on-non-bool. The `# noqa: A001` on `set` is load-bearing (ruff flags shadowing builtin `set`); keep it exactly.

```python
"""Household-scoped ``<FLAG_SNAKE>`` flag.

<one-paragraph description of what the flag gates and what False does>.
Default is ``<Default Bool>``; migration <NNNN> seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

<FLAG_UPPER>_DEFAULT: bool = <Default Bool>

_SETTING = FeatureFlagSetting(
    key="<FLAG_SNAKE>",
    default=<FLAG_UPPER>_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``<FLAG_SNAKE>`` flag (default ``<Default Bool>``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["<FLAG_UPPER>_DEFAULT", "get", "set"]
```

Notes: `core/__init__.py` is docstring-only — do NOT re-export there; `from toybox.core import <FLAG_SNAKE>` works as a plain submodule import. Do NOT import `_feature_flag`'s internals (its `__all__` is intentionally empty) — only `FeatureFlagSetting`.

---

## Step 2: Per-flag settings router

**Create** `src/toybox/api/<FLAG_SNAKE>_settings.py`. Clone verbatim from `clickable_words_enabled_settings.py`. Invariants to preserve:
- GET is **unauthenticated** (household read). PUT is **parent-scoped** via `RequireScope({TokenScope.parent})` — do NOT add auth to GET.
- The router defines its OWN `get_db` (duplicated by per-setting-module convention); `RequireScope` uses its own `get_auth_db` internally. Don't merge them.
- `check_same_thread=False` is load-bearing (anyio threadpool) — keep it.
- **No If-Match-Version / no 409.** Settings PUTs are last-write-wins. The 409/If-Match-Version optimistic-concurrency rule in toybox CLAUDE.md applies to *activity-mutation* routes ONLY, not settings flags. Do not add a version token.
- No WS broadcast on change (single-parent kiosk re-fetches on mount). Don't add one.

```python
"""``<FLAG_SNAKE>`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import <FLAG_SNAKE> as core_<FLAG_SNAKE>
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["<FLAG_SNAKE>_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class <FlagPascal>Response(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/<flag-kebab>``."""

    value: bool


class <FlagPascal>Update(BaseModel):
    """Request body for ``PUT /api/settings/<flag-kebab>``."""

    value: bool


@router.get("/<flag-kebab>", response_model=<FlagPascal>Response)
def get_<FLAG_SNAKE>_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> <FlagPascal>Response:
    """Return the persisted ``<FLAG_SNAKE>`` flag."""
    value = core_<FLAG_SNAKE>.get(conn)
    return <FlagPascal>Response(value=value)


@router.put("/<flag-kebab>", response_model=<FlagPascal>Response)
def put_<FLAG_SNAKE>_endpoint(
    body: <FlagPascal>Update,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> <FlagPascal>Response:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_<FLAG_SNAKE>.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_<FLAG_SNAKE>"},
        ) from exc
    return <FlagPascal>Response(value=new_value)


__all__ = [
    "<FlagPascal>Response",
    "<FlagPascal>Update",
    "get_db",
    "router",
]
```

Naming recap for this file: route leaf = `<flag-kebab>`; `tags=` keeps snake_case + `_settings`; 422 detail = `invalid_<FLAG_SNAKE>`; model classes = `<FlagPascal>Response`/`<FlagPascal>Update`; core import alias = `core_<FLAG_SNAKE>`.

---

## Step 3: Register the router in `create_app()`

**Edit** `src/toybox/app.py` (the ONLY file with `include_router` — `main.py` does NOT register routers). Three edits:

**(3a)** Add the import in the `from .api.*` block, in isort order. Single-line if it fits the line-length limit; otherwise the parenthesized form (ruff format decides — do NOT hand-guess the wrap or the slot, let `ruff` place it):
```python
from .api.<FLAG_SNAKE>_settings import (
    router as <FLAG_SNAKE>_settings_router,
)
```

**(3b)** Add the `include_router` call **inside** the boolean-flag cohort (currently lines 82-86), in **alphabetical** position among the `*_enabled_settings_router` lines:
```python
    app.include_router(<FLAG_SNAKE>_settings_router)
```
Boolean flags go in this cohort (`clickable_words_enabled` → `jokes_enabled` → `play_standalone_enabled` → `read_me_button_enabled` → `songs_enabled`). Numeric/preset settings (the `image_gen` / `banned_themes` / `transcript_retention` / `play_target_depth` / `play_cadence_seconds` block above it) are a separate, non-alphabetical group — a boolean flag does NOT go there.

**(3c) Bump the cohort comment count.** The comment above the cohort hardcodes the flag count. The actual text in `app.py` (lines 77-81) is:
```
    # Phase K Step K2 + Phase L Step L5: five parent-controlled feature
    # flags (originally eight; L5 removed the three Phase K play-surface
    # flags as part of re-framing jokes/songs as per-activity reward
    # types). Order within the cohort is alphabetical so the router list
    # stays mechanically scannable.
```
Change `five` → `six` for truth-in-comment hygiene (the codebase is meticulous about comment-vs-reality). NOTE: no test parses this comment — it is NOT lock-test-gated, just convention. Don't break a build over it, but do update it.

---

## Step 4: Migration seed (forward-only)

**Create** `src/toybox/db/migrations/<NNNN>_<FLAG_SNAKE>.sql`. **Verify the version first** — list `src/toybox/db/migrations/*.sql`, take `MAX(version)+1`, zero-pad to 4 digits. As of this writing the highest is `0021_drop_deprecated_play_flags.sql`, so a fresh flag is `0022`; re-derive in case more migrations have landed. Filename must match `^(\d+)_[A-Za-z0-9_]+\.sql$`.

Migration-runner constraints (the runner wraps the whole file in one BEGIN/COMMIT and splits on `sqlite3.complete_statement`):
- NO `BEGIN`/`COMMIT`/`ROLLBACK` of your own.
- EVERY statement ends with `;`.
- Forward-only — no down/rollback.
- Boolean value is the lowercase STRING `'true'` / `'false'` (NOT `0`/`1`, NOT capitalized). `INSERT OR IGNORE` is idempotent (preserves an operator's chosen value on re-run). `get()` falls back to the module default if the row is absent, so seeding is for operator visibility, not correctness.

```sql
-- Seed the default row for the ``<FLAG_SNAKE>`` parent feature flag.
-- Forward-only; INSERT OR IGNORE is idempotent (re-run is a no-op when
-- the operator has already set a value). Value mirrors
-- <FLAG_UPPER>_DEFAULT in src/toybox/core/<FLAG_SNAKE>.py.
--
-- The migration runner wraps the whole file in one BEGIN/COMMIT and
-- splits on sqlite3.complete_statement; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('<FLAG_SNAKE>', '<true|false>');
```

Use `'true'` if `<Default Bool>` is `True`, else `'false'`.

---

## Step 5: Shared TS declaration (hand-edited, NOT codegen)

**Edit** `frontend/src/shared/feature_flags.ts` with the **Edit tool** (UTF-8; the header contains non-ASCII — the `§` section sign in "code-quality §2" — so do NOT round-trip through PowerShell `Get-Content -Raw`/`Set-Content`, which mojibakes it through cp1252). This file has **no** `// Regenerate with python tools/gen_types_ts.py` header — it is NOT codegen; `tools/gen_types_ts.py` knows nothing about it.

Keep the object literals **flat** (no nested braces) — the lock-test regex captures the body as `[^}]*` and stops at the first `}`.

**(5a)** Add a union member to `PhaseKFeatureFlag` (snake_case quoted string, leading `|`; keep the `;` on the last member):
```ts
  | "<FLAG_SNAKE>";
```

**(5b)** Add a defaults entry to `PHASE_K_FEATURE_FLAG_DEFAULTS` (bare lowercase TS boolean, trailing comma):
```ts
  <FLAG_SNAKE>: <true|false>,
```
Value must be `true`/`false` matching `<Default Bool>` exactly — the lock test compares with `is` identity to the backend `<FLAG_UPPER>_DEFAULT`.

---

## Step 6: Kiosk routing entry (`KIOSK_FEATURE_FLAG_PATHS`)

**Edit** `frontend/src/child/api.ts`, constant `KIOSK_FEATURE_FLAG_PATHS` (around lines 216-224). Add ONE flat entry:
```ts
  <FLAG_SNAKE>: "/api/settings/<flag-kebab>",
```
Double-quoted value (single quotes won't parse in the lock-test regex and silently drop the entry → count assertion fails).

**Decision rule — does every flag go here?** YES, always. There is no kiosk-only subset. The map is typed `Readonly<Record<KioskFeatureFlag, string>>` and `KioskFeatureFlag === PhaseKFeatureFlag` (it's a re-exported alias of the same union) — so the moment Step 5 adds a union member, `tsc` requires the matching `Record` key here. The lock test also asserts `set(paths.keys()) == backend keys`. So: add the union member → you MUST add the kiosk path. (The comment near line 358 still says "one of the eight feature flags" — stale; leave it or fix it, but it isn't load-bearing.)

---

## Step 7: Update the tests that hardcode the flag list

The lock test's backend list is **hardcoded Python**, not derived. Two test files must be edited or they red-fail.

**(7a)** `tests/integration/test_phase_k_feature_flag_lists_agree.py` — in `_backend_canonical_defaults()`:
- add `<FLAG_SNAKE>` to the `from toybox.core import (...)` tuple (keep it sorted), and
- add the dict entry:
```python
        "<FLAG_SNAKE>": <FLAG_SNAKE>.<FLAG_UPPER>_DEFAULT,
```

**(7b)** `tests/unit/core/test_phase_k_feature_flags.py` — add to the `FLAGS` fixture list (positional order is `module, key, default`):
```python
    FlagFixture(<FLAG_SNAKE>, "<FLAG_SNAKE>", <Default Bool>),
```
(import `<FLAG_SNAKE>` at the top alongside the other modules, in the `from toybox.core import (...)` block). This drives `test_migration_seeds_match_defaults` (asserts the raw `'true'`/`'false'` seed string matches the default — the SQL↔module lock the integration oracle deliberately does NOT cover) and `test_get_returns_seeded_default` / `test_default_matches_spec`.

**If `<Default Bool>` is `False`:** `test_all_surviving_flags_default_true` will break — it asserts `all(f.default is True for f in FLAGS)`, and its docstring claims the lone opt-in flag was deleted in L5. That encodes a post-L5 reality. Update it to exclude the new flag (e.g. assert the count of `False`-defaulting flags, or filter `<FLAG_SNAKE>` out) AND fix the docstring. Do NOT just delete the test.

---

## Step 8: Parent UI — fetch + toggle (MANDATORY, but NOT lock-test-gated)

This is the step the green-CI trap hides. None of the parent-UI surface is forced by the lock test or `tsc`, so a flag wired only through Steps 1-7 ships *invisible to the operator* — the parent app keeps showing the hardcoded default and offers no on/off control. To make the flag operator-controllable you MUST do 8a + 8b; do 8c if you want a UI toggle in the standard "Play features" panel.

**(8a) `frontend/src/parent/api.ts` — add a getter + setter method pair** on `ApiClient`. Clone the `getClickableWordsEnabled` / `setClickableWordsEnabled` pair verbatim (around lines 1849-1893), substituting tokens. Method names are camelCase `get<FlagPascal>` / `set<FlagPascal>`; the URL literal is `/api/settings/<flag-kebab>`:
```ts
  async get<FlagPascal>(
    opts: RequestOptions = {},
  ): Promise<FeatureFlagResponse> {
    return this.request<FeatureFlagResponse>(
      "/api/settings/<flag-kebab>",
      { method: "GET", signal: opts.signal },
    );
  }

  async set<FlagPascal>(
    value: boolean,
    opts: RequestOptions = {},
  ): Promise<FeatureFlagResponse> {
    return this.request<FeatureFlagResponse>(
      "/api/settings/<flag-kebab>",
      {
        method: "PUT",
        body: JSON.stringify({ value }),
        signal: opts.signal,
      },
    );
  }
```

**(8b) `frontend/src/parent/App.tsx` — fetch the flag at bootstrap.** Two edits in the bootstrap effect:
- In the `Promise.allSettled([...])` array (around lines 346-365) add `api.get<FlagPascal>({ signal: aborter.signal }),` alongside the other `get*Enabled` calls, and add a matching `<flag>Result` name to the destructuring tuple (around lines 335-345). Tuple order MUST line up with the array order.
- In the `flagBootstrapResults` array (around lines 416-424) add the row:
```ts
        ["<FLAG_SNAKE>", <flag>Result],
```
This is what folds the fetched value into the lifted `featureFlags` state. WITHOUT this row the parent UI never reads the persisted value — `tsc` will not complain because `flagBootstrapResults` is a tuple array (any subset of keys is legal), and `nextFlags` is seeded from `PHASE_K_FEATURE_FLAG_DEFAULTS` so the new key silently stays at its default. This is the silent gap.

**(8c) Surface a toggle.** Most boolean flags belong in the standard panel: edit `frontend/src/parent/components/PlayFeaturesControls.tsx`:
- add `"set<FlagPascal>"` to the `FlagSetterName` union, and
- add a `FeatureToggleSpec` entry to `FEATURE_TOGGLES` (`key: "<FLAG_SNAKE>"`, an operator-facing `label`, a one-sentence `hint`, `setter: "set<FlagPascal>"`).

Adding to `FEATURE_TOGGLES` **red-fails `PlayFeaturesControls.test.tsx`** until you also update its fixtures — these vitest assertions hardcode the count:
- `ALL_FLAG_KEYS` (around line 57) — add `"<FLAG_SNAKE>"`.
- `FEATURE_TOGGLES).toHaveLength(3)` (around line 80) → `4`, and the test title "exactly three entries" → "four".
- `StubApi` type + `buildStubApi()` (around lines 33-52) — add a `set<FlagPascal>: vi.fn(...)` stub so the new setter is mockable.

(Content-master flags like jokes/songs live in `RewardsSection` instead of `PlayFeaturesControls` per Phase L L8; only route there if the operator explicitly wants a Rewards-header toggle. The default home for a new boolean flag is `PlayFeaturesControls`.)

---

## Sources of truth that must agree

The integration lock test (`test_phase_k_feature_flag_lists_agree.py`) cross-checks these THREE declarations by key set + value (booleans via `is`, paths byte-for-byte). All must contain the new flag:

1. **Backend modules** (source of truth) — `src/toybox/core/<FLAG_SNAKE>.py` `<FLAG_UPPER>_DEFAULT` (Step 1), surfaced via the hardcoded `_backend_canonical_defaults()` (Step 7a).
2. **Frontend shared** — `frontend/src/shared/feature_flags.ts`: `PhaseKFeatureFlag` union + `PHASE_K_FEATURE_FLAG_DEFAULTS` (Step 5).
3. **Kiosk routing** — `frontend/src/child/api.ts`: `KIOSK_FEATURE_FLAG_PATHS` (Step 6).

Plus the SQL↔module seed lock (unit suite, Step 7b): migration `<NNNN>` (Step 4) ↔ `<FLAG_UPPER>_DEFAULT`.

A red lock test means one of those three drifted — the failure message names the offending list and the disagreeing key. Trace it back to the step above.

**NOT covered by any test:** the parent UI (Step 8). The lock test re-derives the parent's URLs from the backend modules; it never parses `parent/api.ts` / `App.tsx` / `PlayFeaturesControls.tsx`. So a parent-UI omission is INVISIBLE to CI and only an operator hitting the parent app (or the iPad UAT) catches it. Treat Step 8 as a hard requirement, not an optional polish.

---

## Step 9: Mandatory verification (run, confirm green)

Run from the toybox project root. Backend lock + unit suite first:

```powershell
uv run pytest tests/integration/test_phase_k_feature_flag_lists_agree.py tests/unit/core/test_phase_k_feature_flags.py
```

Then lint/format/typecheck the backend (isort placement of the new import in `app.py` is ruff-enforced — let ruff tell you the slot):

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src
```

Then the frontend typecheck (catches a missing `KIOSK_FEATURE_FLAG_PATHS` entry as a `Record` key error) and the frontend unit tests (catches the `PlayFeaturesControls.test.tsx` count fixtures from Step 8c):

```powershell
cd frontend; npm run typecheck; npm run test; cd ..
```

Apply the migration to the dev DB if a backend will run (idempotent):

```powershell
uv run python -m toybox.db.migrate
```

All green → the flag is wired end-to-end AND operator-controllable. A green backend+typecheck but a missing Step 8 means the flag works for the kiosk/backend but the parent has no toggle — verify the toggle renders (iPad UAT or a `npm run dev` parent-app check) before declaring done.

---

## File-writing rule (BOM trap)

Write/edit every generated file (`.py`, `.sql`, `.ts`, `.tsx`, and this SKILL.md) with the **Write/Edit tools** (UTF-8, no BOM). Never use PowerShell `Set-Content -Encoding utf8` / `Out-File -Encoding utf8` — both prepend a BOM on Windows PowerShell 5.1, which breaks `feature_flags.ts` parsing and mojibakes its `§` header through cp1252. If forced to use .NET: `[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))`.

---

## Limitations

- **Boolean flags only.** Numeric/preset/list settings have a different shape and cohort — stop and ask.
- **No optimistic concurrency on settings.** If the operator wants version-gated writes, that's the activity-mutation pattern, not this one.
- **Parent UI (Step 8) is not CI-gated.** Green tests do not prove the operator can toggle the flag. The split between "the kiosk consumes the flag" (it now defaults-on per the backend default) and "the kiosk actually CHANGES behavior" is separate work — this skill wires the flag's plumbing, not the feature it gates. The actual kiosk/parent behavior that the flag turns on/off is the operator's feature code.
- Do NOT resurrect the three Phase-L-deleted flags (`play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`) as templates — use a surviving module (`clickable_words_enabled` / `jokes_enabled` are the canonical references).
