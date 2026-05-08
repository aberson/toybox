# Activity loop

> **Scope:** activity state machine, offline + Claude generators, slot registry, curated NLP triggers, ingestion paths. Read this when changing how activities are proposed/run, adding a generator path, or wiring trigger registration. Persistence is in [data-model.md](data-model.md); HTTP shape in [api.md](api.md); modes/capability in [runtime.md](runtime.md).

## State machine

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

## Step shape

```json
{
  "seq": 3,
  "body": "Mr. Unicorn whispers a secret — only the kid can find the next clue, hidden somewhere in the kitchen.",
  "sfx": "transition",
  "expected_action": "kid runs to kitchen and looks for next clue"
}
```

`body` is rendered on the child app. `expected_action` is parent-only — shown in the parent's live activity panel as a coaching hint.

## Linear template generator (offline path)

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

### Parametric slot registry

`src/toybox/activities/slots.py` defines the placeholder vocabulary the offline path can fill:

| Slot | Source | Contributes to feedback signature? |
|------|--------|------------------------------------|
| `{toy}` | resolved toy catalog (`{DEFAULT_TOY_NAME}` if empty) | yes |
| `{slot}` | caller-supplied via propose body (`{DEFAULT_SLOT_FILLER}` if absent) | yes |
| `{room}` | resolved room catalog (`{DEFAULT_ROOM_NAME}` if empty) | no |
| `{action_verb}` | hand-curated word list (~15 silly verbs) | no |
| `{adjective}` | hand-curated word list (~15 positive/silly) | no |
| `{prop}` | hand-curated word list (everyday safe objects) | no |
| `{body_part}` | hand-curated word list (kid-named body parts) | no |
| `{count}` | English number words 2..6 | no |

Fills resolve **once per activity** (in first-occurrence order across title + steps) so a single template like `"{toy} {action_verb}s through {room}"` reads consistently across all five steps. Word-list fills are intentionally excluded from the feedback signature so a `loved_it` on "{toy} stomps" still boosts the "{toy} skips" pick — same template, surface variety only.

Adding a new slot is a five-step change documented in `slots.py`'s module docstring (add value source, branch in `fill`, append to `KNOWN_SLOTS`, decide signature contribution, update schema description).

The Claude-authored template generation work (see Future enhancements: "AI-authored offline templates" in [appendix.md](appendix.md)) will produce templates that reference these slots; the registry is the contract that lets that work happen without further generator changes.

> **NOTE (Group 2 refinement):** child screen content (avatar + step text + sfx) is the v1 default. Pre-recorded persona voice clips and richer per-step assets are tracked as a Phase D+ refinement. Live parent controls (pause/skip/regenerate/end/"didn't work") are the agreed v1 set; richer mid-activity authoring is a future refinement.

## Claude path (modes 3+)

Single Claude call with structured-output schema:
- **Inputs:** child profile, available toys, available rooms, persona library, last 60 sec of transcript context, listening mode, anti-signal feedback, current local hour (if `time_of_day_aware`).
- **Output:** a 5-step activity matching the schema above, plus `summary` and `intent_source`.
- **Caching:** prompt-cache the persona/toy/room context per session.
- **Rate-limit handling:** on 429, breaker opens for `retry-after`; queued triggers route to the offline path; no retries hammered.
- **Output validation:** strict Pydantic schema; malformed output → fall back to offline template, log warning to `system` ws topic.

## Curated NLP triggers

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

## Ingestion paths

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
