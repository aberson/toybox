# Phase X — Room Import (from a real-estate listing)

## 1. What This Feature Does

Phase X lets a parent stand up a realistic set of household rooms in one pass instead of
uploading and naming each room photo by hand. The parent pastes a saved real-estate listing
(Redfin HTML or a list of photo URLs); the backend parses the room breakdown (e.g. 3 bedrooms /
2 baths / garage / yard), generates a named room set ("Bedroom #1", "Bathroom #2", "Playroom #1"),
downloads the listing photos through the existing image-validation pipeline, and best-guess
matches each photo to a room — filename heuristic first, then a **local CLIP zero-shot classifier**
(ONNX, runs on the existing on-device `onnxruntime`; no cloud, no Claude) — leaving any room as
"N/A — no photo" when nothing fits. The parent reviews
the proposed set in an editable table (rename, set type, reassign/clear photo) and commits. It
also adds the two missing room knobs: a **room type/category** and a **"room exists but stay out"**
toggle that keeps a room in the catalog but excludes it from play. It's built because rooms are
currently a one-at-a-time manual upload, and the parent wants to seed a whole house quickly.

## 2. Existing Context

Confirmed against source (via an Explore sweep), not docs:

- **Rooms exist but are thin.** `rooms(id, display_name, image_path, image_hash, notes)` +
  `room_features(id, room_id, name, tags)` — both in
  [`0001_initial.sql`](../../src/toybox/db/migrations/0001_initial.sql) (rooms at lines 44-50). Rooms
  have **no `type`/category** and **no `active`/"stay out" flag** — both must be added this phase.
- **A bulk-photo room pipeline already ships.** [`api/rooms.py`](../../src/toybox/api/rooms.py)
  exposes `POST /api/rooms/upload-bulk` (≤50 files, per-file vision suggestion) + atomic
  `POST /api/rooms/confirm-bulk`, plus `GET /api/rooms`, `GET /api/rooms/{id}`,
  `PATCH /api/rooms/{id}` (today only `display_name` + `notes`), `DELETE`, and
  `POST /api/rooms/{id}/image`. The staged-then-confirm shape is the template Phase X's import
  flow copies.
