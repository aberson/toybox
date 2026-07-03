# Phase Z — Persona voices (server-rendered neural TTS + voice wire-through)

## 1. What This Feature Does

Gives each kiosk persona a genuinely distinct, natural-sounding voice and makes the words the
kiosk speaks match the words on screen. Today every persona speaks with the identical default
browser `speechSynthesis` voice ("the robot narrator") for two reasons: (a) the per-persona
`voice_profile` authored in the persona library never rides the wire (the backend SELECT omits
the column, acknowledged as a K8-era TODO in `persona-voice.ts`), and (b) iPad Safari's Web
Speech API cannot do voice identity at all — premium/Siri voices are walled off from web
content and named-voice selection silently no-ops (verified in the 2026-07-03 voice survey).
Phase Z fixes (a) as a quick-win slice, then replaces the primary speech path with
**Kokoro-82M neural TTS rendered on the backend** (Apache-2.0, CPU-in-process via the
already-core `onnxruntime`), served as cached WAV clips through the existing static-mount +
`HTMLAudioElement` pattern, with Web Speech retained as the offline/missing-clip fallback.
It also fixes the operator-reported spoken-vs-screen mismatch: a 157-char step body was cut to
"…What does Miss" by the Phase R spoken-text-limit's word-boundary truncation at limit 150.
Neural clips always speak the full visible text; the fallback path's truncation becomes
sentence-boundary-aware.

