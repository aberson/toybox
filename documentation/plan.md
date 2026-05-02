# toybox

AI assistant for play with children. Passive-listening home device that watches for play opportunities, suggests activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

## What This Is

A local-first family-private system that:

1. **Listens** to ambient audio in the play area (single mic on the home machine).
2. **Detects** play opportunities through a curated NLP layer with an optional Claude escalation path.
3. **Proposes** structured activities (linear scripts) to the parent.
4. **Runs** approved activities on a child-facing kiosk app — persona avatar, step cards, sound effects.
5. **Learns** from "this didn't work" parent feedback to avoid recurring flop patterns.

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth and the system degrades to a fully-offline mode without it.

**v1 ship point: end of Phase A** — the closed-loop demo with a manual "trigger" button instead of a real mic. v1 testing is **adult-only** (the user and their spouse) before children participate.

## Stack

| Layer | Tool | Why |
|-------|------|-----|
| Backend | Python 3.12 + FastAPI | dev/ standard; async-native; ws built-in |
| ASR | faster-whisper (`small`) | local STT; GPU when available, CPU fallback |
| VAD | silero-vad (ONNX) | gates STT on detected speech only; ~1 MB model, runs on CPU |
| AI | Claude (subscription OAuth) | per `claude-oauth-auth`; capability-gated for offline mode |
| Curated NLP | Python regex + intent registry | fast, deterministic, offline-capable |
| DB | SQLite (WAL mode) | local, file-based, family-private; single-writer |
| Password hashing | argon2-cffi (argon2id) | parent PIN hashing |
| Mic capture | sounddevice | cross-platform, low-latency, callback-based (bridged to asyncio via thread-safe queue) |
| Image decoding | Pillow + pillow-heif | JPEG/PNG/WebP via Pillow; iPhone HEIC via pillow-heif |
| Slugify | python-slugify | deterministic slug derivation from `display_name` for entity IDs |
| Frontend | React + TypeScript + Vite | dev/ standard; single project, two routes (`/parent`, `/child`) |
| Frontend state | Zustand | minimal boilerplate |
| Real-time | WebSockets (FastAPI) | bidirectional parent ↔ backend ↔ child |
| Type sync | pydantic-to-typescript | codegen TS types from Pydantic models on backend changes |
| Tests | pytest + Playwright | unit + integration + UI smoke |
| Lint/format | ruff (line-length=100) | dev/ standard |
| Type check | mypy strict | dev/ standard |
| Package mgmt | uv | dev/ standard |

Vite config pins `server.port: 3000, strictPort: true` (per dev/ memory `feedback_vite_dev_port`); proxies `/api` and `/ws` to backend at `:8000` in dev.

**Process model:** single uvicorn worker. SQLite + multi-worker leads to silent corruption under contention; the listening loop, AI calls, and mic capture all live in one async process anyway.

## Data Store

SQLite at `data/toybox.db`. Versioned migrations in `src/toybox/db/migrations/` applied on startup, **forward-only for v1** (no rollback path; restore from a backup file if needed).

**Connection pragmas applied at every connection open:**
- `PRAGMA journal_mode=WAL;`
- `PRAGMA synchronous=NORMAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`

Single-writer assumption: toybox runs as one uvicorn worker. Multi-worker deployments will corrupt SQLite under write load and are not supported.

**Partial-write protection:** photo uploads write the image first to a `data/images/.staging/` path, compute SHA-256, then insert the DB row referencing the final path; on success the file is moved into place atomically. On any failure the staging file is unlinked.

### Slug derivation

Entity IDs (`toys.id`, `personas.id`, `rooms.id`, `children.id`) are slugs derived from `display_name` via `python-slugify`:

```python
from slugify import slugify
slug = slugify(display_name, lowercase=True, separator="-",
               regex_pattern=r"[^a-z0-9\-]")
```

On collision with an existing non-archived row, append `-2`, `-3`, ... until unique. Empty / all-symbol display_names reject with `code=invalid_display_name`. Slug is computed server-side; client cannot supply it.

### Partial UNIQUE indexes

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

### Foreign-key cascade policy

All FKs default to `ON DELETE RESTRICT`. Hard deletion is not a v1 operation — archive/soft-delete patterns are used instead. Two exceptions:

```sql
feedback.activity_id    REFERENCES activities(id) ON DELETE CASCADE
activity_steps.activity_id REFERENCES activities(id) ON DELETE CASCADE
```

(If an activity row is ever hard-deleted in operator recovery, its steps and feedback go with it.) `transcripts.session_id` is `RESTRICT` — sessions are never hard-deleted; transcripts are wiped via the dedicated `DELETE /api/transcripts` path.

### `auth_tokens` table

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

### Tables

#### `toys`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug, e.g. `mr-unicorn` |
| `display_name` | TEXT NOT NULL | "Mr. Unicorn" |
| `image_path` | TEXT NOT NULL | relative to `data/images/toys/`; UUID-named, not user-supplied |
| `image_hash` | TEXT NOT NULL | SHA-256 of image bytes; partial UNIQUE index (see below); dedup on re-upload |
| `type` | TEXT | `plush`, `vehicle`, `doll`, `figure`, `book`, `instrument`, `other` |
| `tags` | TEXT (JSON) | colors, sizes, themes |
| `persona_id` | TEXT | FK to `personas.id`, nullable |
| `archived` | INTEGER (0/1) | soft-delete |
| `created_at` | TEXT (ISO8601) | |
| `last_used_at` | TEXT | nullable |

#### `personas`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug |
| `display_name` | TEXT NOT NULL | "Marvelous the Wizard" |
| `archetype` | TEXT | `princess`, `wizard`, `detective`, `periodic_table`, `custom` |
| `system_prompt` | TEXT NOT NULL | the persona's instruction text |
| `avatar_image_path` | TEXT | relative path |
| `avatar_image_hash` | TEXT | SHA-256 of avatar bytes; null for library personas (avatars copied from package); partial UNIQUE index for non-library (see below) |
| `behavior_tags` | TEXT (JSON) | `["curious","kind","loud"]` |
| `age_range_min` | INTEGER | inclusive |
| `age_range_max` | INTEGER | inclusive |
| `language` | TEXT | BCP-47 tag, default `en`; reserved for v1.5 multi-language libraries |
| `source` | TEXT | `library`, `ai_generated`, `manual` |
| `default_voice_tone` | TEXT | reserved for v2 TTS |
| `created_at` | TEXT | |

Library-source personas cannot be deleted (only edited or hidden).

#### `children`
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

#### `rooms`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug |
| `display_name` | TEXT | "Living Room" |
| `image_path` | TEXT | nullable; UUID-named, server-generated |
| `image_hash` | TEXT | SHA-256 of bytes; partial UNIQUE index when not null (see below); dedup on re-upload |
| `notes` | TEXT | |

#### `room_features`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `room_id` | TEXT NOT NULL | FK |
| `name` | TEXT | "couch", "toy chest" |
| `tags` | TEXT (JSON) | |

`UNIQUE(room_id, name)` prevents duplicate "couch" features on the same room. Names are lowercased + trimmed before insert.

#### `activities`
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

#### `activity_steps`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `activity_id` | TEXT NOT NULL | FK |
| `seq` | INTEGER NOT NULL | 1-indexed |
| `body` | TEXT NOT NULL | rendered on the child app |
| `sfx` | TEXT | sfx tag, e.g. `transition`, `success` |
| `expected_action` | TEXT | parent coaching hint, not shown to child |
| `current` | INTEGER (0/1) | one row true at a time per activity |

#### `sessions`
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `started_at` | TEXT | |
| `ended_at` | TEXT | nullable |
| `mode` | INTEGER | listening mode at session start |
| `mic_id` | TEXT | "home" v1 |

#### `transcripts`
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

#### `feedback`
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

#### `settings` (key-value)
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

### File layout

```
data/
├── toybox.db                       # SQLite
├── images/
│   ├── toys/<toy-slug>.<ext>
│   ├── personas/<persona-slug>.<ext>
│   └── rooms/<room-slug>.<ext>
└── audio/                          # ephemeral; never persists across runs
```

### Deduplication / corruption protection

- Toy/persona/room/child IDs are slugs derived from `display_name`. Insert-conflict surfaces "this name exists, edit the existing one?"
- All non-slug IDs are UUIDs; append-only.
- DB writes wrapped in transactions per request handler.
- `transcripts` is the only large-growing table. Vacuum trigger when row count > 100k or DB size > 100 MB.
- **Backups: v1 has none.** Nightly snapshot to `data/backups/toybox-YYYY-MM-DD.db` with 14-day retention is v1.5 scope. Operator recovery procedures (M5) reflect this — DB corruption in v1 means data loss; the only mitigation is manual ad-hoc copies of `data/toybox.db` while the backend is stopped. This is acceptable for adult-only v1 testing where toy/persona/room data is small and re-enterable.

## Listening Modes

Parent app surfaces a 5-position slider. Mode dictates whether Claude is invoked and at what cadence.

| Mode | Name | Curated NLP | Claude calls |
|------|------|-------------|--------------|
| 5 | Intense | always | every utterance, throttled by `claude_call_min_interval_sec` |
| 4 | High | always | curated triggers + spontaneous every N min (default 5) |
| 3 | Default | always | only on curated triggers |
| 2 | Low | always | only on parent-tap "what should we do?" |
| 1 | Offline | always | never (also engaged automatically when token missing/expired) |

Default = **mode 3** on first run. Mode is stored in `settings`, emitted on the `mode` ws topic so both apps reflect changes instantly.

The capability gate `ai.client.is_capable()` returns `False` when:
- mode == 1
- OAuth token missing
- OAuth token expired and refresh fails
- last N Claude calls returned network errors (circuit breaker open)

Every AI call site reads this; on `False` the call falls back to the offline path or no-ops cleanly.

The gate also publishes a `capability_reason` so the parent UI can distinguish *why* it's offline:

| Reason | Meaning | UI banner |
|--------|---------|-----------|
| `config_mode_1` | Listening mode = 1 by parent choice | none (intentional) |
| `token_missing` | No OAuth file at startup | "Set up Claude in Settings" |
| `token_expired` | Refresh failed or token revoked | "Re-authenticate Claude" |
| `network_error` | Circuit breaker open after N consecutive fails | "Can't reach Claude — retrying" |
| `rate_limited` | Anthropic returned 429 | "Slowing down for a few minutes" |

`capability_reason` is emitted on the `system` ws topic on every flip.

## What Leaves the House

Privacy posture for a passively-listening home device used around children.

| Data | Stays local | Sent outbound | Notes |
|------|-------------|---------------|-------|
| Microphone audio | ✅ | ❌ | Ephemeral ring buffer, never written to disk, never sent anywhere |
| Transcripts (text) | ✅ | ❌ | SQLite-only; PIN-gated wipe button |
| Toy / room photos | ✅ (always) | ⚠️ when ingest with vision enabled | Sent to Claude's vision endpoint over OAuth; only at ingest time, never replayed |
| Child profile (name, age, interests) | ✅ (always) | ⚠️ as Claude prompt context (modes 3+) | Sent on activity-generation Claude calls. Free-text fields can be redacted by parent |
| Trigger phrase that fired | ✅ | ⚠️ as Claude prompt context (modes 3+) | Last 60 sec of transcript context |
| Listening mode + curated NLP triggers | ✅ | ❌ | |
| Feedback / anti-signal | ✅ | ⚠️ as Claude prompt addendum | "things this family doesn't like" string |
| OAuth token | local file | ❌ (refresh round-trip excluded) | `~/.toybox/secrets.json` |

**Anthropic-side retention:** governed by Anthropic's published policy at the time of use. Toybox cannot wipe data already received by Anthropic. If retention is a concern, run in mode 1 (offline) — no data leaves the house.

**Mode 1 guarantee:** when `listening_mode=1`, no outbound network calls except OAuth refresh (which can also be disabled by removing `secrets.json`). Pure local operation.

