# Data model

> **Scope:** SQLite schema, indexes, slug derivation, file layout, dedup. Read this when adding/altering a column, writing a migration, or reasoning about FKs and constraints. Module map lives in [architecture.md](architecture.md); HTTP surface in [api.md](api.md).

## Storage

SQLite at `data/toybox.db`. Versioned migrations in `src/toybox/db/migrations/` applied on startup, **forward-only for v1** (no rollback path; restore from a backup file if needed).

**Connection pragmas applied at every connection open:**
- `PRAGMA journal_mode=WAL;`
- `PRAGMA synchronous=NORMAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`

Single-writer assumption: toybox runs as one uvicorn worker. Multi-worker deployments will corrupt SQLite under write load and are not supported.

**Partial-write protection:** photo uploads write the image first to a `data/images/.staging/` path, compute SHA-256, then insert the DB row referencing the final path; on success the file is moved into place atomically. On any failure the staging file is unlinked.

## Slug derivation

Entity IDs (`toys.id`, `personas.id`, `rooms.id`, `children.id`) are slugs derived from `display_name` via `python-slugify`:

```python
from slugify import slugify
slug = slugify(display_name, lowercase=True, separator="-",
               regex_pattern=r"[^a-z0-9\-]")
```

On collision with an existing non-archived row, append `-2`, `-3`, ... until unique. Empty / all-symbol display_names reject with `code=invalid_display_name`. Slug is computed server-side; client cannot supply it.

## Partial UNIQUE indexes

Image dedup is enforced via partial unique indexes (SQLite supports them; column-level `UNIQUE` does not take a predicate):

```sql
CREATE UNIQUE INDEX idx_toy_image_hash
  ON toys(image_hash)
  WHERE archived = 0;

CREATE UNIQUE INDEX idx_room_image_hash
  ON rooms(image_hash)
  WHERE image_hash IS NOT NULL;

CREATE UNIQUE INDEX idx_persona_avatar_hash
  ON personas(avatar_image_hash)
  WHERE source != 'library' AND avatar_image_hash IS NOT NULL;
```

Library personas (with their shipped avatars) never participate in dedup; user-uploaded persona avatars do. Archived toys don't block re-creating with the same image (a parent can re-add a previously-archived toy).

## Foreign-key cascade policy

All FKs default to `ON DELETE RESTRICT`. Hard deletion is not a v1 operation — archive/soft-delete patterns are used instead. Two exceptions:

```sql
feedback.activity_id    REFERENCES activities(id) ON DELETE CASCADE
activity_steps.activity_id REFERENCES activities(id) ON DELETE CASCADE
```

(If an activity row is ever hard-deleted in operator recovery, its steps and feedback go with it.) `transcripts.session_id` is `RESTRICT` — sessions are never hard-deleted; transcripts are wiped via the dedicated `DELETE /api/transcripts` path.

## `auth_tokens` table

Active session tokens persist to disk so backend restarts don't log out parent apps or unpair child kiosks.

| Column | Type | Notes |
|--------|------|-------|
| `token_hash` | TEXT PK | SHA-256 of the token string; raw token never stored |
| `scope` | TEXT | `parent` or `child` |
| `child_session_label` | TEXT | nullable; for child kiosks, a parent-set label |
| `created_at` | TEXT | |
| `expires_at` | TEXT | |
| `last_used_at` | TEXT | rolled forward on each use; sliding TTL |
| `revoked_at` | TEXT | nullable; soft-revoke instead of delete |

Lifespan startup deletes rows past `expires_at`. Revocation sets `revoked_at`; capability check rejects revoked tokens. Validation:

```python
def validate(token: str) -> Token | None:
    h = sha256(token).hexdigest()
    row = db.fetch_one("SELECT * FROM auth_tokens WHERE token_hash=?", h)
    if not row or row.revoked_at or row.expires_at < now():
        return None
    db.execute("UPDATE auth_tokens SET last_used_at=? WHERE token_hash=?", now(), h)
    return Token(**row)
```

## Tables