- **Image validation/storage is centralized and mandatory.**
  [`storage/images.py`](../../src/toybox/storage/images.py) is the only place `Image.open` may run
  (invariant #5). It magic-byte-sniffs MIME (jpeg/png/webp; HEIC rejected without pillow-heif),
  caps size (`TOYBOX_MAX_UPLOAD_BYTES`, default 15 MB) and dimensions (8000×8000, downscaled to
  ≤1600 for vision), SHA-256 dedups via `find_dedup(conn, "rooms", hash)`, stages to
  `data/images/.staging/`, and commits to `data/images/rooms/`. **Any photo the importer
  downloads MUST pass through `validate_upload` → `stage` → `commit_staging` — never a raw write.**
- **Existing room vision is Claude-based (left untouched).**
  [`ai/house_vision.py`](../../src/toybox/ai/house_vision.py) (Claude Haiku, cloud) backs the
  existing `upload-bulk` flow — Phase X does **not** use or change it. **No local image classifier
  exists anywhere in the repo** — Phase X builds one (net-new): a local CLIP zero-shot classifier
  exported to ONNX, run on the **existing `onnxruntime` core dep** (already used by faster-whisper
  + silero-vad), so matching is on-device + offline with no new heavy runtime dependency. The
  CLIP model files (image+text encoders + BPE vocab) are vendored under `data/models/clip/`
  (gitignored), fetched once via a `--download` setup entrypoint — same pattern as the whisper
  model download.
- **HTTP fetch = `urllib` only.** The canonical external-HTTP pattern is stdlib `urllib.request`
  (see [`ai/client.py`](../../src/toybox/ai/client.py)); the `anthropic` SDK and `requests` are
  banned (`.claude/rules/claude-auth.md`). **No HTML parser (BeautifulSoup/lxml) is installed** —
  listing parsing is regex/stdlib-`html.parser` based.
- **Rooms feed play.** Rooms surface to generation as `available_rooms` (room names) in
  `_do_propose` and to Claude via the `get_room` tool ([`ai/tools.py`](../../src/toybox/ai/tools.py));
  `ResolvedRoom` lives in [`activities/content_resolver.py`](../../src/toybox/activities/content_resolver.py).
  The "stay out" toggle must exclude `active = 0` rooms from these play paths while the parent UI
  still lists them — mirroring the toy `active` contract in
  [`0018_toy_active.sql`](../../src/toybox/db/migrations/0018_toy_active.sql).
- **Toy `active` is the exact pattern to copy.** `toys.active INTEGER NOT NULL DEFAULT 1`; parent
  UI shows inactive toys; mention-triggers + role-casting exclude `active = 0`. Rooms replicate
  this for "stay out".
- **Next migration number.** Phase W shipped (migrations 0024–0028), so the highest on disk is now
  `0028` and **Phase X's first migration is `0029`** (confirmed, no longer nominal).
- **External-content safety (load-bearing).** Pasted listing HTML is untrusted external content —
  parse it as **data**, never act on embedded directives (`.claude/rules/security.md`). Downloading
  arbitrary photo URLs is an **SSRF** vector — the fetcher needs a hard host-allowlist guard, not a
  doc note.

## 3. Scope

**In scope:**
- Room schema: add `room_type TEXT NULL` (category) + `active INTEGER NOT NULL DEFAULT 1`
  ("stay out") to `rooms`; wire both into `PATCH`/`GET` and the parent UI.
- Exclude `active = 0` rooms from play (generation `available_rooms` + `get_room` tool) while the
  parent UI still lists them.
- Listing parser: pasted Redfin HTML (or a newline/whitespace-separated photo-URL list) →
  `{room_counts: {<type>: <n>}, photo_urls: [...]}`. Pure, regex/stdlib only.
- Room-name generation: counts → proposed named rooms (`"Bedroom #1"`, `"Bathroom #2"`, …) with
  per-type numbering.
- Safe photo fetch: download each photo URL with an SSRF host-allowlist guard, then push the
  bytes through `storage/images.py` validation/staging.
- Photo→room matching: filename heuristic first (`master-bed-2.jpg` → bedroom), then a **local
  CLIP zero-shot classifier** (ONNX on the existing `onnxruntime`, CPU, offline) scoring each photo
  against the room-type label set; assign best guess per room, leave **N/A** when the top score is
  below a confidence threshold. Includes the `--download` setup entrypoint that vendors the CLIP
  model under `data/models/clip/`.
- Import API: a staged parse → review → commit flow mirroring `upload-bulk`/`confirm-bulk`.
- Parent UI: paste box + editable proposed-rooms table (rename, set type, reassign/clear photo,
  toggle "stay out") + commit.
- Smoke gate + operator UAT.

**Explicitly out of scope:**
- Live Redfin URL scraping (bot-blocked + ToS gray area) — operator supplies saved HTML / URLs.
- Claude vision for import matching — Phase X matches with the **local** CLIP classifier only; the
  existing `house_vision` Claude path (bulk-upload feature) is untouched, not reused here.
- A new heavy ML runtime dep — the CLIP classifier runs on the **existing** `onnxruntime` (core
  dep); no `torch`/`open_clip` added to the runtime path. The one-time CLIP→ONNX export is a
  dev/setup concern, not a shipped dependency. CPU-only; GPU not required.
- Fine-tuning the CLIP model or custom room taxonomy training — zero-shot over a fixed room-type
  label set only.
- Parsing non-Redfin listing formats (Zillow/MLS) — Redfin HTML + a generic photo-URL list only.
- Room `features` auto-population beyond what `house_vision` already returns.
- Editing/cropping downloaded photos; bulk re-matching after commit (re-run import instead).
- Backfilling `room_type`/`active` for pre-existing rooms beyond the migration default
  (`active = 1`, `room_type = NULL`).

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/toybox/db/migrations/0029_room_type_active.sql` | create | adds `rooms.room_type TEXT NULL` + `rooms.active INTEGER NOT NULL DEFAULT 1` | glob confirmed: rooms schema in `0001_initial.sql:44-50` has neither column; next free number 0024 (0029 after Phase W) |
| `src/toybox/api/rooms.py` | modify | `PATCH` accepts `room_type` + `active`; `GET`/list serialize both; add import endpoints (X5) | read confirmed: PATCH currently updates only `display_name`+`notes`; `upload-bulk`/`confirm-bulk` at `:538-702` is the staging template |
| `src/toybox/activities/content_resolver.py` | modify | `ResolvedRoom` + the room-selection query exclude `active = 0` from play | confirmed `ResolvedRoom` dataclass at `:143-149`; room rows feed generation — grep `available_rooms` / room SELECTs before landing |
| `src/toybox/ai/tools.py` | modify | `get_room` tool + room enumeration exclude `active = 0` (parent UI still lists) | confirmed `get_room` returns `{id,name,features,image_path}` at `:492-524` |
| `src/toybox/api/activities.py` | modify | the `available_rooms=room_names` builder in `_do_propose` filters `active = 0` | confirmed `_do_propose` passes `available_rooms` into the generator context (`:2600`); grep the room-name builder |
| `src/toybox/core/listing_parser.py` | create | pure: pasted HTML / URL list → `{room_counts, photo_urls}` (regex + stdlib `html.parser`) | n/a (new); no HTML-parser dep installed (use stdlib) |
| `src/toybox/core/room_naming.py` | create | pure: `{type: n}` → ordered proposed room names with per-type numbering | n/a (new) |
| `src/toybox/core/photo_fetch.py` | create | SSRF-guarded URL download → bytes; hard host allowlist + size cap, then hand to `storage/images.py` | n/a (new); `urllib` per `claude-auth.md` (no `requests`) |
| `src/toybox/core/room_match.py` | create | filename heuristic → local CLIP zero-shot fallback → best-guess room assignment (+ N/A below threshold); classifier injected for testing | n/a (new) |
| `src/toybox/ai/room_classifier.py` | create | local ONNX CLIP zero-shot classifier (`classify(image_bytes) -> {label: score}`) over the room-type label set, on the existing `onnxruntime`; lazy-loads model from `data/models/clip/`; `--download` CLI entrypoint vendors the model (mirror `audio/stt.py --download`) | confirmed `onnxruntime>=1.25` is a core dep (faster-whisper/silero); `audio/stt.py` is the lazy-load + `--download` precedent |
| `pyproject.toml` | (no runtime change) | CLIP runs on existing `onnxruntime`; no new runtime dep. CLIP→ONNX export is dev/setup-only (not added to the shipped deps) | confirmed onnxruntime already present |
| `data/models/clip/` | create (gitignored) | vendored ONNX CLIP image+text encoders + BPE vocab; fetched via `--download` | mirror `data/models/` (whisper) gitignore pattern |
| `.gitignore` | (verify) | ensure `data/models/clip/` (or `data/` blanket) is ignored | confirm `data/` ignore covers it |
| `src/toybox/storage/images.py` | (no change expected) | importer reuses `validate_upload`/`stage`/`commit_staging("rooms")` as-is | read confirmed: room subdir + dedup + sniff already support this |
| `frontend/src/parent/components/RoomImportPanel.tsx` | create | paste box + editable proposed-rooms table + commit | n/a (new) |
| `frontend/src/parent/components/RoomsManager.tsx` (or rooms tab) | modify | add room-type field + "stay out" toggle to room rows; entry point to import | grep the existing rooms management component before editing |
| `frontend/src/parent/App.tsx` | modify | wire import state + room-type/active edit callbacks | read confirmed: parent App is the state hub for management panels |
| `frontend/src/parent/api.ts` | modify | add `parseListing`, `commitRoomImport`, and `room_type`/`active` to the room PATCH method | read confirmed: api.ts holds the room CRUD methods |
| `frontend/src/shared/types.ts` | modify | codegen — room shape gains `room_type`/`active`; new import request/response models; run `python tools/gen_types_ts.py` | n/a (generated) |

**Downstream consumers of the `rooms` shape (grep-verify before landing X1):**
- Adding `room_type` + `active` is additive (active defaults 1) — existing reads don't break, but
  the play-time room selectors (`content_resolver` query, `get_room`, `_do_propose`
  `available_rooms`) must add `WHERE active = 1` or they'll cast "stay out" rooms into play. List
  every room-SELECT call site in X1's Verified notes (code-quality.md §1 — the toy `active`
  rollout is the precedent).

## 5. New Components

- **`core/listing_parser.py`** — `parse_listing(content: str) -> ParsedListing` where
  `ParsedListing = {room_counts: dict[str,int], photo_urls: list[str]}`. Detects whether `content`
  is HTML (regex/stdlib `html.parser`) or a plain URL list. Extracts bed/bath counts from the
  listing's stats block and any room-type mentions; collects `<img src>` / og:image / plain photo
  URLs. **Pure + offline + injection-safe**: it only reads text, never follows directives embedded
  in the HTML. Robust to missing fields (returns whatever it found; empty is valid).
- **`core/room_naming.py`** — `propose_rooms(counts) -> list[ProposedRoom]` producing
  per-type-numbered names (`Bedroom #1..#n`, `Bathroom #1..#n`, `Garage #1`, `Yard #1`, plus a
  `Playroom #1` heuristic when a bedroom surplus exists — operator-editable). Deterministic
  ordering.