Triggering event: operator request 2026-07-03 ("attach different voices to personas; tired of
the robot narrator voice") + the observed mid-sentence cutoff.

## 2. Existing Context

- **Persona voice plumbing (Phase K K1/K8), built on both ends but never connected.** All 4
  library personas author distinct `{rate, pitch}` in `src/toybox/personas/library/*.json`
  (wizard 0.9/0.7, princess 1.0/1.4, detective 1.1/0.9, periodic_table 1.2/1.0); migration 0014
  added the `personas.voice_profile` JSON TEXT column; the loader upserts it
  (`src/toybox/personas/loader.py:121`, JSON wins over DB on every migrate). Pydantic
  `VoiceProfile` (`src/toybox/personas/models.py:81`, frozen, `extra="forbid"`, codegen'd to
  `frontend/src/shared/types.ts` per invariant 9) + `parse_voice_profile` (models.py:145, no
  production caller yet). Kiosk chain is live: `persona-voice.ts getVoiceProfile()` reads
  `activity.metadata.persona.voice_profile` → `StepCard.tsx:418` resolves once per render →
  threads to every speech surface. The break: `_pick_random_library_persona`
  (`src/toybox/api/activities.py:1729-1749`) SELECTs only
  `id, display_name, archetype, avatar_image_path`.
- **Persona wire envelope.** Built ONLY by `_pick_random_library_persona`, spliced verbatim at
  the 3 propose sites (activities.py:2149 standalone, :2291 adventure, :2665 template);
  `_row_to_response` and `_emit_state` pass `metadata.persona` through untouched to REST + WS.
  When `body.persona_id` is supplied (pinned persona), `persona_meta` stays `None` — no
  envelope at all (root cause of the known letter-avatar fallback). The listening-trigger path
  (`_persist_dispatcher_activity`, `src/toybox/main.py:863`, persona lookup :939-951) builds a
  display_name-only `persona_meta` consumed ONLY by `_build_persona_reasoning` (:952) — it
  never writes `metadata["persona"]` at all, so trigger-originated activities have no persona
  envelope today.
- **Kiosk speech surfaces (all Web Speech today).** Tap-triggered: `ReadMeButton` (full step
  body), `ChoiceReadButton` (choice label; shares `truncateAtWordBoundary` from
  ReadMeButton.tsx:49), `ClickableText` (single tapped word). Auto-play: `JokeStep` only
  (setup on mount, punchline after 1.5s). `RewardStep.tsx:52` hardcodes a duplicate
  `DEFAULT_VOICE_PROFILE` instead of threading StepCard's persona-resolved one.
  `tts.ts` handles iOS gesture unlock + the #207 cancel-race guard.
- **Audio serving/playback infra to reuse.** Songs: pre-rendered mp3s → `data/songs/audio/` →
  `app.mount("/api/static/songs/audio", StaticFiles(..., check_dir=False))`
  (`src/toybox/app.py:184-188`) → `SongPlayer.tsx` HTMLAudioElement with a `blocked` state
  (autoplay-policy rejection surfaces a manual Play button = the iOS gesture) and 404-grace
  degrade. Caution: the songs URL prefix is duplicated across `api/activities.py:1955` and
  `activities/interjection.py:79` — a known two-sources-of-truth wart; Phase Z must keep ONE
  constant.
- **Background-work-at-approve precedent.** Phase S S2: `post_approve` calls
  `_annotate_and_persist_step_animations(...)` (activities.py:3047) to annotate step metadata
  and persist BEFORE the WS broadcast. Phase Z's clip-enqueue hook mirrors this shape.
  Adventure beats (Phase W W4) are generated **during play**, persisted via
  `_insert_adventure_beat` (activities.py:4726) — beat clips can only be enqueued there, so a
  clip may not exist when the child first sees a step. Fallback is structurally required.
- **Deps/hardware.** `onnxruntime>=1.25.1` (CPU) is a core dep (`pyproject.toml:16`); the
  `image_gen` optional-extra + cu124 index pattern exists for torch. Host GPU is an 8 GB
  RTX 4070 shared with SD 1.5 — Phase Z deliberately stays off it (CPU synth, decided
  2026-07-03). Kokoro-82M: ~300 MB ONNX + voices bin, ~20 US-English voices with published
  grades, RTF ≈ 1.1 on CPU (RTF = real-time factor: seconds of compute per second of audio;
  a 2-sentence step renders in ~5-8 s in the background).
- **Offline scripts idiom.** `scripts/batch_scenes.py` (skip-existing, `--force`, `--dry-run`,
  per-item exception isolation); `scripts/generate_song_corpus.py` (lazy heavy imports so
  `--help` works without the dep). Model download CLI idiom: `python -m toybox.audio.stt
  --download`, `room_classifier --download`.

## 3. Scope

**In:**
- Z1 quick win: `voice_profile` on the wire (random + pinned + listening-trigger paths),
  RewardStep profile threading — distinct rate/pitch on the device-voice path immediately.
- Sentence-boundary-aware truncation for the Web Speech fallback (fixes "What does Miss").
- Kokoro-82M engine module (CPU in-process, provider seam for a later GPU flip), `tts`
  optional extra, model `--download` CLI, capability probe + graceful degradation.
- `neural_voice` field on `VoiceProfile` (persona → Kokoro voice id), library JSON casting
  defaults, audition script + operator listen-and-remap step.
- Clip cache (`data/tts/`), background synth worker, enqueue at approve / adventure-beat
  insert / joke-song insert / reward resolve, `spoken_audio_url` step-metadata wire shape,
  static mount.
- Kiosk clip playback for step bodies + jokes + choice labels (shared gesture-unlocked
  audio element), Web Speech fallback on missing clip/404/flag-off/offline.
- `neural_voice_enabled` parent feature flag (default ON).
- Real-engine smoke gate + iPad UAT.

**Out (explicitly):**
- Per-word taps (`ClickableText`) stay Web Speech — pre-rendering every tappable word is
  combinatorial; single words are the least-robotic case (decided 2026-07-03).
- Parent-UI voice picker / persona CRUD API (no personas endpoint exists; voice casting is
  library-JSON-managed like avatars and gradients).
- Chatterbox voice cloning (MIT, 10s-clip character voices) — tier-2 follow-up phase; GPU-only
  batch generator in the `batch_scenes.py` idiom. Mentioned for roadmap, not scoped here.
- Song corpus re-render (existing Coqui mp3s untouched).
- Removing the spoken-text-limit setting (it keeps meaning for the fallback path).

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/toybox/api/activities.py` | modify | Z1: `_pick_random_library_persona` SELECT + decoded `voice_profile` splice; new `_hydrate_persona_meta_by_id` for pinned path; Z4: enqueue hooks in `post_approve`, `_insert_adventure_beat`, insert-joke/song, reward resolve; `spoken_audio_url` step-metadata annotation | grep `_pick_random_library_persona`: 1 def (:1729) + 3 callers (:2047, :2234, :2405). Pinned-path sites = same 3 propose fns. `post_approve` :2932 (S2 hook at :3047 is the pattern); `_insert_adventure_beat` :4726 |
| `src/toybox/main.py` | modify | Z1: `_persist_dispatcher_activity` (:863, lookup :939-951) additionally writes a full `metadata["persona"]` envelope incl. voice_profile (today it feeds only `persona_reasoning` — no envelope exists on the trigger path) | agent-verified 2026-07-03: `persona_meta = {"display_name": ...}` :951 → `_build_persona_reasoning` :952 only; single site |
| `src/toybox/personas/models.py` | extend | Z3: `VoiceProfile.neural_voice: str \| None` (extra="forbid" makes this additive-safe; no SQL migration — JSON column) | grep `VoiceProfile\|parse_voice_profile` in src: models.py def, `personas/__init__.py` re-export, migration 0014 comment, 0 API callers today. Codegen ripples to `frontend/src/shared/types.ts` via pre-commit (invariant 9) |
| `src/toybox/personas/library/*.json` + `_schema.json` | extend | Z3/Z7: `voice_profile.neural_voice` casting defaults; schema allows the new key | 4 persona JSONs, all with `{rate, pitch}` only; `_schema.json:102-124` `additionalProperties=false` must add the property |
| `pyproject.toml` | extend | Z3: `[project.optional-dependencies] tts = ["kokoro-onnx", "soundfile"]` + mypy override for untyped deps | image_gen extra precedent :23-35; mypy overrides list :95-115 |
| `src/toybox/app.py` | extend | Z4: `/api/static/tts` mount (`check_dir=False`) + worker startup/shutdown wiring | songs mount precedent :184-188; ImageGenWorker lifecycle precedent |
| `frontend/src/child/persona-voice.ts` | extend | Z5: `PersonaMetadata.voice_profile.neural_voice` passthrough; header comment's "backend does not yet splice" note deleted in Z1 | production caller: StepCard.tsx:418 only; tests persona-voice.test.ts (10 asserts on defaults) |
| `frontend/src/child/components/ReadMeButton.tsx` | modify | Z2: `truncateAtWordBoundary` → sentence-aware; Z5: prefer clip URL, fallback Web Speech | grep `truncateAtWordBoundary`: def :49, callers ReadMeButton.tsx:82 + ChoiceReadButton.tsx:63 (+ its tests). Both callers updated together |
| `frontend/src/child/components/ChoiceReadButton.tsx` | modify | Z2 shared truncation; Z5 choice-label clips | see above (comment at ChoiceReadButton.test.tsx:170 pins the shared-truncation contract) |
| `frontend/src/child/components/JokeStep.tsx` | modify | Z5: setup/punchline clips with sequenced playback, Web Speech fallback (preserve #207 cancel semantics) | speak() callers: autoplay :76/:87, replayJoke :177-190; mounts from StepCard :596-602/:871-878 + RewardStep :166-171 |
| `frontend/src/child/components/RewardStep.tsx` | modify | Z1: delete duplicate `DEFAULT_VOICE_PROFILE` (:52), thread StepCard's persona-resolved profile | grep `DEFAULT_VOICE_PROFILE`: canonical export persona-voice.ts:61 + duplicate RewardStep.tsx:52/:169. One-source-of-truth rule: add identity regression test |
| `frontend/src/child/components/StepCard.tsx` | extend | Z5: thread clip URLs + persona profile to speech surfaces (incl. RewardStep) | voiceProfile resolved :418; ReadMeButton mount :914-921 (agent-corrected 2026-07-03; :879-886 is the waiting-for-parent div), JokeStep :597-601, JokeReadMeButton :906-912, ChoiceButton voiceProfile :808 |
| `frontend/src/shared/types.ts` | regenerate | Z3 codegen (invariant 9 pre-commit) | generated file; no hand edits |
| tests (backend) | extend | 9 existing files touch persona/voice surfaces: `test_phase_k_smoke`, `test_persona_library_phase_k_attrs`, `test_persona_models`, `migrations/test_0014_*`, `test_propose_roles`, `test_activities_recast`, `test_active_surfaces`, `test_phase_n_smoke`, `fixtures/personas/role_weighted.json` | grep'd `voice_profile\|_pick_random_library_persona\|metadata["persona"]` in tests/ — per the grep-scaffolding rule, budget for envelope-shape assertions in these, not just new tests |

New files: `src/toybox/tts/` (engine, cache, worker, `__main__` download CLI),
`scripts/batch_tts_audition.py`, `frontend/src/child/clip-audio.ts`, per-flag settings module +
migration seed for `neural_voice_enabled` (via the `add-parent-feature-flag` recipe), tests.

## 5. New Components

`VoiceProfile` shape after Z3 (producer: `src/toybox/personas/models.py:81`; codegen'd to
`frontend/src/shared/types.ts`):

| field | type | note |
|---|---|---|
| rate | float | required when profile non-null; ge 0.5, le 2.0 (Web Speech fallback only) |
| pitch | float | required when profile non-null; ge 0.0, le 2.0 (Web Speech fallback only) |
| voice_name | str \| None | optional, 1-128 chars; legacy Web Speech named-voice hint |
| neural_voice | str \| None | NEW (Z3): Kokoro voice id, e.g. `am_michael`; None → default `af_heart` |

Per-step clip metadata keys (written into step `metadata_json` at enqueue time, Z4): plain
steps get `spoken_audio_url`; joke-kind steps (incl. reward jokes) get
`spoken_audio_setup_url` + `spoken_audio_punchline_url`. All values are
`/api/static/tts/<voice>/<sha16>.wav` paths derived via `clip_url()` — the kiosk never
computes hashes.

- **`src/toybox/tts/engine.py`** — Kokoro-82M wrapper behind a provider seam
  (`synthesize(text, voice) -> bytes` WAV 24 kHz mono). Lazy model load on first call;
  `is_tts_capable()` probe (deps importable + model files present); `TOYBOX_TTS_STUB=1` test
  mode writing a tiny valid WAV (mirrors `TOYBOX_IMAGE_GEN_STUB`). CPU
  `onnxruntime` session; flipping to GPU later is a provider-config change, not a refactor.
- **`src/toybox/tts/__main__.py`** — `python -m toybox.tts --download` fetches
  `kokoro-v1.0.onnx` + voices bin to `data/models/tts/` (idiom: `stt --download`).
- **`src/toybox/tts/cache.py`** — clip store `data/tts/<voice>/<sha256(text)[:16]>.wav` under
  `TOYBOX_DATA_DIR`; ONE `TTS_AUDIO_URL_PREFIX` constant (producers import it — do not repeat
  the songs two-constants mistake); `clip_url(voice, text)` + `clip_path(...)` pair.
- **`src/toybox/tts/worker.py`** — single asyncio background worker draining a synth queue
  (ImageGenWorker shape, no breaker needed — failures just leave the fallback in place);
  enqueue is fire-and-forget and never blocks the request path; skip-if-exists.
- **`frontend/src/child/clip-audio.ts`** — one shared `HTMLAudioElement` for voice clips,
  primed at the PIN-gate gesture alongside `sfx.ts unlockAudio()` (iOS unlock is
  per-element); `playClip(url) -> Promise` rejecting on 404/decode so callers fall back to
  `tts.ts speak()`.
- **`scripts/batch_tts_audition.py`** — renders one sample line per candidate Kokoro voice +
  the current per-persona casting into `data/tts/audition/` for the operator to listen.
- **`neural_voice_enabled`** — parent feature flag (default ON), per-flag settings module +
  kiosk boot fetch, via the project `add-parent-feature-flag` recipe.

## 6. Design Decisions

- **Kokoro-82M, CPU in-process** (operator-confirmed 2026-07-03). Apache-2.0; clearly more
  natural than Piper in 2026 comparisons; ~20 distinct en-US voices is enough to cast 4
  personas with headroom. CPU keeps the 8 GB VRAM budget untouched for SD 1.5 and avoids
  onnxruntime-gpu dependency churn; RTF ≈ 1.1 is fine because the primary path is background
  pre-render, not tap-time synth. Alternatives: Piper (fastest, but the most robotic — defeats
  the purpose; maintained fork is GPL-3.0), GPU torch Kokoro (0.3 s/sentence but VRAM
  contention), XTTS-v2/F5-TTS (non-commercial weights; repo is public), Web Speech tuning only
  (cannot change voice identity on iPadOS — dead end).
- **Clips are pre-rendered + cached, URL persisted in step metadata, 404 = fallback.**
  `spoken_audio_url` (and joke `setup/punchline` pair) is written into step `metadata_json` at
  enqueue time (S2 persist-then-broadcast pattern), so the kiosk never derives cache keys
  client-side (wire-shape rule: the producer publishes, the consumer reads). A not-yet-rendered
  clip 404s → kiosk falls back to Web Speech (SongPlayer 404-grace precedent). This makes
  approve-time latency zero and adventure-beat clips best-effort by construction.
- **Enqueue points** (hook lines agent-verified 2026-07-03): `post_approve` (all step bodies +
  choice labels + question text), `_insert_adventure_beat` (beat + boss-beat bodies at
  generation time), parent joke/song insert (`_parent_insert_finish` :3695 →
  `_insert_interjection_step_row` :3867, which already writes per-step `metadata_json`), and
  reward resolve (`_insert_reward_step_as_current` :5346 — enqueue between the
  `resolve_reward` call :5433 and the step INSERT :5459-5472; setup/punchline metadata built by
  `_build_reward_step_metadata` :5246). Never in the propose path — proposals are speculative
  and most are dismissed.
- **Spoken-vs-screen contract** (operator-confirmed): neural clips ALWAYS render the full
  visible text — the main path can never mismatch. The parent spoken-text-limit setting keeps
  meaning only for the Web Speech fallback, whose truncation becomes sentence-boundary-aware
  (last `.`/`!`/`?` at or below the limit; word-boundary fallback for a first sentence longer
  than the limit; `…` retained).
- **Voice casting lives in the persona library JSONs** (`voice_profile.neural_voice`), synced
  by the existing loader upsert. No new storage, no CRUD API; remapping = edit JSON +
  `python -m toybox.db.migrate`. Defaults: Marvelous→`am_michael`, Princess Lyra→`af_bella`,
  Inspector Pip→`am_puck`, Professor Iridia→`bf_emma`; audition step lets the operator remap
  before UAT. Personas without `neural_voice` (custom/future) fall back to a default voice
  constant (`af_heart`, top-graded).
- **One shared kiosk audio element** for clips, unlocked at the PIN gesture — avoids iOS
  per-element unlock (the sfx.ts lesson) without adding a per-clip manual-Play tap. If the
  unlock proves flaky on real hardware, the SongPlayer blocked-state button is the fallback UX,
  validated at UAT.
- **Not a scheduled/always-on system**: the worker only drains request-triggered enqueues
  inside the server process, so the autonomous-behavior observation trigger does not fire;
  end-to-end validation is the Z8 real-engine smoke gate + Z9 UAT.

## 7. Build Steps

<!-- autofix-applied: 2026-07-03 -->
### Step Z1: Voice-profile wire-through (quick win)
- **Problem:** `voice_profile` never rides the wire: add the column to `_pick_random_library_persona`'s SELECT and splice a DECODED object (via `parse_voice_profile(...).model_dump(exclude_none=True)`; the kiosk's typeof-number guard silently rejects a raw JSON string). Add `_hydrate_persona_meta_by_id` and use it in all 3 propose paths when `body.persona_id` is supplied (fixes the pinned-persona missing-envelope bug = letter-avatar fallback too), and in `_persist_dispatcher_activity` (`main.py:863`) — which today builds display_name-only `persona_meta` for `persona_reasoning` and writes NO `metadata["persona"]` — write the full envelope there. Frontend:
- **Type:** code delete `RewardStep.tsx:52`'s duplicate `DEFAULT_VOICE_PROFILE`, import the canonical one, thread StepCard's persona-resolved profile into RewardStep's JokeStep mount; add an identity regression test (one-source-of-truth rule). Integration test through the production caller: propose (random AND pinned) → GET + WS payload → `metadata.persona.voice_profile` is an object with numeric rate/pitch.
- **Issue:** #3
- **Flags:** --reviewers code
- **Produces:** distinct per-persona rate/pitch on the device voice; pinned personas get avatar + voice envelope; wire-shape integration tests
- **Done when:** pytest wire-shape tests pass (random + pinned + trigger paths); vitest RewardStep threads persona profile; full gates green
- **Depends on:** none
- **Status:** DONE (2026-07-03)

<!-- autofix-applied: 2026-07-03 -->
### Step Z2: Sentence-boundary-aware fallback truncation
- **Type:** code
- **Problem:** `truncateAtWordBoundary` (ReadMeButton.tsx:49) cuts mid-sentence ("…What does Miss" at limit 150 on a 157-char body). Replace with sentence-aware truncation: cut at the last `.`/`!`/`?` at or below the limit; fall back to word boundary when the first sentence exceeds the limit; keep `…` and the limit=0/short-text passthroughs. Update both callers (ReadMeButton, ChoiceReadButton) and their tests, including the operator's exact 157-char example as a regression case.
- **Issue:** #4
- **Flags:** --reviewers code
- **Produces:** fallback speech never stops mid-sentence
- **Done when:** vitest includes the "What does Miss Maple think?" regression (spoken text ends at a sentence boundary); both caller test suites green
- **Depends on:** none
- **Status:** DONE (2026-07-03)

<!-- autofix-applied: 2026-07-03 -->
### Step Z3: Kokoro TTS engine substrate + voice-id schema
- **Type:** code
- **Problem:** Build `src/toybox/tts/` — engine.py (lazy-load Kokoro-82M via kokoro-onnx on CPU onnxruntime, provider seam, `synthesize(text, voice) -> WAV bytes`, `is_tts_capable()` probe, `TOYBOX_TTS_STUB=1` mode), `__main__.py --download` (models to `data/models/tts/`), `tts` optional extra in pyproject + mypy overrides. Extend `VoiceProfile` (pydantic + `_schema.json` + codegen) with optional `neural_voice`; author casting defaults in the 4 library JSONs (am_michael / af_bella / am_puck / bf_emma) + a module-level default-voice constant. Unit tests run against the stub (CI has no model files).
- **Issue:** #5
- **Flags:** --reviewers code
- **Produces:** `src/toybox/tts/{engine,__main__}.py`; `tts` extra; `neural_voice` end-to-end schema; casting defaults; stub-mode tests
- **Done when:** `uv run python -m toybox.tts --download --dry-run` prints targets without the extra installed (lazy-import idiom); stub synth round-trips a valid WAV in pytest; codegen hook regenerates types.ts with `neural_voice`; loader upserts the new JSON field (migration test extended)
- **Depends on:** none (parallel-safe with Z1/Z2)

<!-- autofix-applied: 2026-07-03 -->
### Step Z4: Clip cache + synth worker + enqueue hooks + wire shape
- **Type:** code
- **Problem:** Build `tts/cache.py` (keyed `data/tts/<voice>/<sha16>.wav`, single `TTS_AUDIO_URL_PREFIX` constant, `clip_url`/`clip_path`) and `tts/worker.py` (asyncio queue drain, skip-if-exists, fire-and-forget enqueue, capability-gated no-op when TTS unavailable). Mount `/api/static/tts` (`check_dir=False`) and wire worker lifecycle in `app.py`. Enqueue + persist `spoken_audio_url` (jokes: setup/punchline pair) into step `metadata_json` at: `post_approve` (S2 pattern, before WS broadcast), `_insert_adventure_beat`, parent joke/song insert (`_parent_insert_finish` :3695 → `_insert_interjection_step_row` :3867), and reward resolve (`_insert_reward_step_as_current` :5346, between `resolve_reward` :5433 and the INSERT :5459-5472). Persona voice comes from the activity's persona `neural_voice` (default constant when absent). Integration test through the production caller with the stub engine: approve a real activity → step metadata carries URLs → worker renders → GET the static URL returns 200 WAV.
- **Issue:** #6
- **Flags:** --reviewers code
- **Produces:** `tts/{cache,worker}.py`; static mount; enqueue hooks; `spoken_audio_url` wire shape; producer→consumer integration test
- **Done when:** integration test passes end-to-end on the stub; approve latency unchanged (enqueue is non-blocking, asserted); no URL-prefix constant duplication (grep gate in test)
- **Depends on:** Z3

<!-- autofix-applied: 2026-07-03 -->
### Step Z5: Kiosk clip playback with fallback
- **Type:** code
- **Problem:** Build `clip-audio.ts` (one shared HTMLAudioElement, primed at the PIN-gate gesture next to `sfx.ts unlockAudio()`, `playClip(url)` rejecting on 404/decode/interrupt). Update ReadMeButton, JokeStep (autoplay sequencing + replay; preserve #207 cancel semantics), ChoiceReadButton, and StepCard threading: when the step carries `spoken_audio_url` AND `neural_voice_enabled` is ON → play the clip with FULL text contract (no truncation); on any failure or missing URL → existing Web Speech path (Z2 truncation applies there only). Clip playback interrupts Web Speech and vice versa (single audio focus).
- **Issue:** #7
- **Flags:** --reviewers code
- **Produces:** `clip-audio.ts`; clip-first speech surfaces; fallback chain; vitest coverage incl. 404-fallback and joke sequencing
- **Done when:** vitest: clip preferred when URL present, Web Speech fallback on 404/flag-off/no-URL, joke setup→punchline order preserved on both paths; full gates green
- **Depends on:** Z4 (wire shape), Z2 (fallback truncation)

<!-- autofix-applied: 2026-07-03 -->
### Step Z6: `neural_voice_enabled` parent flag
- **Type:** code
- **Problem:** Add the `neural_voice_enabled` boolean parent feature flag (default ON) end-to-end via the project `add-parent-feature-flag` recipe: core module, per-flag settings router, app registration, migration seed, shared TS declaration, kiosk boot fetch + routing into the Z5 clip/fallback decision, parent Settings toggle, oracle/unit tests.
- **Issue:** #8
- **Flags:** --reviewers code
- **Produces:** flag end-to-end; parent can force the device voice
- **Done when:** flag OFF routes all surfaces to Web Speech (vitest); settings PUT/GET pytest; migration seed test
- **Depends on:** Z5

<!-- autofix-applied: 2026-07-03 -->
### Step Z7-prep: Audition batch script
- **Type:** code
- **Problem:** Author `scripts/batch_tts_audition.py` (batch_scenes.py idiom: skip-existing, `--force`, `--dry-run`, per-item exception isolation, lazy heavy imports): renders one fixed sample line per available Kokoro en voice plus the current 4-persona casting into `data/tts/audition/`, printing a listen-order manifest.
- **Issue:** #9
- **Flags:** --reviewers code
- **Produces:** `scripts/batch_tts_audition.py` + stub-mode wiring test
- **Done when:** `--dry-run` lists targets without model files; stub run writes N sample WAVs
- **Depends on:** Z3

### Step Z7: Operator voice audition + casting sign-off
- **Problem:** Operator installs the extra (`uv sync --extra tts`), runs `python -m toybox.tts --download`, then `scripts/batch_tts_audition.py`; listens to samples; either accepts the default casting or edits `voice_profile.neural_voice` in the library JSONs and re-runs `python -m toybox.db.migrate`. Record the final casting in this plan doc.
- **Type:** operator
- **Issue:** #10
- **Produces:** confirmed per-persona voice casting; real model files on the host. (If the operator remaps, the artifact is a mechanical <50-LOC value edit to `voice_profile.neural_voice` in the library JSONs — within the §22 inline-code-inside-operator-brief exception; the casting *defaults* are authored in Z3, a code step.)
- **Done when:** operator states casting is final; `data/models/tts/` populated
- **Depends on:** Z7-prep

### Step Z8: Real-engine smoke gate
- **Problem:** 60-second end-to-end run with REAL components, no stubs (data-pipeline smoke rule): start the backend with the tts extra installed, propose + approve one real activity, verify the worker renders real Kokoro WAVs into `data/tts/`, `curl` the `spoken_audio_url` returns 200 audio/wav, and one clip is audibly the cast persona voice. Deliverable is "one real cycle completes without crashing", not content quality.
- **Type:** operator
- **Issue:** #11
- **Produces:** smoke evidence (command transcript + one clip spot-check)
- **Done when:** approve → real clip served round-trip confirmed on the host
- **Depends on:** Z4, Z7

### Step Z9: iPad UAT
- **Problem:** On the kiosk iPad: (1) two different personas sound audibly different on Read Me; (2) spoken audio matches on-screen text verbatim on a long step (the Phase-R cutoff class is gone); (3) joke auto-play uses the persona clip voice; (4) with `neural_voice_enabled` OFF, device voice returns with sentence-boundary truncation; (5) adventure beats speak (clip when ready, fallback otherwise); (6) shared-element gesture unlock works from the PIN gate (no dead first tap); (7) choice-label read-aloud plays clips. File defects as issues; non-blocking cosmetics may fold into the next bundle.
- **Type:** operator
- **Issue:** #12
- **Produces:** UAT run doc under `documentation/runs/`
- **Done when:** all 7 checks PASS or defects filed + dispositioned
- **Depends on:** Z5, Z6, Z8

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| iOS shared-element gesture unlock | Priming one shared element at the PIN gesture may not unlock later `src` swaps on some iPadOS versions | Z5 keeps SongPlayer-style blocked-state manual-Play as the in-component fallback; Z9 check (6) validates on real hardware |
| kokoro-onnx dependency health | Wrapper is a small third-party MIT package; G2P (grapheme-to-phoneme text→sound conversion, via misaki with espeak-ng fallback) adds transitive deps | Pin versions in the `tts` extra; provider seam isolates the engine so swapping wrapper (or vendoring the ONNX call) is contained in engine.py |
| CPU synth latency vs. advance pace | Adventure beats render in ~5-8 s; a fast child can outrun the worker | 404→Web-Speech fallback is the designed behavior, not an error; if UAT shows it dominating, flip the provider seam to GPU in a follow-up |
| Clip cache growth | WAV @24 kHz mono ≈ 50 KB/s; long-lived households accumulate clips | Content-hash keying dedupes repeated text (templates repeat heavily); note a future LRU sweep as follow-up if `data/tts/` exceeds ~1 GB — not scoped |
| `extra="forbid"` on VoiceProfile | Old persisted envelopes replayed against new pydantic must not 422 | `neural_voice` is optional-with-default; Z3 adds a decode test for a pre-Z voice_profile JSON |
| Loader clobber | DB-side casting edits are overwritten by the loader upsert on migrate | By design — JSONs are the source of truth; documented in Z7's remap procedure |
| Worker silently dead | Enqueue no-ops → everything falls back to device voice and nobody notices | Worker logs render counts at INFO; Z8 smoke gate proves the real path; kiosk visibly differs (robot voice) which the household will notice |
| Uncommitted working-tree overlap (detected 2026-07-03) | `ReadMeButton.tsx` + `StepCard.tsx`(+test) are MODIFIED and `ChoiceReadButton.tsx`(+test) is UNTRACKED on master — these are Z2/Z5 targets; Z2's caller inventory assumes ChoiceReadButton is in git | **Pre-flight prerequisite:** operator dispositions (commit or revert) these files BEFORE `/build-phase` starts; worktree-based build-steps branch from committed state and would silently miss untracked/dirty edits |
| Phase E latent file overlap | Phase E Step 26 (E2, pending) adds a `local` extra to `pyproject.toml`; Z3 adds a `tts` extra | Different sections, trivially mergeable; no other Phase E overlap (its activities.py step E5 was superseded by Phase G) |

## 9. Testing Strategy

- **Unit (backend):** engine stub round-trip; cache keying/URL derivation (one constant);
  sentence-boundary truncation table incl. the 157-char regression; `VoiceProfile.neural_voice`
  validation + legacy-JSON decode; loader upsert of the new field.
- **Integration (backend):** wire-shape tests through the production caller for all three
  persona-envelope paths (random, pinned, listening-trigger) asserting `voice_profile` arrives
  as a typed object (code-quality "audit wire shape" rule — reviewers should treat any test
  diff that *relaxes* envelope assertions as suspect); approve→enqueue→render→serve round-trip
  on the stub engine; adventure-beat and reward-joke enqueue paths; non-blocking enqueue
  assertion. Existing suites likely touched: `test_phase_k_smoke`,
  `test_persona_library_phase_k_attrs`, `test_persona_models`, `test_propose_roles`,
  `test_activities_recast`, migration 0014 test (grep'd; expect envelope-shape assertion
  updates, and treat wholesale assertion rewrites as review flags).
- **Unit (frontend):** clip-audio fallback matrix (404 / decode error / flag off / no URL);
  joke sequencing on both paths; RewardStep persona-profile threading + DEFAULT_VOICE_PROFILE
  identity test; persona-voice passthrough of `neural_voice`.
- **End-to-end:** Z8 real-engine smoke (no mocks) then Z9 iPad UAT (7 checks). Web Speech
  behavior itself remains manually validated via `speech-test.html` (cannot run headless).
- **Gates:** full pytest + vitest + ruff + mypy strict + codegen drift check per step; test
  counts must not regress (baseline 2,670 pytest / 802 vitest at `f878eb7`).