### `toys`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug, e.g. `mr-unicorn` |
| `display_name` | TEXT NOT NULL | "Mr. Unicorn" |
| `image_path` | TEXT NOT NULL | relative to `data/images/toys/`; UUID-named, not user-supplied |
| `image_hash` | TEXT NOT NULL | SHA-256 of image bytes; partial UNIQUE index (see above); dedup on re-upload |
| `type` | TEXT | `plush`, `vehicle`, `doll`, `figure`, `book`, `instrument`, `other` |
| `tags` | TEXT (JSON) | colors, sizes, themes |
| `persona_id` | TEXT | FK to `personas.id`, nullable |
| `archived` | INTEGER (0/1) | soft-delete |
| `created_at` | TEXT (ISO8601) | |
| `last_used_at` | TEXT | nullable |

### `personas`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug |
| `display_name` | TEXT NOT NULL | "Marvelous the Wizard" |
| `archetype` | TEXT | `princess`, `wizard`, `detective`, `periodic_table`, `custom` |
| `system_prompt` | TEXT NOT NULL | the persona's instruction text |
| `avatar_image_path` | TEXT | relative path |
| `avatar_image_hash` | TEXT | SHA-256 of avatar bytes; null for library personas (avatars copied from package); partial UNIQUE index for non-library (see above) |
| `behavior_tags` | TEXT (JSON) | `["curious","kind","loud"]` |
| `age_range_min` | INTEGER | inclusive |
| `age_range_max` | INTEGER | inclusive |
| `language` | TEXT | BCP-47 tag, default `en`; reserved for v1.5 multi-language libraries |
| `source` | TEXT | `library`, `ai_generated`, `manual` |
| `default_voice_tone` | TEXT | reserved for v2 TTS |
| `created_at` | TEXT | |

Library-source personas cannot be deleted (only edited or hidden).

### `children`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug |
| `display_name` | TEXT NOT NULL | |
| `birthdate` | TEXT (YYYY-MM-DD) | derives age |
| `pronouns` | TEXT | |
| `reading_level` | TEXT | `none`, `sounds_out`, `fluent` |
| `interests` | TEXT (JSON) | free tags |
| `comfort` | TEXT | `loud_ok`, `prefers_quiet`, `mixed` |
| `banned_themes` | TEXT (JSON) | parent-set, e.g. `["monsters","violence"]` |
| `notes` | TEXT | |

### `rooms`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug |
| `display_name` | TEXT | "Living Room" |
| `image_path` | TEXT | nullable; UUID-named, server-generated |
| `image_hash` | TEXT | SHA-256 of bytes; partial UNIQUE index when not null (see above); dedup on re-upload |
| `notes` | TEXT | |

### `room_features`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `room_id` | TEXT NOT NULL | FK |
| `name` | TEXT | "couch", "toy chest" |
| `tags` | TEXT (JSON) | |

`UNIQUE(room_id, name)` prevents duplicate "couch" features on the same room. Names are lowercased + trimmed before insert.

### `activities`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `session_id` | TEXT NOT NULL | FK |
| `state` | TEXT | `proposed`, `approved`, `running`, `paused`, `ended`, `dismissed` |
| `version` | INTEGER NOT NULL | optimistic concurrency; incremented on every state change |
| `summary` | TEXT | "Hide-and-seek with Mr. Unicorn" |
| `persona_id` | TEXT | FK |
| `child_ids` | TEXT (JSON) | array of FKs |
| `room_ids` | TEXT (JSON) | optional |
| `toy_ids` | TEXT (JSON) | optional |
| `intent_source` | TEXT | `parent_manual`, `curated_nlp`, `claude_periodic`, `claude_per_utterance` |
| `created_at` | TEXT | |
| `started_at` | TEXT | nullable |
| `ended_at` | TEXT | nullable |

Mutating activity endpoints accept an `If-Match-Version` header; mismatched versions return HTTP 409 with the current state. Prevents two parent tabs from racing approve/dismiss.