- **`core/photo_fetch.py`** — `fetch_photo(url) -> bytes` with a **hard SSRF guard**: scheme ∈
  `{http,https}`, host matched against an allowlist (Redfin CDN domains, e.g.
  `*.cdn-redfin.com` / `ssl.cdn-redfin.com`, configurable via `TOYBOX_PHOTO_FETCH_ALLOWLIST`),
  rejects private/loopback/link-local IPs, caps response bytes at the image size cap, and times
  out. Returns bytes for `validate_upload`. Raises a typed error with a stable code
  (`photo_fetch_blocked`) on any guard failure.
- **`ai/room_classifier.py`** — local CLIP zero-shot classifier. `classify(image_bytes) ->
  dict[label, score]` scores a photo against the room-type label set (bedroom, bathroom, kitchen,
  living room, garage, yard, playroom, dining room, office, …) via CLIP image↔text cosine
  similarity, computed with ONNX sessions on the existing `onnxruntime`. Model (image encoder +
  text encoder ONNX + CLIP BPE vocab) lazy-loads from `data/models/clip/` on first call (so import
  is cheap); a `--download` CLI entrypoint vendors it once (mirrors `audio/stt.py`). The ONNX
  session + tokenizer are injectable so unit tests run with a fake encoder — **no model download
  needed for the test suite / `/build-phase`**. CPU-only, offline.