**LAN exposure:** the backend defaults to `127.0.0.1` (loopback-only) so v1 — which has no PIN — is not reachable from the LAN at all. The child kiosk tablet path requires the parent PIN to be set (Phase D step 20); only then does the LAN-binding guard allow `TOYBOX_HOST=0.0.0.0`. See "LAN binding guard" under WebSocket auth.

## Claude OAuth Lifecycle

Token managed via the `claude-oauth-auth` subscription flow; storage at `~/.toybox/secrets.json` (Windows: `%USERPROFILE%\.toybox\secrets.json`). On Windows, ACL inheritance is sufficient for single-user home machines; on POSIX, file mode is forced to `0600` at write time.

```
┌────────────┐    on startup     ┌──────────────┐
│ load token │──────────────────►│ check expiry │
└────────────┘                   └──────┬───────┘
                                        │
                  ┌─────────────────────┼────────────────────────┐
                  │                     │                        │
              valid (>5 min)        expiring soon            expired
                  │                  (<5 min)                    │
                  ▼                     ▼                        ▼
          ┌────────────┐        ┌──────────────┐         ┌──────────────┐
          │ is_capable │        │ refresh in   │         │ refresh now  │
          │ = True     │        │ background   │         │ (sync, once) │
          └────────────┘        └──────────────┘         └──────┬───────┘
                                                                │
                                                       success ─┴─ fail
                                                          │       │
                                                          ▼       ▼
                                                   re-arm timer  capability=False
                                                                 reason=token_expired
                                                                 emit system ws
```

- Background refresh task wakes every 60 sec; refreshes any token within 5 min of expiry.
- On Anthropic 401 mid-call, the call fails and the next gate read returns `False` with `reason=token_expired` until refresh succeeds.
- On three consecutive `network_error` outcomes, circuit breaker opens for 60 sec; `reason=network_error`.
- On `429`, breaker opens for the response's `retry-after` (default 120 sec); `reason=rate_limited`. Subsequent triggers route to the offline path.

Parent UI displays current `capability_reason` and an "Re-authenticate" button that re-runs the OAuth flow in a new window.

## Failure Modes & Error Policy

Every external call is wrapped to return a typed `Result[T, ToyboxError]` (using a small Result type, not exceptions across boundaries). Errors carry a stable `code` (string), `message` (user-safe), and optional `detail` (dev-only).

| Site | Failure | Surface |
|------|---------|---------|
| STT (`audio.stt.transcribe`) | model load fails on startup | fatal, exits with clear log; `--check` would have caught it |
| STT | mid-stream decode error | log + drop chunk; no user-visible message |
| Claude (`ai.client.call`) | network/timeout | circuit breaker counts; capability flips to `network_error` |
| Claude | 401 | mark token expired; trigger refresh; capability flips to `token_expired` |
| Claude | 429 | open breaker; capability flips to `rate_limited` |
| Claude | malformed JSON output | log; fall back to offline path for that one suggestion; emit `system` warning |
| Vision (`ai.toy_vision`) | timeout/error | suggestion fields empty; parent fills manually; toast in UI |
| Photo upload | size > limit / bad MIME / bad bytes | 415/413 with explicit code; UI shows the rule that failed |
| DB | constraint violation | 409 with `code=duplicate_*`; UI shows "this exists, view existing?" |
| ws | client disconnect | client auto-reconnect with state resync (full activity refetch on reconnect) |
| Persona library load | malformed JSON | log + skip that persona; startup continues; `--check` warns |
| Migration | apply failure | abort startup; preserve DB at original path; log the failed migration filename + traceback. v1 has no backup; operator must manually copy `data/toybox.db` aside, then either fix the migration source or factory-reset per M5. |

UI errors flow on the `system` ws topic with `{level: "error"|"warn"|"info", code, message, dismissable}`.

### Logging policy

- Logs go to stdout; structured JSON when not a TTY.
- **Transcript text never logged at `INFO` or higher** — only at `DEBUG`. The DEBUG handler is opt-in via `TOYBOX_LOG_LEVEL=DEBUG` and intended for development. A pre-commit hook (`tools/check_no_transcript_in_info.py`) greps for `log\.(info|warning|error).*transcript` and fails the commit on hit. A full ruff plugin was considered but is overkill for one rule; the grep hook is ~15 lines and covers the same ground.
- Children's names appear in transcripts and are PII; logging policy applies the same gate.
- API request bodies containing photos are never logged in full (size only).
- OAuth tokens never logged in any form.
- Failed PIN attempts logged at `WARNING` with attempt count, never the attempted value.

## Activity Loop

### State machine

```
[curated trigger | claude periodic | claude per-utterance | parent manual]
                         │
                         ▼
                    proposed ─── (parent dismiss) ──► dismissed
                         │
                  (parent approve)
                         │
                         ▼
                     running ◄──── (resume) ──── paused
                         │                          ▲
                         │                          │
                    (parent pause / skip / regenerate from here / end)
                         │
                         ▼
                       ended (terminal)
```

- One activity per session can be `running` or `paused` at a time. `proposed` activities can stack as a queue, **capped at 5** — older proposed activities are auto-dismissed when the cap is hit.
- **Default `child_ids` on proposal:** generator emits with `child_ids=[]`. The parent picks the kid(s) for this activity at approval time via a multi-select on the suggestion card (defaults to "all kids in the persona's age range"). Skip the picker if there's only one child profile.
- `regenerate from here` does NOT change activity id — replaces remaining `activity_steps` with a fresh generation seeded by the current step.
- `end` requires a yes/no confirm in the parent UI to prevent accidental shutdown.
- All mutating endpoints require `If-Match-Version` header to prevent concurrent-tab race conditions.