**Header format:** `If-Match-Version: <decimal-integer>` (e.g. `If-Match-Version: 5`). No quoting, no `W/` prefix. Custom name (vs standard `If-Match` + ETag) chosen because the version is already an integer column on the row — no opaque ETag derivation, no weak/strong distinction to reason about. Server returns the current `version` in the 409 body so the client can refetch and retry.

### `activity_steps`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `activity_id` | TEXT NOT NULL | FK |
| `seq` | INTEGER NOT NULL | 1-indexed |
| `body` | TEXT NOT NULL | rendered on the child app |
| `sfx` | TEXT | sfx tag, e.g. `transition`, `success` |
| `expected_action` | TEXT | parent coaching hint, not shown to child |
| `current` | INTEGER (0/1) | one row true at a time per activity |

### `sessions`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `started_at` | TEXT | |
| `ended_at` | TEXT | nullable |
| `mode` | INTEGER | listening mode at session start |
| `mic_id` | TEXT | "home" v1 |

### `transcripts`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `session_id` | TEXT NOT NULL | FK |
| `mic_id` | TEXT | "home" v1 |
| `started_at` | TEXT | |
| `ended_at` | TEXT | |
| `text` | TEXT | |
| `confidence` | REAL | faster-whisper avg log prob |
| `triggered_intent` | TEXT | nullable; intent slug if curated NLP fired |

### `feedback`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `activity_id` | TEXT NOT NULL | FK |
| `step_seq` | INTEGER | nullable; null = whole activity |
| `kind` | TEXT | `didnt_work`, `loved_it`, `dismissed_pre_approval` |
| `signature` | TEXT NOT NULL | anti-signal match key: `{template_id}:{slot_fingerprint}` (sha256 of sorted slot key=value pairs) |
| `reason` | TEXT | parent's free text |
| `created_at` | TEXT | |

Anti-signal blocking: offline generator computes the candidate's `signature` before emitting and looks up `feedback.signature` matches with `kind='didnt_work'`; on hit, picks an alternative template or different slot fills. Same `signature` + `kind='loved_it'` boosts ranking instead.

### `settings` (key-value)
| Column | Type | Notes |
|--------|------|-------|
| `key` | TEXT PK | see keys below |
| `value` | TEXT | |

Settings keys:

| Key | Type | Notes |
|-----|------|-------|
| `listening_mode` | int (1–5) | default 3 |
| `parent_pin_hash` | argon2id encoded string | `$argon2id$v=19$m=65536,t=3,p=4$...`; PIN never stored plain |
| `parent_pin_set_at` | ISO8601 | for audit + reset window |
| `claude_call_min_interval_sec` | int | mode 5 throttle, default 30 |
| `claude_spontaneous_interval_sec` | int | mode 4 cadence, default 300 |
| `vad_aggressiveness` | int (0–3) | silero-vad threshold, default 2 |
| `log_level` | string | `DEBUG`/`INFO`/`WARNING`, default `INFO` |
| `mic_enabled` | bool | parent UI mute toggle (separate from mode) |
| `time_of_day_aware` | bool | inject hour-of-day into activity generator context, default true |

## File layout

```
data/
├── toybox.db                       # SQLite
├── images/
│   ├── toys/<toy-slug>.<ext>
│   ├── personas/<persona-slug>.<ext>
│   └── rooms/<room-slug>.<ext>
└── audio/                          # ephemeral; never persists across runs
```

## Deduplication / corruption protection

- Toy/persona/room/child IDs are slugs derived from `display_name`. Insert-conflict surfaces "this name exists, edit the existing one?"
- All non-slug IDs are UUIDs; append-only.
- DB writes wrapped in transactions per request handler.
- `transcripts` is the only large-growing table. Vacuum trigger when row count > 100k or DB size > 100 MB.
- **Backups: v1 has none.** Nightly snapshot to `data/backups/toybox-YYYY-MM-DD.db` with 14-day retention is v1.5 scope. Operator recovery procedures (M5) reflect this — DB corruption in v1 means data loss; the only mitigation is manual ad-hoc copies of `data/toybox.db` while the backend is stopped. This is acceptable for adult-only v1 testing where toy/persona/room data is small and re-enterable.