- **`core/room_match.py`** — `match_photo(filename, image_bytes, *, classifier) -> RoomGuess` where
  `RoomGuess = {room_type, confidence, source: "filename"|"clip"|"none"}`. Tries the filename
  heuristic first (keyword map: bed/bath/kitchen/living/garage/yard/play/dining/office); on a weak
  filename, calls the injected `classifier` and takes the top label **if its score ≥ a confidence
  threshold**, else returns `none` → N/A. Pure assignment logic is unit-testable with a fake
  classifier injected (no model).
- **Import endpoints (in `api/rooms.py`)** — `POST /api/rooms/import/parse` (paste content →
  `{proposed_rooms, photo_urls}`, no side effects) and `POST /api/rooms/import/commit` (downloads
  via `photo_fetch`, validates/stages via `storage/images.py`, matches via `room_match`, inserts
  rooms in one transaction; returns the created set). Parent-scope only.
- **`RoomImportPanel.tsx`** — paste textarea → parse → editable table (name, type dropdown,
  matched-photo thumbnail with reassign/clear→N/A, "stay out" toggle) → commit.

## 6. Design Decisions

**Operator pastes saved HTML / URLs; no live scraping (chosen).** Redfin actively bot-blocks and
automated fetch of the live listing is a ToS gray area, so a URL-fetch would fail unpredictably.
The parser accepts a saved page's HTML or a pasted photo-URL list; the only network calls are
downloads of the explicitly-listed photo URLs (themselves SSRF-guarded). Rejected: backend URL
scrape (brittle, ToS).