**Mic-hot indicator (parent UI):** persistent visual element in the parent app header showing live mic status — green dot + "listening" when capture is active, red dot + "muted" when `mic_enabled=false`, grey + "offline" when capture daemon is down. Independent of mode (mode 1 still listens, just doesn't escalate to Claude). Trust + consent posture for a passively-listening device used around children.

**Time-of-day awareness:** if `time_of_day_aware=true` (default), the activity generator receives the current local hour as context. Offline templates can route on it (`hour < 9` → "morning_quiet"; `19 ≤ hour < 21` → "wind_down"); Claude prompt includes "It is currently 7:42 PM on a Tuesday — bias toward calmer activities."

### Step shape

```json
{
  "seq": 3,
  "body": "Mr. Unicorn whispers a secret — only the kid can find the next clue, hidden somewhere in the kitchen.",
  "sfx": "transition",
  "expected_action": "kid runs to kitchen and looks for next clue"
}
```

`body` is rendered on the child app. `expected_action` is parent-only — shown in the parent's live activity panel as a coaching hint.

### Linear template generator (offline path)

Templates in `src/toybox/personas/templates/<archetype>/<intent>.json`. Each is a 5-step script with slot placeholders:

```json
{
  "name": "hide_and_seek_with_persona",
  "intents": ["request_play", "boredom"],
  "steps": [
    "{persona_intro}",
    "{persona} hides somewhere in {room}!",
    "Find {persona} — {hint_a}",
    "{persona} says good job — but they hid one more thing! {hint_b}",
    "{persona_outro}"
  ]
}
```

Slots fill deterministically from intent slot + child profile + persona + available toys/rooms. Hash-based seeding for reproducibility.

> **NOTE (Group 2 refinement):** child screen content (avatar + step text + sfx) is the v1 default. Pre-recorded persona voice clips and richer per-step assets are tracked as a Phase D+ refinement. Live parent controls (pause/skip/regenerate/end/"didn't work") are the agreed v1 set; richer mid-activity authoring is a future refinement.

### Claude path (modes 3+)

Single Claude call with structured-output schema:
- **Inputs:** child profile, available toys, available rooms, persona library, last 60 sec of transcript context, listening mode, anti-signal feedback, current local hour (if `time_of_day_aware`).
- **Output:** a 5-step activity matching the schema above, plus `summary` and `intent_source`.
- **Caching:** prompt-cache the persona/toy/room context per session.
- **Rate-limit handling:** on 429, breaker opens for `retry-after`; queued triggers route to the offline path; no retries hammered.
- **Output validation:** strict Pydantic schema; malformed output → fall back to offline template, log warning to `system` ws topic.

## Curated NLP Triggers

Initial trigger registry (extensible JSON; lives in `src/toybox/nlp/triggers.json`):

| Pattern | Intent | Slot |
|---------|--------|------|
| `(?i)let'?s play\s+(.+)` | `request_play` | match group |
| `(?i)i (want\|wanna) (to )?play\s+(.+)` | `request_play` | last group |
| `(?i)play (.+) with me` | `request_play` | match group |
| `(?i)i'?m bored` | `boredom` | — |
| `(?i)i (don'?t know\|dunno) what to do` | `boredom` | — |
| `(?i)what (should\|can) we do` | `solicit` | — |
| `(?i)\b(<toy_name>)\b` | `mention_toy` | match (only if known toy) |
| `(?i)hide\s*(and\|&)?\s*seek` | `request_game` | `hide_and_seek` |
| `(?i)treasure hunt` | `request_game` | `treasure_hunt` |
| `(?i)(unicorn\|princess\|wizard\|detective\|monster\|dragon\|robot)` | `theme_mention` | match |

`mention_toy` triggers compile dynamically from the `toys` table on startup and refresh when toys are added/removed.

**Registry user-edit path:** the shipped `triggers.json` lives in `src/`, but on first run it is copied to `data/triggers.json`. The runtime reads `data/triggers.json` (parent-editable through the parent UI's "advanced" pane); the shipped file is only used to seed/repair. This way package upgrades don't clobber user edits, and the user can extend or remove patterns without touching the codebase.

Each pattern entry carries a `version` field. Loader supports schema evolution: missing fields fall back to defaults from the shipped registry.

## Ingestion Paths

### Toy ingestion
1. Parent uploads photo via `POST /api/toys/photo`.
2. Backend validates upload (see Upload validation rules below); rejects on failure with explicit code.
3. Backend computes SHA-256 of bytes; if it matches `toys.image_hash` of an existing non-archived row, returns 409 with the existing toy (UI shows "this image already exists, view existing toy?").
4. Otherwise writes file to `data/images/toys/<uuid>.<ext>` (filename is server-generated, never user-supplied), calls `ai.toy_vision(image)`.
5. Claude returns suggested fields: `display_name`, `type`, `tags`, optional `suggested_persona_archetype`.
6. Parent confirms / edits in the parent UI.
7. Row inserted with `image_hash` set; `mention_toy` trigger registry refreshes.

Offline mode: step 4's vision call skipped, parent fills all fields manually.

### Upload validation rules (apply to all photo endpoints)

| Rule | Limit | Error code |
|------|-------|-----------|
| Max file size | 10 MB per file | `upload_too_large` |
| Max files per bulk request | 50 | `upload_too_many` |
| Allowed MIME (sniffed, not extension-trusted) | `image/jpeg`, `image/png`, `image/webp`, `image/heic` | `upload_bad_mime` |
| Min dimensions | 200 × 200 | `upload_too_small` |
| Max dimensions | 8000 × 8000 (downscaled to ≤1600 on long edge before vision call) | accepted, downscaled |
| Filename | server-generated UUID; user filename is discarded | n/a |
| Storage | `data/images/.staging/<uuid>` first, atomic rename to `data/images/toys/<uuid>.<ext>` on success | n/a |
| Static serving | only files within `data/images/{toys,personas,rooms}/` are reachable; other paths 404 | n/a |
| Decoder hardening | bytes pass `Image.open(...).verify()` in a try/except before any further use; malformed images rejected with `upload_decode_failed`; Pillow + pillow-heif pinned to ≥ latest CVE-fixed versions; Dependabot/renovate watches them | `upload_decode_failed` |

### Room ingestion (bulk)
1. Parent drops a folder of photos onto `POST /api/rooms/photos/bulk`.
2. Backend validates (per Upload validation rules) and dedups (room photo hashes tracked similarly).
3. Backend stores each photo, calls `ai.house_vision(image)` per photo.
4. Vision returns `room_label` ("Living Room"), `features` (`["couch", "toy chest", "fireplace"]`).
5. Parent reviews each in a tabbed UI: confirm room name, edit/add/delete features.
6. Rows inserted into `rooms` and `room_features`.

UI guidance: "Real estate listing photos work well — clean, labeled by room, full coverage of the house."

**v1.5 scope:** paste a Redfin/Zillow URL → fetch photos automatically. Out of v1 due to TOS uncertainty around scraping; manual save-then-upload is the supported path.

### Child profile
Plain CRUD form; no AI assist.

## Modules

### `src/toybox/main.py`
FastAPI entrypoint. Mounts the React build for production; in dev, frontend runs on Vite at `:3000` and proxies to backend at `:8000`. Mic-capture loop runs as an asyncio background task started during app lifespan.

**Lifespan & shutdown:** `@asynccontextmanager` lifespan starts (1) DB migrations, (2) persona loader, (3) Claude OAuth client + refresh task, (4) STT/VAD model load, (5) mic capture, (6) ws subscriber pruner. On shutdown (SIGINT or uvicorn graceful), an `asyncio.Event` is set; each task observes it, drains its in-flight work (≤5 sec), and exits. After 5 sec, remaining tasks are cancelled. Then connections close. Order is reverse of startup.

**`--check` mode:** `python -m toybox.main --check` runs lifespan startup, prints a status table (db ok, models loaded, claude capable, mic detected), and exits 0/1. No network listeners.

**`--smoke` mode:** `python -m toybox.main --smoke` runs lifespan startup, plays a synthetic WAV through the audio pipeline (`tests/fixtures/audio/lets_play_unicorns.wav`), asserts that a suggestion is generated, exits 0/1. CI smoke without Playwright.

**Tools:** `python -m toybox.tools.gc_images` removes orphan images; `python -m toybox.tools.lint_templates` validates that all `{slot}` placeholders in `personas/templates/` correspond to known slot generators.

### `src/toybox/api/`
- `activities.py` — REST: suggest/approve/dismiss/advance/pause/resume/regenerate/end/feedback.
- `toys.py` — REST: CRUD + photo upload.
- `personas.py` — REST: list/create/edit/delete (delete blocked for `source=library`).
- `house.py` — REST: rooms + features + bulk photo upload.
- `children.py` — REST: CRUD.
- `transcripts.py` — REST: list/search/delete/wipe.
- `settings.py` — REST: mode get/set; PIN setup; Claude OAuth status; mic mute toggle.
- `metrics.py` — REST `/api/metrics` + in-memory counter aggregation.
- `ws.py` — WebSocket router with topic subscription; envelope shape; topics: `transcripts.live`, `activity.state`, `mode`, `system`, `metrics`.

### `src/toybox/audio/`
- `capture.py` — sounddevice mic loop, ring buffer (~2 min, 16 kHz mono int16). PortAudio invokes the mic callback in its own native thread; the callback drops samples into a thread-safe `queue.Queue(maxsize=TOYBOX_MIC_QUEUE_BOUND)` (default 100). On overflow, drop-oldest with a metrics event (`code=mic_queue_overflow`). An asyncio task (`loop.run_in_executor(None, q.get)` in a polling loop) pumps samples to the async pipeline. Cooperative shutdown via an `asyncio.Event`; mic stream stops then queue drains.
- `vad.py` — silero-vad ONNX gate; only emits chunks containing sustained speech (≥`vad_min_speech_ms`). Inference is fast (<1 ms), runs on the asyncio thread.
- `stt.py` — faster-whisper wrapper, GPU autodetect, model load on startup, `transcribe(chunk) → text + confidence + timestamps`. Runs in `asyncio.to_thread`.

### `src/toybox/nlp/`
- `triggers.py` — loads `triggers.json`, exposes `match(text) → list[Intent]`. Subscribes to `core/state.py` topic `triggers.invalidate` and rebuilds compiled patterns when toys/personas change. No polling.
- `intents.py` — `Intent` dataclass, slot extraction.

### `src/toybox/ai/`
- `client.py` — Claude OAuth client; capability gate; offline-safe wrappers.
- `activity_gen.py` — Claude path for activity generation.
- `toy_vision.py` — Claude vision for toy cataloging.
- `house_vision.py` — Claude vision for room/feature extraction.

### `src/toybox/core/`
- `activities.py` — state machine, offline template generator, regenerate-from-here logic.
- `modes.py` — listening mode persistence + capability composition.
- `state.py` — in-memory pub/sub for ws topics + capability flips. Interface:
  ```python
  async def publish(topic: str, payload: dict) -> None     # never blocks; drops on overflow
  def subscribe(topics: list[str]) -> AsyncIterator[Envelope]   # bounded queue per subscriber
  def unsubscribe(token: SubscribeToken) -> None
  def current(topic: str) -> dict | None                         # last-seen payload, for resync
  ```
  **Backpressure policy (uniform across external ws subscribers and internal subscribers):** every subscriber owns a single `asyncio.Queue(maxsize=TOYBOX_WS_QUEUE_BOUND)`. `publish` calls `queue.put_nowait` and on `QueueFull` drops the oldest message and emits a `system` notice (`code=ws_backpressure_drop`, includes topic name). Publishers never block — the mic loop, OAuth refresh task, and toy-add handler all stay responsive when a slow subscriber falls behind.

  Internal topic for cross-module signals: `triggers.invalidate` — published by `api/toys.py` on toy add/archive; subscribed by `nlp/triggers.py` to rebuild compiled patterns. Subscriber rebuilds asynchronously; if rebuild is in progress when a second invalidate arrives, the queued event coalesces (deduped) so a burst of toy uploads triggers at most one extra rebuild.
- `feedback.py` — anti-signal storage and lookup for the activity generator.
- `errors.py` — central registry. `class ErrorCode(StrEnum)` lists every code referenced in the plan (`upload_too_large`, `upload_bad_mime`, `duplicate_image`, `invalid_display_name`, `version_conflict`, `ws_backpressure_drop`, `mic_queue_overflow`, `pin_locked`, `token_invalid`, …). The `pydantic-to-typescript` codegen also emits `frontend/src/shared/errors.ts` from this enum so server and client share one source of truth.

### `src/toybox/db/`
- `connection.py` — connection factory, transaction context manager.
- `models.py` — Pydantic models matching schema.
- `migrations/` — `0001_initial.sql`, `0002_*.sql`, …
- `migrate.py` — applies pending migrations on startup.

### `src/toybox/personas/`
- `library/<archetype>.json` — Princess, Wizard, Detective, Periodic Table Professor.
- `library/avatars/<archetype>.png` — **shipped with the package**. Four CC0 / commissioned PNGs, 512×512, transparent background. Sourced via [openclipart.org](https://openclipart.org) (CC0) or commissioned from a freelance illustrator before Phase A step 3 starts. Listed in `library/_credits.md`.
- `library/_schema.json` — JSON Schema for persona files; loader validates against it on startup.
- `templates/<archetype>/<intent>.json` — offline activity templates per archetype × intent.
- `loader.py` — idempotent startup loader; copies avatars from `library/avatars/` into `data/images/personas/` on first run so they're served from the same path as user-created personas.

### `src/toybox/config.py`
Pydantic Settings: paths, model size/device, port, OAuth path, Claude min-interval, default mode.

### `frontend/src/parent/`
Parent app — settings page, listening mode slider, suggestion queue, live activity panel, content management (toys/personas/rooms/kids/transcripts), PIN gate (Phase D).

### `frontend/src/child/`
Child app — kiosk mode: persona avatar, current step card, sfx player, "next step" button.

### `frontend/src/shared/`
- `api.ts` — REST client.
- `ws.ts` — WebSocket client + topic subscription.
- `types.ts` — TypeScript types matching Pydantic models.
- `components/` — shared UI primitives.

## API Route Contract

**Token transport:** all gated endpoints expect `Authorization: Bearer <token>`. No cookies (sidesteps CSRF on a LAN device). Pre-Phase-D, `/api/auth/parent` returns a token without PIN check — **don't expose toybox to a guest network or untrusted houseguests until Phase D step 20 lands**. The pre-Phase-D auth path is a transitional convenience, not a security boundary.

| Method | Route | Purpose | Body / Query | Response |
|--------|-------|---------|--------------|----------|
| GET | `/api/health` | liveness | — | `{ok, version, claude_capable, capability_reason, mode, mic_enabled}` |
| GET | `/api/metrics` | counters for tuning (parent token) | — | `{trigger_fires_by_intent, claude_calls_by_mode, suggestions_total, suggestions_approved, suggestions_dismissed, avg_stt_confidence, breaker_state, ws_subscribers, mic_device, mic_queue_depth, model_loaded, data_dir_bytes, disk_free_bytes, uptime_sec}` |
| POST | `/api/auth/parent` | issue parent token | `{pin?}` (pre-Phase D: empty body) | `{token, expires_at}` |
| POST | `/api/auth/child/pair` | pair a child kiosk (parent token required) | `{label?}` | `{token, expires_at}` |
| POST | `/api/auth/revoke` | revoke a token (parent token required) | `{token}` | `{ok}` |
| GET | `/api/settings/mode` | current mode | — | `{mode: 1-5}` |
| POST | `/api/settings/mode` | set mode | `{mode: 1-5}` | `{mode}` |
| GET | `/api/settings/pin/status` | PIN setup status | — | `{configured: bool}` |
| POST | `/api/settings/pin` | set/change PIN (gated) | `{old?, new}` | `{ok}` |
| GET | `/api/settings/claude` | OAuth status | — | `{capable, expires_at?}` |
| GET | `/api/toys` | list | `?archived=` | `[Toy]` |
| POST | `/api/toys/photo` | upload + propose | multipart | `{toy: Toy, ai_suggestions}` |
| PATCH | `/api/toys/{id}` | edit | partial Toy | `Toy` |
| DELETE | `/api/toys/{id}` | archive | — | `{ok}` |
| GET | `/api/personas` | list | — | `[Persona]` |
| POST | `/api/personas` | create | `Persona` | `Persona` |
| PATCH | `/api/personas/{id}` | edit | partial | `Persona` |
| DELETE | `/api/personas/{id}` | delete (non-library only) | — | `{ok}` |
| GET | `/api/rooms` | list with features | — | `[Room with features]` |
| POST | `/api/rooms/photos/bulk` | bulk upload + propose (per-file results, partial success allowed) | multipart (multi-file) | `{results: [{filename, status: "ok"\|"error", code?, message?, room?, ai_suggestions?}]}` |
| PATCH | `/api/rooms/{id}` | edit | partial | `Room` |
| GET | `/api/children` | list | — | `[Child]` |
| POST | `/api/children` | create | `Child` | `Child` |
| PATCH | `/api/children/{id}` | edit | partial | `Child` |
| DELETE | `/api/children/{id}` | delete | — | `{ok}` |
| POST | `/api/activities/suggest` | manual trigger (dev / parent solicit) | `{intent, slot?, child_ids?}` | `Activity` |
| POST | `/api/activities/{id}/approve` | start running with selected kid(s) | header: `If-Match-Version: N`; body `{child_ids: [...]}` (required if >1 child profile, else server fills) | `Activity` (409 on version mismatch) |
| POST | `/api/activities/{id}/dismiss` | discard pre-approval | header: `If-Match-Version: N` | `{ok}` (409 on mismatch) |
| POST | `/api/activities/{id}/advance` | next step | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/pause` | pause | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/resume` | resume | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/regenerate` | regenerate from current step | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/end` | end (with confirm) | header: `If-Match-Version: N`; body `{confirmed: true}` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/feedback` | "didn't work" / "loved it" | `{kind, step_seq?, reason?}` | `{ok}` |
| GET | `/api/transcripts` | list | `?since=&intent=` | `[Transcript]` |
| DELETE | `/api/transcripts/{id}` | delete one | — | `{ok}` |
| DELETE | `/api/transcripts` | wipe all (PIN-gated) | — | `{deleted: int}` |
| WS | `/ws` | bidirectional topics | subscribe by topic | streamed events |

### WebSocket envelope

All ws messages share a single envelope:

```json
{
  "topic": "activity.state",
  "type": "transition",
  "payload": { /* topic-specific shape */ },
  "ts": "2026-05-01T12:34:56.789Z"
}
```

#### WebSocket authentication

`/ws` requires a session token in the `Sec-WebSocket-Protocol` header (or `?token=` query string for browsers that don't allow custom subprotocols on tablets). Tokens are issued by:

| Endpoint | Recipient | Scope | Lifetime |
|----------|-----------|-------|----------|
| `POST /api/auth/parent` (body: `{pin}`) | parent app after PIN entry | all topics | 24 h sliding |
| `POST /api/auth/child/pair` (body: `{room?}`, parent token required) | child kiosk one-time pairing | `activity.state` for current session only | 30 days, revocable |

Topics by scope:

| Topic | Required scope |
|-------|---------------|
| `transcripts.live` | parent |
| `activity.state` | parent OR matching-session child |
| `mode` | parent OR child |
| `system` | parent (warns + errors) / child (errors only) |
| `metrics` | parent |

Tokens are random 32-byte hex strings; revocation is tracked via `auth_tokens.revoked_at` (single source of truth — no separate `revoked_tokens` table). Lifespan startup deletes rows past `expires_at`; capability check rejects rows with `revoked_at IS NOT NULL`. Pre-Phase D (no PIN), the auth endpoints accept any request and return a token — the gate exists structurally so Phase D step 20 only needs to add PIN verification, not retrofit auth. **For v1, this means the backend must bind loopback-only — see "LAN binding guard" below.**

#### Origin header check (defense-in-depth)

`/ws` upgrade and all `POST`/`PATCH`/`DELETE` REST handlers reject requests whose `Origin` header is not in the configured allow-list. Default allow-list:

- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://<TOYBOX_LAN_IP>:3000` if `TOYBOX_LAN_IP` env var is set (Phase D LAN-bind path only)

Mitigates DNS rebinding and cross-site websocket hijacking from a phishing tab on the same machine. `GET` requests skip the check (no state change).

#### LAN binding guard

The backend refuses to bind any non-loopback host unless `settings.parent_pin_hash` is set. Concretely: at startup, if `TOYBOX_HOST != 127.0.0.1` and `TOYBOX_HOST != localhost` and the PIN is unset, the process logs `code=lan_bind_requires_pin` and exits non-zero. This makes the security invariant a runtime check, not a documentation request — v1 (no PIN, no LAN binding) and post-Phase-D (PIN set, LAN binding allowed) are the only valid states.

#### Subscription messages

```json
{ "action": "subscribe", "topics": ["activity.state", "mode", "system"] }
{ "action": "unsubscribe", "topics": ["transcripts.live"] }
```

Server rejects subscriptions to out-of-scope topics with a `system` error message and disconnects after 3 violations.

#### Subscriber backpressure

Each subscriber has a single bounded outbound queue (default 100 messages **total across all subscribed topics**, not per-topic). On overflow, oldest messages drop and a `system` notice fires (`code=ws_backpressure_drop`). Mic loop and other publishers never block on slow subscribers.

#### Topics (server → client)

| Topic | Payload shape |
|-------|---------------|
| `transcripts.live` | `{transcript_id, session_id, text, confidence, started_at, ended_at, triggered_intent?}` |
| `activity.state` | full `Activity` DTO including `version` |
| `mode` | `{mode: 1-5}` |
| `system` | `{level: "error"\|"warn"\|"info", code, message, dismissable, capability_reason?}` |
| `metrics` | snapshot every 30 sec: same shape as `GET /api/metrics` |

## Project Structure

```
toybox/
├── pyproject.toml
├── README.md
├── AGENTS.md                       # references parent dev/AGENTS.md + project specifics
├── CLAUDE.md                       # @AGENTS.md
├── documentation/
│   ├── plan.md                     # this file
│   ├── architecture.md             # design decisions, deeper than plan
│   └── operator/                   # manual step procedures
│       ├── claude-oauth-setup.md
│       ├── mic-hardware-test.md
│       ├── play-session-template.md
│       ├── recovery.md             # DB reset, image cleanup, factory reset, OAuth rotate
│       └── troubleshooting.md      # common errors and fixes
├── src/toybox/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── audio/
│   ├── nlp/
│   ├── ai/
│   ├── core/
│   ├── db/
│   │   └── migrations/
│   ├── personas/
│   │   ├── library/
│   │   │   ├── _schema.json
│   │   │   ├── _credits.md
│   │   │   ├── avatars/
│   │   │   └── *.json
│   │   └── templates/
│   └── tools/
│       ├── gc_images.py
│       ├── lint_templates.py
│       ├── check_no_transcript_in_info.py   # pre-commit hook; grep-based
│       └── gen_error_codes_ts.py            # fallback if pydantic2ts can't emit StrEnum (per Phase A step 1 spike)
├── frontend/
│   ├── package.json
│   ├── vite.config.ts              # port 3000, strictPort
│   ├── tsconfig.json
│   ├── index.html
│   ├── public/
│   │   └── sfx/                    # M4 manual step
│   └── src/
│       ├── main.tsx                # routes /parent, /child
│       ├── parent/
│       ├── child/
│       └── shared/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── ui/                         # Playwright smoke
│   └── fixtures/
│       ├── audio/                  # bundled WAVs: silence, "lets_play_unicorns", multi-speaker
│       ├── photos/{toys,rooms}/    # CC0 sample photos
│       ├── claude/                 # canned Claude response JSONs (matched by stub)
│       └── README.md               # license + provenance per asset
├── data/                           # gitignored
│   ├── README.md                   # what's in here, what's safe to delete
│   ├── toybox.db
│   ├── images/{toys,personas,rooms}/
│   ├── images/.staging/            # in-flight uploads, atomic rename on success
│   ├── models/                     # whisper + silero-vad caches; gitignored
│   ├── triggers.json               # user-editable copy of trigger registry
│   └── audio/                      # ephemeral
├── .pre-commit-config.yaml
├── .gitignore
└── .claude/
    └── settings.local.json
```

`data/README.md` documents:
- `toybox.db` — primary DB; safe to back up; do not concurrent-write
- `images/{toys,personas,rooms}/` — UUID-named files, referenced by `*.image_path` in DB; orphan files cleaned by `python -m toybox.tools.gc_images`
- `images/.staging/` — in-flight uploads; safe to wipe when backend is stopped
- `models/` — downloaded ML models; safe to wipe (re-downloads on next run)
- `triggers.json` — user-editable trigger registry; deleting reverts to shipped defaults
- `audio/` — ephemeral; never persists across runs

**`.gitignore` contents:**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Frontend
frontend/node_modules/
frontend/dist/
frontend/.vite/

# Runtime data (never committed)
data/

# Editor / OS
.vscode/
.idea/
.DS_Store
Thumbs.db

# Local Claude config
.claude/settings.local.json
```

OAuth secrets live at `~/.toybox/secrets.json` (outside the repo); no exclusion rule needed.

## Key Design Decisions

### Two-path NLP
Curated regex/intent layer is the always-on watchdog; Claude is invoked behind a capability gate. This gives offline-mode parity for the basic loop, hard cost ceilings on Claude usage, and a place to land deterministic behavior the user can audit. Trade-off: the curated layer needs maintenance as kid vocabulary shifts. Mitigation: trigger registry is a JSON file the parent can edit.

### Single FastAPI process for everything
Mic capture, STT, NLP, AI calls, REST, and WebSockets all live in one async process. Simpler ops, single source of truth for state, no IPC complexity. Trade-off: a slow Claude call could starve the mic loop. Mitigation: AI calls run via `asyncio.to_thread` with a circuit breaker; mic loop is its own task that cannot be blocked by API requests.

### Claude via OAuth, not API key
Per the `claude-oauth-auth` skill. Aligns billing with the user's existing subscription, removes API-key management, scales with Anthropic's subscription tiers. Trade-off: refresh logic + token rot. Mitigation: capability gate falls back to offline cleanly; parent UI surfaces token status.

### Linear activity scripts for v1
Tree branching and freeform per-step generation are deferred. Linear scripts are easy to author, render, persist, edit, and learn from. Trade-off: less responsive to in-the-moment kid behavior. Mitigation: "regenerate from here" parent action effectively branches when needed.

### Adult-only initial testing
The user and spouse exercise v1 (and likely v1.5) before any child uses it. This relaxes some UX concerns for the first build (no kid-readability gate on parent UI text) and is the rationale for deferring the PIN gate to Phase D.

### Single Vite project, two routes
Parent and child apps share types and persona/activity rendering primitives. Bundling them together avoids duplication. Trade-off: child bundle is slightly larger than a dedicated build. Mitigation: lazy-load parent-only routes; child kiosk loads the smaller chunk.

### House map = rooms + tagged features
Spatial geometry deferred. Rooms-as-nodes + named features ("couch," "toy chest") gives the activity generator enough hooks ("hide behind the couch in the living room") without requiring vision-derived geometry. Trade-off: no path planning. Acceptable — this isn't a robotics project.

### Anti-signal feedback model
"This didn't work" stored as a `feedback` row, threaded into the activity generator as both an offline filter (block exact step patterns) and a Claude-prompt addendum (free text "things this family doesn't like"). Trade-off: requires the parent to actually press the button. Mitigation: dismissed-pre-approval also counts as soft anti-signal.

### Single-worker SQLite + WAL
SQLite + multi-worker writes corrupt silently. Toybox runs as one uvicorn worker; mic, STT, NLP, AI calls, and HTTP all live in one async process. Trade-off: no horizontal scale. Acceptable — a household device serves a household.

### Optimistic concurrency on activities
Activity state mutations require `If-Match-Version` matching the row's current `version` integer. Two parent tabs racing approve/dismiss → second loses with 409 + current state. Cheap to add now; would require schema migration + every mutation site to update later. Justified by the multi-tab parent-app expectation.

### Curated NLP registry copied into `data/`
Shipped registry seeds a user-editable copy in `data/triggers.json`. Survives package upgrades; lets parents tune triggers without editing source. Trade-off: schema migrations on the registry need a merge step. Mitigation: pattern entries carry a `version` field; loader fills missing fields from shipped defaults.

### Mic-hot indicator as a first-class UI element
A passively-listening device aimed at children needs a constant-on visual indicator that mic capture is live. Independent of mode (mode 1 still records). Trust + consent posture, and a hard-to-ignore sanity check during development.

### Pydantic ↔ TypeScript codegen
Manual type sync between Pydantic models and `frontend/src/shared/types.ts` drifts. `pydantic-to-typescript` runs as a Make target / pre-commit hook on backend changes. Trade-off: an extra build step. Worth it — drift here corrupts both sides silently.

## Open Questions / Risks

| Item | Risk | Mitigation |
|------|------|------------|
| faster-whisper accuracy on kid speech | Garbled transcripts → bad triggers | Default `small` is fine on adult speech (initial testing); evaluate `medium`/`large-v3` when kids start using; `TOYBOX_STT_CONFIDENCE_FLOOR` blocks low-confidence transcripts from firing triggers |
| Claude OAuth token refresh failures | Silent offline mid-session | Background refresh task; `capability_reason` enum distinguishes config vs token vs network; parent UI banner per reason |
| Curated NLP coverage of toddler-ese | False negatives | Trigger registry editable in `data/triggers.json`; transcript log lets parent see what was heard but didn't fire; user edits survive package upgrades |
| Single mic in busy household | Multiple kids, parent voices, TV → noisy triggers | VAD gate + confidence threshold + parent has final approval; multi-mic schema in v1.5 |
| Storage growth from transcripts | DB bloat | Vacuum trigger at 100k rows / 100 MB; PIN-gated wipe button |
| Claude vision cost on toy/room ingest | $$ over a large catalog | One-time per item; SHA-256 dedup prevents re-billing on re-upload; offline mode skips vision; parent can decline AI suggestion |
| Persona IP boundaries | "Elmo" specifically can't ship | Library uses archetypes ("friendly red monster"); user can customize names locally |
| WebSocket reconnect during activities | Brief disconnect = stuck step on child app | Server pings every 20s, closes if no pong within 30s. Client reconnects with exponential backoff: 1s → 2s → 4s → 8s → 16s → cap at 30s, jitter ±25%. On reconnect, client refetches the active activity via REST and resubscribes to all prior topics. |
| Children's voice retention privacy | Even local-only audio is sensitive | Audio ephemeral by design; transcripts redactable; PIN-gated wipe in Phase D; mic-hot indicator + mute toggle for trust |
| Child app in browser, not native | Tablet may sleep, lock, etc. | Documented setup: kiosk mode (`chrome --kiosk`), display always-on; v2 native shell |
| CUDA toolkit on Windows for GPU whisper | Setup friction | Default to CPU; `TOYBOX_WHISPER_DEVICE=auto` falls back; CPU `small` runs faster than realtime on a modern machine |
| Mic loop blocked by long Claude call | Missed audio | AI calls in `asyncio.to_thread`; mic loop is independent task with its own ring buffer |
| Anthropic rate limits in mode 5 | Cost spike or 429 spam | `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` throttle; 429 opens breaker for `retry-after`; queued triggers route to offline path |
| Multi-tab parent app race conditions | Two tabs racing approve/dismiss | `If-Match-Version` on every mutation; 409 + state refresh on mismatch; ws state sync keeps tabs aligned |
| Pydantic ↔ TypeScript type drift | API contract decays silently | `pydantic-to-typescript` codegen wired into pre-commit / CI; drift is a check failure |
| Photo-upload path traversal | Arbitrary file write via filename | Server-generated UUID filenames; user filename discarded; static serving whitelisted to `data/images/{toys,personas,rooms}/` |
| First-run model download on no-internet machine | Setup blocked | Documented in How to Run; `--download` script is explicit; offline-clean once cached |
| Family Wi-Fi exposure | Backend on `0.0.0.0` reachable to anyone on the LAN | Default `TOYBOX_HOST=127.0.0.1` (loopback-only); LAN-binding startup guard refuses `0.0.0.0` until parent PIN is set (Phase D step 20); Origin header allow-list enforced on `/ws` + state-changing REST |
| Migration apply failure on startup | DB locked in partial state | Forward-only for v1; abort + preserve DB; operator/recovery.md walks through manual restore from backup |

## How to Run

### System requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 11 (primary), macOS 13+, Linux (Ubuntu 22.04+) | Windows 11 |
| RAM | 8 GB | 16 GB |
| Disk | 5 GB free (incl. ~500 MB whisper-small download + room for transcripts) | 20 GB |
| CPU | 4 cores ≥3.0 GHz | 8+ cores |
| GPU | not required (CPU `small` is faster than realtime) | NVIDIA, ≥4 GB VRAM. GPU mode requires **CUDA Toolkit 11.8 or 12.x AND cuDNN 8.x** (faster-whisper / ctranslate2 needs both). Test via `python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cuda')"` |
| Mic | any USB or built-in mic with 16 kHz mono support | conference USB mic in play area |
| Network | only required first-run (model download, OAuth) and for Claude calls | persistent LAN, optional WAN |

**Child tablet browser:** Chrome 100+ or Safari 16+. Must support WebSocket and Web Audio API. iPad Safari, Chromebook Chrome, and Fire HD Silk Browser have all been verified target platforms.

**First run downloads:** ~500 MB faster-whisper `small` model + ~1 MB silero-vad ONNX from HuggingFace. Cached to `data/models/` afterward; subsequent runs are offline-clean.

### First-time setup

```powershell
cd c:\Users\abero\dev\toybox

# Python deps
uv sync

# Frontend deps
cd frontend; npm install
# Playwright browsers (UI smoke tests; ~300 MB on first install)
npx playwright install
cd ..

# Initialize DB (applies migrations, copies trigger registry to data/, copies persona avatars)
uv run python -m toybox.db.migrate

# Pre-download whisper + VAD models (optional; happens lazily on first transcription otherwise)
uv run python -m toybox.audio.stt --download

# Set up Claude OAuth (see operator/claude-oauth-setup.md)
# Token written to ~/.toybox/secrets.json (Windows: %USERPROFILE%\.toybox\secrets.json)

# Verify
uv run python -m toybox.main --check
# Expected output: ok, db ready, whisper model loaded, vad model loaded, claude capable, mic detected
```

### Run dev

```powershell
# Terminal 1 - backend (loopback only by default; LAN binding requires PIN)
uv run python -m toybox.main --host 127.0.0.1 --port 8000

# Terminal 2 - frontend
cd frontend; npm run dev

# Open http://localhost:3000/parent on the home machine
```

### Run dev — child tablet on LAN (Phase D and later only)

After Phase D step 20 sets a parent PIN, LAN binding is unlocked:

```powershell
# Find the home machine's LAN IP
ipconfig                          # look for IPv4 Address under your Wi-Fi adapter
$env:TOYBOX_LAN_IP = "192.168.1.42"

# Backend on LAN
uv run python -m toybox.main --host 0.0.0.0 --port 8000

# Frontend on LAN
cd frontend; npm run dev -- --host 0.0.0.0

# Pair the tablet from the parent UI; tablet opens http://<lan-ip>:3000/child
```

**LAN trust assumption:** binding `0.0.0.0` exposes toybox to anyone on your home Wi-Fi. The LAN-binding startup guard prevents it without a PIN; the PIN gate + Origin check are the actual controls. Do not run toybox on a public, hotel, or shared Wi-Fi even with a PIN — these have no defense against pairing-flow phishing.

### Run tests

```powershell
uv run pytest                                    # unit + integration
uv run pytest -m "not requires_claude"           # offline-only suite
cd frontend; npm run test                        # vitest
cd frontend; npm run test:ui                     # playwright smoke
```

### Quality gates (per build-step defaults)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
cd frontend; npm run typecheck; npm run lint; npm run test
```

## Development Process

Use `/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` where TDD makes sense — schema/CRUD steps are good TDD candidates).

**Prerequisite before the first `/build-phase` run:** run `/repo-init` to create the GitHub repo + per-step issues, then `/repo-sync` to populate the `**Issue:** #` lines in each step (currently `TBD`). `/build-phase` posts progress to those issues; missing issue numbers break the audit trail. Re-run `/repo-sync` after any plan-doc edits that change step shape or numbering.

Build order: Phase A → B → C → D. Manual steps interleave as marked.

### Phase A — Closed-loop skeleton (v1 ship)

Goal: parent clicks "trigger demo," sees suggestion, approves, child app runs activity to completion. No audio. No Claude.

#### Automated steps

| # | Step | Issue | Reviewers | Done-when |
|---|------|-------|-----------|-----------|
| 1 | Project skeleton | #1 | `--reviewers full --start-cmd "uv run python -m toybox.main" --url http://localhost:8000/api/health` | Backend serves `/api/health` returning `capability_reason`; default bind is `127.0.0.1`; LAN-binding startup guard refuses non-loopback host when PIN unset (test: `TOYBOX_HOST=0.0.0.0` → exit non-zero with `code=lan_bind_requires_pin`); vite serves `/parent` and `/child` placeholder pages; vite proxies `/api` and `/ws` to :8000; ruff/mypy/pytest configured and clean; `pydantic-to-typescript` codegen wired with verified `ErrorCode` StrEnum emission (see Phase A step 1 spike note below) |
| 2 | SQLite schema + migrations | #2 | `--reviewers code` | All tables created via `0001_initial.sql` including `auth_tokens`, `image_hash` (toys/rooms), `avatar_image_hash` (personas), `version` (activities), `signature` (feedback), `language` (personas), `UNIQUE(room_id, name)` on `room_features`, all settings keys; partial UNIQUE indexes for image hashes match the spec; FK ON DELETE clauses applied (RESTRICT default, CASCADE for `feedback.activity_id` and `activity_steps.activity_id`); WAL + foreign_keys + busy_timeout pragmas applied at connection open; slug derivation utility + collision-rule tests; round-trip insert/read tests pass for every table; concurrent-write smoke test passes without corruption; migration test pattern wired (every future migration ships `tests/integration/migrations/test_NNNN_*.py` loading a v=N-1 fixture DB and asserting v=N schema) |
| 3 | Persona library JSON + loader | #3 | `--reviewers code` | 4 archetype JSONs validated against `_schema.json`; 4 avatar PNGs shipped + credited in `_credits.md`; loader idempotent (second startup is no-op); avatars copied to `data/images/personas/` on first run |
| 4 | Listening mode state machine | #4 | `--reviewers code` | Modes 1–5 persist; ws emits on change with envelope shape; capability composition tested; `capability_reason` reachable from each path |
| 5 | Claude OAuth client + capability gate + circuit breaker | #5 | `--reviewers code` | `is_capable()` False in offline / missing-token / expired / breaker-open / rate-limited cases each emit correct `capability_reason`; background refresh task implemented; AI call sites stubbed for tests |
| 6 | Curated NLP trigger registry | #6 | `--reviewers code` | 20+ trigger patterns parse correctly; dynamic toy-name trigger registers; user-editable copy seeded to `data/triggers.json` on first run; loader merges shipped defaults into user file |
| 7 | Offline activity generator | #7 | `--reviewers code` | Given (intent, slot, context, hour-of-day) returns a 5-step activity; deterministic given seed; 10 sample inputs produce coherent outputs; time-of-day routing tested |
| 8 | Activity API + ws + auth scaffolding | #8 | `--reviewers code` | Full state machine enforced; `If-Match-Version` enforced on all mutations (409 on mismatch, response body includes current `version`); proposed-queue capped at 5 (oldest auto-dismissed); ws envelope shape matches contract; ws auth requires session token (pre-Phase-D `/api/auth/parent` returns a token without PIN check, but LAN-bind guard from step 1 blocks LAN exposure); Origin header check enforced on `/ws` upgrade + state-changing REST handlers (allow-list test: `Origin: http://evil.example` rejected with 403); per-subscriber bounded queue with drop-oldest + emits `system` notice (`code=ws_backpressure_drop`); ws heartbeat: server pings every 20s, closes connection if no pong within 30s; tests cover happy path + invalid transitions + version conflicts + auth-required topics + Origin reject + backpressure drop-oldest under synthetic burst (200 messages to a stalled subscriber) + concurrent `If-Match-Version` race (two clients, same version, exactly one 409); `child_ids` selected at approval (server fills if 1 child profile) |
| 9 | Parent UI — suggestion + activity panel + mic-hot indicator | #9 | `--reviewers full --start-cmd "<see step 1>" --url "http://localhost:3000/parent" --ui` | Mic-hot indicator visible in header (green/red/grey states); trigger button creates suggestion; approve transitions to running; skip/regenerate/end work; "didn't work" persists; capability banner appears when offline; mic mute toggle works |
| 10 | Child UI — kiosk activity view | #10 | `--reviewers full --start-cmd "<see step 1>" --url "http://localhost:3000/child" --ui` | Persona avatar + current step render; sfx fires on transition (silence stub OK); next-step button advances; ws auto-reconnect tested with state resync on reconnect |

**Phase A step 1 spike — pydantic2ts + StrEnum:** before declaring step 1 done, write a 30-line scratch script that defines `class ErrorCode(StrEnum)` with two members, runs `pydantic2ts` on the module, and inspects the generated TS. If `pydantic2ts` emits the enum as a TS string-literal union (`type ErrorCode = "upload_too_large" | ...`), the codegen path works as planned. If not (older pydantic2ts versions skip non-Pydantic exports), fall back to a 20-line `tools/gen_error_codes_ts.py` that walks `ErrorCode` and writes `frontend/src/shared/errors.ts` directly; wire it into the same pre-commit hook slot. Either way, `errors.ts` must regenerate from `core/errors.py` deterministically.

#### Step 1: Project skeleton

- **Problem:** Stand up the backend (FastAPI + uvicorn entrypoint) and frontend (Vite, two routes `/parent` and `/child`) scaffolds, plus the toolchain (ruff line-length=100, mypy strict, pytest) and the pydantic-to-typescript codegen path. Backend serves `GET /api/health` returning `capability_reason`. Default bind is `127.0.0.1`; LAN-bind startup guard refuses non-loopback host without a parent PIN (`TOYBOX_HOST=0.0.0.0` → exit non-zero with `code=lan_bind_requires_pin`). Vite pins `server.port: 3000, strictPort: true` and proxies `/api` + `/ws` to `:8000`. The pydantic2ts + StrEnum spike must verify the codegen path emits a string-literal union (or activate the `tools/gen_error_codes_ts.py` fallback) before this step is "done." See issue #1 for full file list, Done-when, and spike procedure.
- **Type:** code
- **Issue:** #1
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:8000/api/health"
- **Status:** DONE (2026-05-01)

#### Step 2: SQLite schema + migrations

- **Problem:** Implement the full v1 SQLite schema in `0001_initial.sql` (toys, personas, children, rooms, room_features, activities, activity_steps, feedback, transcripts, sessions, auth_tokens, settings, schema_migrations) plus the migration runner, a connection helper that applies WAL/synchronous/foreign_keys/busy_timeout pragmas at every open, the slug-derivation utility (`python-slugify` with collision rule and `invalid_display_name` rejection), and the migration test pattern that every future migration must follow. Forward-only — no rollback path. Includes partial UNIQUE indexes for image hashes, FK ON DELETE clauses (RESTRICT default; CASCADE on `feedback.activity_id` and `activity_steps.activity_id`), `auth_tokens` columns, `version` on activities, `signature` on feedback, `language` on personas, and `UNIQUE(room_id, name)` on `room_features`. See issue #2 for required columns and full constraint list.
- **Type:** code
- **Issue:** #2
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 3: Persona library JSON + loader

- **Problem:** Ship the four library personas (Princess Lyra, Marvelous the Wizard, Inspector Pip, Professor Iridia) as JSON files validated against `_schema.json`, plus their PNG avatars credited in `_credits.md`. The loader runs at startup, is idempotent on second run (no duplicate inserts, no avatar re-copy), and copies avatars to `data/images/personas/` on first run. Library personas can be edited (system_prompt, behavior_tags) but not deleted (only hidden). `avatar_image_hash` is null for library personas; user-uploaded persona avatars participate in the partial UNIQUE index. See issue #3 for persona JSON shape and IP boundary notes.
- **Type:** code
- **Issue:** #3
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 4: Listening mode state machine

- **Problem:** Implement modes 1–5 with persistent settings (read from `settings` on startup), ws-emit on mode change with the typed envelope shape `{topic, ts, payload, schema_version}`, and a capability composition module whose `capability_reason` enum values (`config_missing`, `token_missing`, `token_expired`, `breaker_open`, `rate_limited`, `network_offline`) are each reachable from at least one path. The state machine is dumb at this step — actual mic + STT + Claude wiring lands in Phase B. Mode 4's spontaneous timer is owned by this layer; the actual Claude call dispatch lands in step 5. Default mode = `TOYBOX_DEFAULT_MODE` env var (default `3`). See issue #4 for mode behaviors and required test coverage.
- **Type:** code
- **Issue:** #4
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 5: Claude OAuth client + capability gate + circuit breaker

- **Problem:** Wrap the `claude-oauth-auth` flow in an async runtime client pinned to `TOYBOX_CLAUDE_TEXT_MODEL`/`TOYBOX_CLAUDE_VISION_MODEL` env vars (do not hard-code a model). `is_capable()` returns False with the correct `capability_reason` for missing-token / expired / breaker-open / rate-limited / network-offline / config-missing cases. Add a background refresh task that polls token expiry and refreshes within `TOYBOX_OAUTH_REFRESH_LEAD_SEC` of expiry, logging WARNING on refresh failure without crashing. Circuit breaker opens on consecutive failures (default 3) or any 429 (honors `Retry-After`); cooldown default 60s; half-open probe one trial call, success closes / failure reopens. AI call sites are stubbed for tests so steps 7–9 can land without live Claude. See issue #5 for capability-reason matrix and breaker spec.
- **Type:** code
- **Issue:** #5
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 6: Curated NLP trigger registry

- **Problem:** Build the regex-based trigger registry — 20+ patterns shipped in `defaults.json` plus a dynamic toy-name trigger source (queries `toys` table, refreshes when toys are added/removed; for v1 stub as "rebuild on each match call"). On first run, seed `data/triggers.json` with shipped defaults; on package upgrade, merge new shipped fields into the user file using `version` markers on each pattern. Loader exposes `match(text) -> list[Intent]` API. Deterministic and offline — no Claude calls. User edits to `data/triggers.json` survive package upgrades; the merge logic is the load-bearing piece. See issue #6 for trigger registry shape.
- **Type:** code
- **Issue:** #6
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 7: Offline activity generator

- **Problem:** Given (intent, slot, context, hour-of-day) return a 5-step linear `Activity`. Deterministic given a seed (same inputs + seed → identical output). Time-of-day routing affects template selection (`morning`, `afternoon`, `evening`, `wind_down`; e.g., `wind_down` excluded outside 19:00–21:00). Output Activity carries `template_id` + sorted slot values for `signature` computation in Phase D step 19 (anti-signal feedback). For Phase A use placeholder content (toys = `["Mr. Unicorn"]`); banned-themes filtering and real toys/rooms wire in Phase C step 18. Linear scripts only — no tree branching. This is the path for modes 1, 3 (when Claude not capable), and the fallback for 4–5 when breaker is open. See issue #7 for activity output shape.
- **Type:** code
- **Issue:** #7
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 8: Activity API + ws + auth scaffolding

- **Problem:** REST + ws contract for activity lifecycle: propose / approve / skip / regenerate / advance / end / "didn't work." Optimistic concurrency via `If-Match-Version` (decimal integer header) returning 409 + current version on mismatch. Proposed-queue capped at 5 (drop-oldest). ws envelope shape `{topic, ts, payload, schema_version}`. ws auth requires session token; `/api/auth/parent` issues tokens without PIN check pre-Phase-D, but the LAN-bind guard from step 1 blocks LAN exposure regardless. Origin allow-list (`http://localhost:3000`, `http://127.0.0.1:3000`, optional `http://${TOYBOX_LAN_IP}:3000`) enforced on `/ws` upgrade + state-changing REST handlers. Per-subscriber bounded queue (drop-oldest + emits `system` notice with `code=ws_backpressure_drop`). Heartbeat: server pings every 20s, closes on 30s no-pong. Internal pub/sub: publish never blocks; coalesce `triggers.invalidate`. `child_ids` selected at approval time (server fills if exactly 1 child profile exists). Tests cover happy path, invalid transitions, version conflicts, auth-required topics, Origin reject, backpressure drop-oldest under 200-message burst, concurrent `If-Match-Version` race (exactly one 409). See issue #8 for full test matrix.
- **Type:** code
- **Issue:** #8
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

#### Step 9: Parent UI — suggestion + activity panel + mic-hot indicator

- **Problem:** Build the parent route (`/parent`) in React + TypeScript + Vite + Zustand: mic-hot indicator (green = capturing, red = error, grey = paused) in header, mic mute toggle, manual trigger button (replaces real mic until Phase B), suggestion card with approve/skip/dismiss, activity panel with regenerate-from-here / end / "didn't work," capability banner that surfaces `capability_reason` when offline. ws auto-reconnect with exponential backoff (1s → 2s → 4s → 8s → 16s → cap at 30s, jitter ±25%) and state resync via REST on reconnect. 409 handling refetches activity and surfaces a toast (no blind retry). Suggestion card "why this?" expandable panel ships in Phase D step 22 — leave a stub. See issue #9 for component file list.
- **Type:** code
- **Issue:** #9
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:3000/parent" --ui

#### Step 10: Child UI — kiosk activity view

- **Problem:** Build the child kiosk route (`/child`) in React + TypeScript: full-bleed persona avatar + current step text, next-step button (calls `POST /api/activities/{id}/advance` with `If-Match-Version`), SFX firing on step transition (silence stub acceptable for v1; M4 sources the real WAVs in `frontend/public/sfx/`). ws auto-reconnect with state resync — child page recovers active step + persona without parent intervention. Activity-end transitions to a friendly "all done" state. End of this step closes the v1 Phase A loop: trigger → suggestion → approve → child runs activity → completion. Adult-only smoke test before Phase B starts. See issue #10 for component file list and SFX format spec.
- **Type:** code
- **Issue:** #10
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:3000/child" --ui

**End of Phase A = v1.** Adult-only smoke test, then move to Phase B.

#### Manual steps

##### M1 — Claude OAuth setup (run before Phase B step 12 needs it)

Procedure documented at `documentation/operator/claude-oauth-setup.md`.

```powershell
# Use the claude-oauth-auth skill flow
# Token saved to ~/.toybox/secrets.json
uv run python -m toybox.ai.client --check
```

What to look for:

| Check | Expected |
|-------|----------|
| `~/.toybox/secrets.json` exists | yes |
| `--check` output includes `claude_capable=True` | yes |
| Token expiration shown is in the future | yes |

### Phase B — Hearing

| # | Step | Reviewers | Done-when |
|---|------|-----------|-----------|
| 11 | Audio capture daemon + VAD gate | `--reviewers code` | sounddevice captures 16 kHz mono int16; ring buffer cycles ~2 min; silero-vad gates STT (only speech chunks emit); synthetic-buffer tests pass; `--test 5` operator script works |
| 12 | faster-whisper integration | `--reviewers code` | GPU autodetect; first-run model download to `data/models/`; bundled WAV transcribes within edit-distance tolerance; CPU fallback works; `confidence` populated; `transcribe` runs in `asyncio.to_thread` so mic loop is not blocked |
| 13 | Transcript pipeline + persistence + ws | `--reviewers code` | Synthetic transcript stream produces correct triggers; `transcripts` table populates; live ws topic emits with envelope shape; transcripts below confidence floor are stored but skip trigger evaluation |
| 14 | Mode-aware Claude escalation + rate-limit handling | `--reviewers code` | Each mode produces correct call counts under synthetic input; min-interval throttle enforced; 429 opens breaker; malformed Claude output falls back to offline path |
| 14b | E2E pipeline test (synthetic audio → child UI) | `--reviewers full --start-cmd "<see step 1>" --url "http://localhost:3000/parent" --ui` | `python -m toybox.main --smoke` plays `tests/fixtures/audio/lets_play_unicorns.wav` → STT → trigger → suggestion fires; Playwright drives parent approve → child UI renders step 1; marked `@pytest.mark.slow`, runs in CI nightly |

#### Manual M2 — Mic hardware test (after step 11)

```powershell
uv run python -m toybox.audio.capture --test 5
```

What to look for:

| Check | Expected |
|-------|----------|
| Default mic detected and named in output | yes (USB or laptop mic) |
| 5 seconds of audio captured | yes |
| Peak level > -40 dB while speaking | yes |
| No buffer overruns logged | none |

### Phase C — Content

| # | Step | Reviewers | Done-when |
|---|------|-----------|-----------|
| 15 | Toy ingest (vision + UI) | `--reviewers full --ui` | All upload validation rules enforced (size, MIME-sniff, dimensions, UUID-rename, atomic staging); SHA-256 dedup returns existing toy on collision; vision → suggested fields → parent confirms → row inserted with `image_hash`; offline path skips vision; mention_toy registry refreshes |
| 16 | Room ingest bulk (vision + UI) | `--reviewers full --ui` | Bulk-cap of 50 enforced; per-file validation per Upload validation rules; per-photo vision → tabbed review UI → rooms + features inserted; dedup applied |
| 17 | Child profile editor | `--reviewers full --ui` | Full CRUD; banned-themes flow into activity generator (offline filter + Claude prompt); reading_level affects step text complexity |
| 18 | Activity generator uses real content | `--reviewers code` | Real toys/rooms appear in generated steps; tests use fixture catalog; banned-themes filtering tested; anti-signal feedback consulted |

#### Manual M3 — Real play session (after Phase C)

```powershell
# Start backend + frontend per "Run dev"
# Set listening mode to 3 via parent UI slider
# Run for 30 minutes during real play (adult-only for v1 / v1.5)
# File one issue per friction point
```

What to look for:

| Check | Where | Expected |
|-------|-------|----------|
| Suggestions trigger when expected | parent UI suggestion panel | within 10 sec of curated phrase |
| Suggestions don't trigger when not | parent UI | < 2 spurious per 30 min |
| Approved activities run cleanly | child UI | all 5 steps render, sfx fires |
| "Didn't work" feedback persists | DB `feedback` table | row inserted with reason |
| No mic dropouts | backend log | no `mic_queue_overflow` events |
| Claude calls fire on curated triggers only | backend log (`grep "claude call"`) | mode 3: zero spontaneous calls; one call per matched trigger |

> **Note:** M3 runs before Phase D step 23 ships the metrics dashboard, so all observability above is via DB queries + backend log grep. The dashboard makes this nicer in v1.5.

### Phase D — Polish

| # | Step | Reviewers | Done-when |
|---|------|-----------|-----------|
| 19 | Anti-signal feedback in generator | `--reviewers code` | Generator computes `signature = sha256("{template_id}:{sorted slot k=v}")` for every candidate; `feedback.signature` matches with `kind='didnt_work'` cause re-pick; `kind='loved_it'` boosts ranking; dismissed-pre-approval is soft anti-signal; tests cover the matching logic |
| 20 | Parent PIN gate (argon2id + rate-limit) | `--reviewers full --ui` | First-run flow sets PIN; argon2id with `m=65536,t=3,p=4`; `/api/auth/parent` validates PIN against stored hash; rate-limit: 5 wrong attempts in 5 min locks PIN entry for 15 min; failed attempts logged at WARNING with count only; PIN reset path documented in operator/recovery.md; gated routes 403 without token; settings/wipe/persona-edit screens require parent token |
| 21 | Transcript management UI | `--reviewers full --ui` | List + search + delete one + wipe all (PIN-gated); confirmation dialog on wipe |
| 22 | Live activity polish + suggestion "why this?" | `--reviewers full --ui` | Pause/resume idempotent; regenerate-from-here replaces remaining steps with version bump; end requires confirm dialog; suggestion card has expandable "why this?" panel showing trigger phrase + persona match reasoning |
| 23 | Metrics endpoint + ws topic + parent operator dashboard | `--reviewers full --ui` | `/api/metrics` returns counters + averages + breaker state + mic device + queue depth; `metrics` ws topic snapshots every 30 sec; in-memory counters survive ws reconnects; parent UI "Operator" tab renders all metrics with auto-refresh |

#### Manual M4 — Sound effect sourcing (any time before Phase A step 10 final review)

| Asset | Purpose | Source |
|-------|---------|--------|
| `transition.wav` | step → next step | royalty-free (e.g. freesound.org CC0) |
| `success.wav` | "this worked" | royalty-free |
| `persona_enter.wav` | persona appears | royalty-free |
| `persona_leave.wav` | activity ends | royalty-free |
| `tada.wav` | optional flourish | royalty-free |

**Format spec:** 16-bit PCM WAV, mono, 22.05 kHz, peak normalized to -3 dBFS, ≤2 seconds, no leading silence > 50 ms. Drop in `frontend/public/sfx/`. Silence works as a Phase A placeholder. Track licenses in `frontend/public/sfx/_credits.md`.

#### Manual M5 — Operator recovery procedures (referenced from `documentation/operator/recovery.md`)

What to look for table is "if X happens, run Y"; the operator doc holds the full procedures. Stub recipes:

| Symptom | Recovery |
|---------|----------|
| DB corrupt or wedged | Stop backend; `mv data/toybox.db data/toybox.db.broken-$(date +%s)`; restart (re-applies migrations into a fresh DB). **v1 has no backups** — toy/room/persona/transcript data is lost. v1.5 will add nightly snapshots; until then, ad-hoc manual copies of `data/toybox.db` (backend stopped) are the only fallback. |
| Migration failed at startup | Stop backend; copy `data/toybox.db` aside (`cp data/toybox.db data/toybox.db.pre-failed-migration`); inspect logged migration filename + traceback; either fix the migration SQL and restart, or factory-reset per below |
| Whisper model load fails | `rm -rf data/models/`; restart (re-downloads on first transcription) |
| Claude OAuth wedged | `rm ~/.toybox/secrets.json`; re-run `claude-oauth-auth` flow; restart |
| Forgot parent PIN | Stop backend; `sqlite3 data/toybox.db "DELETE FROM settings WHERE key='parent_pin_hash'"`; restart; first-run PIN prompt re-appears (this is the documented reset path) |
| Mic dropouts / wrong device | Set `TOYBOX_MIC_DEVICE_INDEX=N` per `python -m sounddevice` device list output; restart |
| Image storage runaway | Stop backend; archive unwanted toys via parent UI; periodic cron at v1.5 will delete orphan files (manual: `python -m toybox.tools.gc_images`) |
| "Factory reset" | Stop backend; remove `data/`; restart (re-runs migrations + first-run setup; all photos, transcripts, profiles, custom personas lost) |

## Appendix

### Persona JSON shape

```json
{
  "id": "wizard",
  "display_name": "Marvelous the Wizard",
  "archetype": "wizard",
  "system_prompt": "You are Marvelous, a kindly old wizard who speaks in rhymes and treats every problem as a magical puzzle. You never frighten children. You love nature, riddles, and small kindnesses. When invited to play, you propose challenges that involve searching, naming, or making things up.",
  "avatar_image_path": "library/avatars/wizard.png",
  "behavior_tags": ["kind", "rhyming", "puzzling", "gentle"],
  "age_range_min": 3,
  "age_range_max": 12,
  "source": "library",
  "default_voice_tone": "warm-and-slow"
}
```

The four shipped library personas:

- **Princess Lyra** — brave-and-curious archetype, treats the house as a kingdom, fond of quests.
- **Marvelous the Wizard** — kindly riddler, magical puzzles.
- **Inspector Pip (Detective)** — questions everything, "the case of the missing X" framing.
- **Professor Iridia (Periodic Table Professor)** — every element has a personality; chemistry as a play motif. Custom for the user's son.

### Listening pipeline data flow

```
mic (sounddevice)
  → ring buffer (in-memory, ~2 min)
  → chunked emit (every ~3 sec)
  → faster-whisper.transcribe()
  → Transcript {text, confidence, timestamps}
  → triggers.match(text)
  → on hit: Intent {slug, slot} → core.activities.handle_intent(intent, mode)
       ├─ mode 1: offline template generator
       ├─ mode 2: no-op (await parent solicit)
       ├─ mode 3: claude path if capable, else offline
       ├─ mode 4: claude path + spontaneous timer
       └─ mode 5: every-utterance claude path (throttled)
  → ws emit on `activity.state` topic
  → parent UI surfaces suggestion card
```

### Trigger registry shape

```json
{
  "version": 1,
  "patterns": [
    {
      "regex": "(?i)let'?s play\\s+(.+)",
      "intent": "request_play",
      "slot_group": 1
    }
  ]
}
```

### Configuration (env / settings)

| Key | Default | Notes |
|-----|---------|-------|
| `TOYBOX_HOST` | `127.0.0.1` | bind address. Default loopback-only. To bind LAN (`0.0.0.0` or specific IP), the parent PIN must be set first — startup guard refuses non-loopback bind without PIN. |
| `TOYBOX_LAN_IP` | unset | optional; when set, added to the `Origin` allow-list as `http://<value>:3000`. Set this to the home machine's LAN IP after the PIN is configured (Phase D). |
| `TOYBOX_PORT` | 8000 | backend |
| `TOYBOX_DATA_DIR` | `./data` | |
| `TOYBOX_OAUTH_PATH` | `~/.toybox/secrets.json` | Windows: `%USERPROFILE%\.toybox\secrets.json` |
| `TOYBOX_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `TOYBOX_WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `TOYBOX_VAD_AGGRESSIVENESS` | 2 | silero-vad threshold, 0 (permissive) – 3 (strict) |
| `TOYBOX_VAD_MIN_SPEECH_MS` | 300 | minimum sustained speech to trigger STT |
| `TOYBOX_MIC_DEVICE_INDEX` | unset (default device) | sounddevice device index; see `python -m sounddevice` |
| `TOYBOX_AUDIO_CHUNK_SEC` | 3 | STT chunk size after VAD gating |
| `TOYBOX_DEFAULT_MODE` | 3 | 1–5 |
| `TOYBOX_CLAUDE_TEXT_MODEL` | `claude-sonnet-4-6` | activity generation, vision-free reasoning. Sonnet 4.6 is the cost/quality default; bump to Opus 4.7 for richer activities once cost is understood. |
| `TOYBOX_CLAUDE_VISION_MODEL` | `claude-haiku-4-5-20251001` | toy + room photo understanding. Haiku is fast and cheap for one-shot vision; sufficient for "name the toy / list room features." |
| `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` | 30 | mode 5 throttle |
| `TOYBOX_CLAUDE_SPONTANEOUS_INTERVAL_SEC` | 300 | mode 4 |
| `TOYBOX_WS_PING_INTERVAL_SEC` | 20 | server-side ping cadence |
| `TOYBOX_WS_PING_TIMEOUT_SEC` | 30 | close if no pong within this window |
| `TOYBOX_STT_CONFIDENCE_FLOOR` | -0.7 | faster-whisper `avg_logprob`; range -1.0 (worst) to 0.0 (best); transcripts below this skip trigger evaluation but still persist for parent review |
| `TOYBOX_MIC_QUEUE_BOUND` | 100 | thread-safe queue between sounddevice callback and asyncio pump; ~6 sec at 64 ms/chunk; drop-oldest on overflow with a `metrics` event |
| `TOYBOX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`; logs to stdout, structured JSON when not a TTY |
| `TOYBOX_TIME_OF_DAY_AWARE` | `true` | inject local hour into activity generator context |
| `TOYBOX_PIN_MAX_ATTEMPTS` | 5 | failed PIN attempts before lockout |
| `TOYBOX_PIN_LOCKOUT_SEC` | 900 | lockout duration after exceeding max attempts |
| `TOYBOX_WS_QUEUE_BOUND` | 100 | per-subscriber outbound message queue size |
| `TOYBOX_PARENT_TOKEN_TTL_SEC` | 86400 | sliding expiry for parent session token |
| `TOYBOX_CHILD_TOKEN_TTL_SEC` | 2592000 | child kiosk pairing token TTL (30 days) |

### Audio capture spec

| Property | Value |
|----------|-------|
| Sample rate | 16 kHz (whisper-native) |
| Channels | 1 (mono) |
| Format | int16 PCM |
| Block size | 1024 samples (~64 ms) |
| Ring buffer | 2 minutes (1.92 M samples, ~3.7 MB) |
| VAD chunk | 30 ms windows fed to silero-vad |
| STT chunk | accumulated speech segments, max 3 sec |

### `frontend/package.json` outline

```jsonc
{
  "name": "toybox-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "typecheck": "tsc -b --noEmit",
    "lint": "eslint src --max-warnings=0",
    "test": "vitest run",
    "test:ui": "playwright test"
  },
  "dependencies": {
    "react": "^18",
    "react-dom": "^18",
    "react-router-dom": "^6",
    "zustand": "^4"
  },
  "devDependencies": {
    "typescript": "^5",
    "@types/react": "^18",
    "@types/react-dom": "^18",
    "@vitejs/plugin-react": "^4",
    "vite": "^5",
    "vitest": "^1",
    "@playwright/test": "^1",
    "eslint": "^9",
    "@typescript-eslint/parser": "^7",
    "@typescript-eslint/eslint-plugin": "^7"
  }
}
```

### `tsconfig.json`

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "exactOptionalPropertyTypes": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "allowImportingTsExtensions": false,
    "outDir": "./dist",
    "baseUrl": "./src",
    "paths": {
      "@shared/*": ["shared/*"],
      "@parent/*": ["parent/*"],
      "@child/*": ["child/*"]
    }
  },
  "include": ["src/**/*", "tests/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

### `vite.config.ts` proxy

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
    host: true,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
```

### `pyproject.toml` dependency outline

```toml
[project]
name = "toybox"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "python-multipart>=0.0.9",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper>=1.0",
    "onnxruntime>=1.17",
    "argon2-cffi>=23",
    "python-slugify>=8",
    "anthropic>=0.25",
    "httpx>=0.27",
    "Pillow>=10",
    "pillow-heif>=0.16",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "ruff>=0.4",
    "mypy>=1.10",
    "pydantic-to-typescript>=2",
    "pre-commit>=3",
]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]   # dev/AGENTS.md standard

[tool.mypy]
strict = true
disallow_untyped_defs = true
no_implicit_optional = true

[tool.pytest.ini_options]
markers = [
    "requires_claude: integration test that hits Claude OAuth",
    "requires_gpu: needs CUDA",
    "slow: end-to-end pipeline test, runs in nightly CI only",
]
```

### Playwright config (`frontend/playwright.config.ts`)

```ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/ui',
  fullyParallel: false,           // backend has shared mic state
  retries: 1,
  use: {
    baseURL: 'http://localhost:3000',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'tablet',   use: { ...devices['iPad (gen 7)'] } },
  ],
  webServer: [
    {
      command: 'cd .. && uv run python -m toybox.main --host 127.0.0.1 --port 8765',
      port: 8765,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'npm run dev -- --port 3000',
      port: 3000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
```

Backend port shifted to 8765 in tests so CI runs don't collide with a dev backend on 8000.

### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check
        language: system
        types: [python]
      - id: ruff-format-check
        name: ruff format --check
        entry: uv run ruff format --check
        language: system
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run mypy src
        language: system
        types: [python]
        pass_filenames: false
      - id: pydantic-to-typescript
        name: regenerate frontend types
        entry: uv run pydantic2ts --module toybox.api.dto --output frontend/src/shared/types.ts
        language: system
        types: [python]
        pass_filenames: false
      - id: no-transcript-in-info-logs
        name: no transcript text in INFO+ logs
        entry: uv run python -m toybox.tools.check_no_transcript_in_info
        language: system
        types: [python]
```

### Test stubbing strategy

- **Unit tests** use `pytest-mock` (`mocker.patch.object`) for narrow stubbing.
- **Claude calls** stubbed via FastAPI dependency override: `app.dependency_overrides[get_claude_client] = lambda: FakeClaudeClient(canned_responses)`. Canned responses live in `tests/fixtures/claude/<scenario>.json`.
- **STT calls** stubbed by injecting a fake `Transcriber` that returns canned `Transcript` objects from a queue.
- **Vision calls** stubbed similarly; Anthropic vision is non-deterministic so live calls are gated behind `@pytest.mark.requires_claude` and skipped in default runs.
- **Audio fixtures** are real WAVs; STT runs end-to-end against them in `slow`-marked tests only.
- **DB tests** use a fresh on-disk SQLite per test (NOT in-memory — must validate WAL pragmas).

### Test fixtures inventory (`tests/fixtures/`)

| File | Purpose | License / source |
|------|---------|------------------|
| `audio/silence_3s.wav` | VAD negative test | generated by `tests/fixtures/_gen_silence.py` (committed); regenerated as needed |
| `audio/lets_play_unicorns.wav` | trigger positive test (E2E smoke) | recorded by author or freesound.org CC0 |
| `audio/im_bored.wav` | boredom intent positive | as above |
| `audio/multi_speaker.wav` | overlap robustness | as above |
| `photos/toys/plush_unicorn.jpg` | toy ingest happy path | CC0 (e.g. unsplash.com — record exact URL) |
| `photos/toys/blurry.jpg` | low-confidence vision | as above |
| `photos/rooms/living_room.jpg` | room ingest happy path | CC0 |
| `photos/rooms/kitchen.jpg` | second room | CC0 |
| `claude/activity_request_play_unicorns.json` | canned activity-gen response | hand-authored |
| `claude/activity_boredom.json` | canned activity-gen response | hand-authored |
| `claude/vision_toy_unicorn.json` | canned toy vision response | hand-authored |
| `claude/vision_room_living.json` | canned room vision response | hand-authored |
| `claude/error_429.json` | rate-limit response | synthetic |
| `claude/error_malformed.json` | schema-validation failure | synthetic |
| `feedback/signatures.json` | anti-signal test rows: 3 `didnt_work` + 2 `loved_it` covering matching `template_id` / slot fingerprints, plus 1 near-miss (different slot fill) for negative test | hand-authored |

`tests/fixtures/README.md` records exact source URL + license per asset.

### Asset `_credits.md` schema

Both `src/toybox/personas/library/_credits.md` and `frontend/public/sfx/_credits.md` follow this format:

```markdown
| File | Title | Author | Source URL | License |
|------|-------|--------|------------|---------|
| `wizard.png` | "Friendly Wizard" | (commissioned) Jane Doe | https://example.com/portfolio/123 | CC-BY-4.0 |
```

### Project root document outlines

`README.md` (skeleton — fleshed out by `/repo-init`):

```markdown
# toybox
AI assistant for play with children. Local-first, family-private.

See `documentation/plan.md` for full architecture + build plan.

## Quick start
- Python 3.12 + `uv sync`
- Frontend: `cd frontend && npm install`
- DB: `uv run python -m toybox.db.migrate`
- Run: `uv run python -m toybox.main --host 0.0.0.0` + `cd frontend && npm run dev`

## Status
v1 builds in 4 phases (A–D); 24 automated steps + 5 manual.
```

`AGENTS.md` (project-specific overrides over `dev/AGENTS.md`):

```markdown
# toybox agent instructions

Inherits from `dev/AGENTS.md`. Project-specific:

## Setup
- Python 3.12, `uv sync` (extras: `dev`)
- Frontend: `cd frontend && npm install`
- DB migrations: `uv run python -m toybox.db.migrate`

## Architecture pointers
- See `documentation/plan.md` for full architecture
- See `documentation/operator/` for runbooks
- Single uvicorn worker — never `--workers >1` (SQLite WAL is single-writer)

## Working rules
- Never log transcript text at INFO+
- Every Claude call goes through the capability gate
- Every activity mutation requires `If-Match-Version`
- Photo uploads always go through the validation pipeline (no direct `Image.open` on user bytes outside it)
```

`CLAUDE.md`:

```markdown
@AGENTS.md

# Toybox-specific notes for Claude Code
- Project root: `c:\Users\abero\dev\toybox\`
- Plan: `documentation/plan.md` is the source of truth
- Phase boundaries: never mix phase-A work with phase-B work in one PR
- When in doubt about ws topic shapes or DB schema, re-read the plan section, don't infer
```

### Operator markdown stubs

Each file lives in `documentation/operator/`.

**`claude-oauth-setup.md`:**
- Run `claude-oauth-auth` skill flow
- Paste resulting token into `~/.toybox/secrets.json` (Windows: `%USERPROFILE%\.toybox\secrets.json`)
- Run `uv run python -m toybox.main --check`; expect `claude_capable=True`
- Token rotation: just re-run; the file is overwritten

**`mic-hardware-test.md`:**
- List devices: `uv run python -m sounddevice`
- Quick test: `uv run python -m toybox.audio.capture --test 5`
- Pin a specific device: `setx TOYBOX_MIC_DEVICE_INDEX <N>` (Windows) and restart
- Troubleshooting: device permissions, sample-rate negotiation, USB hub power

**`play-session-template.md`:**
- Pre-flight: backend running, parent UI shows mic-hot green, mode set
- During session: parent UI suggestion approvals, child UI on tablet
- Post-session: skim transcripts for false negatives; tag flop activities with "didn't work"
- Issue template for friction reports (text body)

**`recovery.md`:**
- Recovery recipes from the [Manual M5 table](toybox/documentation/plan.md) expanded with full commands
- Each recipe lists: symptom, prerequisites (backup first?), exact commands, verification step

**`troubleshooting.md`:**
- Common error codes and what to do (cross-reference to `core/errors.py`)
- Mic dropouts, Claude rate-limit, ws disconnects, Pillow CVE updates
- "When to escalate to opening an issue" decision tree

### `.github/workflows/ci.yml` (outline; v1 ship optional)

```yaml
name: ci
on: [push, pull_request]
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest -m "not slow and not requires_claude and not requires_gpu"
      - name: pydantic2ts drift check
        run: |
          uv run pydantic2ts --module toybox.api.dto --output frontend/src/shared/types.ts
          git diff --exit-code frontend/src/shared/types.ts
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && npm ci
      - run: cd frontend && npm run typecheck
      - run: cd frontend && npm run lint
      - run: cd frontend && npm run test
```

### Persona library JSON Schema

`src/toybox/personas/library/_schema.json` validates every file in `library/*.json` (excluding `_*.json`). Required fields: `id`, `display_name`, `archetype`, `system_prompt`, `avatar_image_path`, `behavior_tags`, `age_range_min`, `age_range_max`, `source`. Schema enforced by loader on startup; malformed files logged and skipped.

### Future scope (out of v1)

- Voice synthesis (TTS) for personas
- Camera observation
- Phone-app mic source
- Multi-mic with child apps as sources
- Native child kiosk app
- Redfin/Zillow URL scraping
- Spatial house map with geometry
- Tree-branching activities
- Real-time freeform activity generation (vs current "regenerate from here")
- Persona voice library (recorded clips)
- Backups: nightly DB snapshot with 14-day retention
- Auto-start on boot (Windows service / systemd unit)
- Localization / i18n (currently English-only in persona prompts and UI strings; `personas.language` field already in v1 schema)
- Dark mode + accessibility audit on parent app
- Persona-image regeneration from prompts (so users can recommission their library art)
- `mDNS` / zeroconf discovery (`toybox.local`) so child tablet finds backend without manual IP
- Multi-worker SQLite migration (likely Postgres) if device ever leaves single-host deployment
- Transcript archive policy: auto-archive transcripts older than 30 days into `data/transcripts-archive-YYYY-MM.jsonl.gz` and drop from main DB to keep the live table snappy
- Secret-question PIN reset path so the SQL-DELETE recovery isn't the only one
- Audit log of admin actions (PIN changes, transcript wipes, persona library overrides)
- Dependabot / renovate config for Pillow + pillow-heif CVE hygiene; promote to v1 if not addressed by `/repo-init`
- `CHANGELOG.md` (Keep-a-Changelog format) — track schema migrations and behavioral changes
- Process supervision (Windows service / systemd unit) so the backend survives terminal close
- Disk-quota enforcement (warn/halt at configured cap on `data/` size)