**Matching uses a local ONNX CLIP zero-shot classifier (chosen).** Per the operator's decision,
photo→room matching is **on-device + offline**, not cloud. Implementation: a CLIP model exported to
ONNX and run on toybox's **existing `onnxruntime`** core dep (already powering faster-whisper +
silero-vad) — so no new heavy runtime dependency (no `torch`/`open_clip` in the shipped path; the
CLIP→ONNX export is a one-time dev/setup step). Zero-shot scores each photo against a fixed
room-type label set via image↔text cosine similarity; the filename heuristic runs first (cheap,
often sufficient), CLIP is the fallback, and a confidence threshold gates N/A. The model is vendored
under `data/models/clip/` via a `--download` entrypoint (the whisper-model pattern), lazy-loaded on
first use, and the encoder is injectable so the test suite + `/build-phase` run without it. Rejected:
**Claude vision** (not local, needs network — fails the operator's local-first requirement);
**torch/open_clip runtime dep** (heavy, GPU-oriented, larger surface than onnxruntime-CLIP needs);
**deferring local matching** (the operator explicitly wants it now). Aligns with the project's
local-first posture (CLAUDE.md: "All on-device by default").

**CLIP model = ViT-B/32 ONNX, fetched via `urllib` (chosen).** The `--download` entrypoint pulls a
pre-exported ONNX CLIP — default **`Xenova/clip-vit-base-patch32`** (ONNX image encoder + text
encoder + tokenizer, purpose-built for `onnxruntime`, CPU-sized ~350 MB, permissive license) — via
`urllib` from pinned HuggingFace `resolve/main/...` URLs into `data/models/clip/` (no new pip dep;
matches the `urllib`-only rule; env-overridable `TOYBOX_CLIP_MODEL_URLS`). No CLIP→ONNX export is
shipped or run by toybox — we consume an already-exported ONNX model. *Operator may swap the model
via the env override if a different CLIP is preferred.* Rejected: `open_clip`/`transformers` runtime
export (heavy, needs torch); `huggingface-hub` dep (urllib suffices for pinned files).

**Room-type label set is one source of truth (chosen).** The room-type vocabulary (bedroom,
bathroom, kitchen, living room, garage, yard, playroom, dining room, office) is defined **once** —
`ROOM_TYPES` in a leaf module (e.g. `core/room_types.py`) — and imported by (a) the filename keyword
map, (b) the CLIP zero-shot label set, and (c) the `room_type` column's accepted values. Per
code-quality.md §2 (one source of truth for data-shape constants); a regression test asserts the
three consumers reference the same constant, so they can't drift.

**"Stay out" = `rooms.active = 0`, copying the toy contract (chosen).** Rather than a new
"excluded" concept, reuse the proven toy `active` semantics: the room stays in the catalog and the
parent UI, but every play-time selector filters `active = 0`. Consistent mental model, and the toy
rollout already proved the filtering call-site list. Rejected: a separate `excluded` column
(redundant with active).

**Photo download is SSRF-guarded as a hard runtime control, not documentation (chosen).** Per
`security.md` ("pair unsafe configs with startup safety checks" / treat external content as data),
`photo_fetch` enforces scheme + host-allowlist + private-IP rejection + size cap with a stable
`photo_fetch_blocked` code — the importer cannot be made to fetch an internal URL by a crafted
pasted page. Rejected: trusting parsed URLs (SSRF).

**Staged parse → review → commit, mirroring `upload-bulk`/`confirm-bulk` (chosen).** The parse
endpoint has no side effects; the parent edits the proposal; commit does the downloads + inserts
atomically. This reuses the established two-phase room-ingest UX and keeps a bad parse from writing
anything. Rejected: one-shot import (no review, hostile when the parse is wrong).

**This feature is request-driven, not autonomous.** Parse and commit run inside parent-invoked
HTTP requests that complete and return — no daemon/scheduler. It IS a producer→consumer pipeline
(parse → fetch → validate → match → persist → play-selection), so a no-mock smoke gate (X7) is
required before UAT.

## 7. Build Steps

### Step X1: Room schema — type + "stay out" toggle
- **Problem:** Add `rooms.room_type TEXT NULL` + `rooms.active INTEGER NOT NULL DEFAULT 1`. Wire
  both into `PATCH /api/rooms/{id}` and room GET/list serialization. Exclude `active = 0` rooms
  from every play-time selector (generation `available_rooms`, `get_room` tool, `content_resolver`
  room query) while the parent UI still lists them. Add the room-type field + "stay out" toggle to
  the parent rooms management UI.
- **Issue:** #255#
- **Flags:** --reviewers code
- **Produces:** `db/migrations/0029_room_type_active.sql`, modified `api/rooms.py`,
  `activities/content_resolver.py`, `ai/tools.py`, `api/activities.py` (room-name builder),
  modified rooms-management component + `App.tsx` + `api.ts`, regenerated `shared/types.ts`
- **Done when:** `uv run pytest` passes (PATCH sets/returns `room_type`+`active`; an `active = 0`
  room is excluded from `available_rooms` and `get_room` but present in `GET /api/rooms`);
  `grep -rn "FROM rooms" src/toybox` enumerated and each play-time selector confirmed to filter
  `active`; `npm run typecheck` + `npm run test` pass; the parent UI shows the type field + toggle
- **Depends on:** none
- **Status:** DONE (2026-06-20)

### Step X2: Listing parser + room-name generation
- **Problem:** Add `core/listing_parser.py` (`parse_listing(content) -> {room_counts, photo_urls}`,
  regex/stdlib only, treats HTML strictly as data) and `core/room_naming.py`
  (`propose_rooms(counts) -> [ProposedRoom]` with per-type numbering). Pure, offline, no network.
- **Issue:** #256#
- **Flags:** --reviewers code
- **Produces:** `core/listing_parser.py`, `core/room_naming.py`
- **Done when:** `uv run pytest` passes against a saved Redfin HTML fixture (asserts bed/bath
  counts + extracted photo URLs) AND a plain URL-list fixture; `propose_rooms({"bedroom":3,
  "bathroom":2})` yields `Bedroom #1..#3` + `Bathroom #1..#2`; malformed/empty input returns an
  empty parse without raising
- **Depends on:** none
- **Status:** DONE (2026-06-20)

### Step X3: Safe photo fetcher
- **Problem:** Add `core/photo_fetch.py` (`fetch_photo(url) -> bytes`) with a hard SSRF guard:
  scheme ∈ {http,https}, host allowlist (`TOYBOX_PHOTO_FETCH_ALLOWLIST`, default Redfin CDN),
  reject private/loopback/link-local IPs, byte cap = image size cap, timeout. Raises a typed error
  with stable code `photo_fetch_blocked`. Returns bytes ready for `storage/images.validate_upload`.
- **Issue:** #257#
- **Flags:** --reviewers code
- **Produces:** `core/photo_fetch.py`
- **Done when:** `uv run pytest` passes (allowed host returns bytes via a stubbed opener;
  `http://127.0.0.1/...`, a non-allowlisted host, a `file://` URL, and an oversize response each
  raise `photo_fetch_blocked`); no real network call in tests
- **Depends on:** none

### Step X4: Photo→room matching (local CLIP)
- **Problem:** Add the local CLIP matcher. (a) `ai/room_classifier.py` — an ONNX CLIP zero-shot
  classifier `classify(image_bytes) -> dict[label, score]` over the room-type label set, computed
  on the existing `onnxruntime` (image↔text cosine similarity). Lazy-load the model from
  `data/models/clip/` on first call; expose the ONNX session(s) + tokenizer as **injectable** deps
  so tests use a fake encoder with NO model file. Add a `--download` CLI entrypoint that fetches the
  pinned ONNX CLIP (default `Xenova/clip-vit-base-patch32`, via `urllib` from HuggingFace
  `resolve/main/...` URLs, env-overridable `TOYBOX_CLIP_MODEL_URLS`) into `data/models/clip/`
  (mirror `audio/stt.py`'s `--download`); ensure `data/models/clip/` is gitignored. Define the
  room-type vocabulary ONCE as `ROOM_TYPES` in a leaf module (e.g. `core/room_types.py`) and import
  it from the filename map + the CLIP label set + the `room_type` validator (code-quality.md §2;
  add a regression test asserting all three reference the same constant). (b) `core/room_match.py`
  (`match_photo(filename, image_bytes, *, classifier) -> RoomGuess`): filename keyword heuristic
  first; on a weak filename, call `classifier` and take the top label iff its score ≥ a confidence
  threshold, else `source="none"` → N/A. Classifier injected for testing.
- **Issue:** #258#
- **Flags:** --reviewers code
- **Produces:** `core/room_types.py` (`ROOM_TYPES` single source of truth), `ai/room_classifier.py`
  (+ `--download` entrypoint), `core/room_match.py`, gitignore entry for `data/models/clip/`
- **Done when:** `uv run pytest` passes WITHOUT any model download — using an injected fake
  classifier: `master-bedroom.jpg` → bedroom via filename (classifier not called); ambiguous
  filename + fake classifier returning a high score for "bathroom" → bathroom (`source="clip"`);
  ambiguous + all-scores-below-threshold → N/A (`source="none"`); the `room_classifier` ONNX
  load path is exercised with a stubbed `onnxruntime` session (no real model). `uv run mypy src` +
  `uv run ruff check .` clean. (The real model is downloaded + exercised at X8 UAT.)
- **Depends on:** none

### Step X5: Import API (parse → review → commit)
- **Problem:** Add `POST /api/rooms/import/parse` (pasted content → `{proposed_rooms, photo_urls}`,
  no side effects) and `POST /api/rooms/import/commit` (download via `photo_fetch`, validate/stage/
  commit via `storage/images.py`, match via `room_match`, insert the named rooms in one
  transaction; honor edited names/types/photo assignments/N/A and `active`). Parent-scope. Wires
  X2–X4 + X1's columns.
- **Issue:** #259#
- **Flags:** --reviewers code
- **Produces:** modified `api/rooms.py`, regenerated `shared/types.ts`
- **Done when:** `uv run pytest` passes (integration: parse a fixture → commit with stubbed
  `photo_fetch` returning local fixture bytes + an **injected fake `room_classifier`** (no model
  download) → rooms created with names/types/images; an N/A room commits with NULL `image_path`; a
  `photo_fetch_blocked` URL is skipped, not fatal; dedup via `find_dedup` honored); `npm run
  typecheck` passes
- **Depends on:** X1, X2, X3, X4

### Step X6: Parent import UI
- **Problem:** Add `RoomImportPanel.tsx`: paste textarea → call parse → editable proposed-rooms
  table (name, room-type dropdown, matched-photo thumbnail with reassign/clear→N/A, "stay out"
  toggle) → commit → refresh rooms list. Reachable from the rooms management tab.
- **Issue:** #260#
- **Flags:** --reviewers code
- **Produces:** `RoomImportPanel.tsx`, modified rooms-management component + `App.tsx` + `api.ts`
- **Done when:** `npm run typecheck` + `npm run test` pass (component test: paste → render proposed
  rooms → edit a name + clear a photo to N/A → commit fires `commitRoomImport` with the edited
  payload); `uv run pytest` unaffected
- **Depends on:** X5

### Step X7: Pipeline smoke gate
- **Type:** code
- **Problem:** No-mock end-to-end: migrate a fresh DB; parse a saved Redfin HTML fixture; commit
  with `photo_fetch` pointed at local fixture image files (real `storage/images.py` validation, no
  network). To stay model-free + autonomous, the fixture photos are named so the **filename
  heuristic** resolves them (e.g. `bedroom-1.jpg`), and one is named ambiguously to exercise the
  classifier path via an **injected fake `room_classifier`** (real CLIP model NOT required). Assert
  a named room set is created with matched photos, one N/A room, correct `room_type`/`active`; then
  assert an `active = 0` room is excluded from `available_rooms`/`get_room`. Surfaces
  parser→fetch→validate→match→persist→play-selection drift.
- **Issue:** #261#
- **Flags:** --reviewers code
- **Produces:** `tests/integration/test_phase_x_room_import_smoke.py` (+ fixture HTML + fixture
  images)
- **Done when:** `uv run pytest tests/integration/test_phase_x_room_import_smoke.py` completes one
  real cycle without exception and asserts the full chain above
- **Depends on:** X1, X5

### Step X8: Operator UAT
- **Type:** operator
- **Problem:** Validate room import end-to-end in the real parent UI — including the **real local
  CLIP model** (the test suite ran model-free; this is the first exercise of the actual classifier).
- **Issue:** #262#
- **Prereq:** run the model download once: `uv run python -m toybox.ai.room_classifier --download`
  (vendors the CLIP ONNX model into `data/models/clip/`). Confirm `data/models/clip/` populated.
- **Done when:** UAT checklist passes:
  1. Save a Redfin listing page (or copy its photo URLs); paste into the import panel; parse shows
     a sensible room breakdown + photo count
  2. Proposed rooms are named per type (Bedroom #1…, Bathroom #1…); names/types are editable
  3. Photos are best-guess matched; at least one mismatch can be reassigned or set to N/A
  4. A room with no good photo commits as N/A (no broken image)
  5. Commit creates the rooms; they appear in the rooms list with their type
  6. Toggling "stay out" on a room keeps it in the parent list but it no longer appears in proposed
     activities; untoggling restores it
  7. Pasting a page with a non-Redfin/private photo URL does not fetch it (skipped, not an error)
- **Depends on:** X6, X7

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Redfin markup drift | The HTML parser breaks when Redfin restructures the page | Parser is best-effort + returns partial results; the URL-list paste path is a stable fallback; counts/photos are operator-editable before commit |
| SSRF via pasted photo URLs | A crafted page points the fetcher at an internal/cloud-metadata URL | Hard allowlist + private-IP rejection + scheme check in `photo_fetch`, stable `photo_fetch_blocked` code, unit-tested with malicious URLs (X3) |
| Prompt injection in pasted HTML | Embedded `<system-reminder>`/"ignore instructions" text reaches the parser | Parser treats HTML purely as data (regex/extract only); the CLIP classifier only ever sees downscaled image bytes, never the page text (`security.md`) |
| CLIP misclassifies rooms | Photos land on the wrong room | Filename heuristic first; CLIP is a thresholded fallback (low score → N/A, not a wrong guess); every assignment is operator-reviewed + reassignable + N/A-able before commit |
| CLIP model not downloaded | `room_classifier` can't run; matching degrades | Code lazy-loads + raises a clear "run --download" error; matcher catches it → falls back to filename-only + N/A (never 500s commit). X8 prereq runs `--download`; test suite is model-free (injected fake) |
| CLIP model size / CPU latency | ONNX CLIP adds disk + per-photo CPU cost on an 8 GB box | Use a CPU-sized CLIP export (e.g. ViT-B/32 ONNX, ~hundreds of MB); classify the already-downscaled (≤1600) bytes; bounded by the ≤50-photo import cap; runs in the request, not a loop |
| Migration number collision with Phase W | Both phases unbuilt; numbers 0024–0028 reserved by W | This plan uses 0029; renumber to next free slot at build time if W hasn't landed (note in X1) |
| Photo dedup across import + manual uploads | The same photo imported twice creates duplicate rooms | Reuse `find_dedup(conn, "rooms", hash)`; on a dedup hit, skip the photo (room still created as N/A or linked to existing) |
| Offline at commit time | n/a for matching — CLIP is **local/offline** (the whole point of the local-CLIP choice). Only concern is the model not yet vendored (see "model not downloaded" above) | Filename heuristic + N/A still work model-less; CLIP works fully offline once downloaded |

**Resolved decisions (were open; committed at plan-wrap):**
- **Photo-fetch allowlist default:** `ssl.cdn-redfin.com` + `*.cdn-redfin.com` (Redfin's photo
  CDN), env-overridable via `TOYBOX_PHOTO_FETCH_ALLOWLIST` (comma-separated host patterns). X3
  ships this default; the operator widens it via the env var if Redfin changes CDN hosts.
- **Playroom heuristic:** none — `room_naming` names every bedroom `Bedroom #n` and the parent
  renames one to "Playroom" in the review table if they want one. No surplus-bedroom guessing (it
  was guesswork; the editable table makes a rename trivial). "Playroom" stays a valid `ROOM_TYPES`
  value for manual selection.

## 9. Testing Strategy

- **X1:** unit/integration for PATCH round-trip of `room_type`+`active`; a regression test that an
  `active = 0` room is absent from `available_rooms` + `get_room` but present in `GET /api/rooms`;
  grep-verify every `FROM rooms` selector (code-quality.md §1).
- **X2:** parser unit tests against a saved Redfin HTML fixture + a plain URL-list fixture +
  malformed/empty input; `room_naming` numbering tests.
- **X3:** SSRF guard table (allowed host ok; loopback / private IP / non-allowlisted host /
  `file://` / oversize all raise `photo_fetch_blocked`) using a stubbed opener — no real network.
- **X4:** matching tests, all model-free via an injected fake classifier (filename hit skips the
  classifier; ambiguous + fake high-score → `clip` guess; ambiguous + below-threshold → N/A); the
  `room_classifier` ONNX load/score path exercised with a stubbed `onnxruntime` session. No model
  download in CI.
- **X5:** integration parse→commit with stubbed `photo_fetch` (local fixture bytes through real
  `storage/images.py`); N/A commit → NULL image_path; blocked URL skipped; dedup honored.
- **X6:** component test (paste → edit → commit payload shape).
- **X7:** the no-mock smoke gate (Type: code) — the cross-module drift catch before UAT.
- **Regression watch:** additive `room_type`/`active` must not break existing room
  serialization/`get_room` tests; treat any test diff that narrows a room-response assertion as
  suspect (code-quality.md "audit wire shape").
- **End-to-end:** X8 operator UAT covers real listing paste, photo matching quality, N/A handling,
  and the "stay out" play exclusion in the live PIN-gated UI.
