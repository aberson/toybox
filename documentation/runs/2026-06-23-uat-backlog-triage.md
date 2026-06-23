# UAT backlog triage — what still needs a human (2026-06-23)

**Goal:** shrink the operator-UAT backlog (#223 bundle R/S/O/T/V/W/X + the separate P/Q work) to only the items that *genuinely* require a person, by mapping every checklist row to existing automated coverage.

**Method:**
1. A read-only fan-out (8 analyzers, one per backlog phase) classified each row as *already-tested / mechanically-checkable / vision-judgeable / genuinely-human*, citing the exact test that asserts the behavior.
2. The cited tests were then **run** to confirm they are green *now*: **159 backend** tests across 18 files (`152 passed` model-free + `7 passed` the W & X end-to-end smoke gates) + **802 frontend** vitest. All green. (A transient faulthandler dump when the full set ran concurrently with the operator's live app + the vitest run was a load/timeout artifact — every file passes when run sanely.)

**Headline:** of the 20 rows in the #223 bundle, **19 have their behavior asserted by green automated tests.** The remaining human work is not "re-verify behavior" — it is the irreducible *physical / sensory / subjective* layer.

## Per-row coverage

Codes: ✅ = behavior asserted by a green test · 👁 = needs a look at the rendered UI (uat-ui can pre-clear on desktop) · 🎤 = real voice/STT · 🔊 = audio listening · 🧒 = child engagement/subjective · 🖼 = GPU-render eyeball · 📝 = content-quality skim.

| Row | Claim | Coverage | Evidence (green now) | Residual human |
|---|---|---|---|---|
| R1 | No cadence controls; TriggerButton is prominent CTA | ✅ | `TriggerButton.test.tsx`; no `play_cadence*` in source | 👁 glance it reads as the primary CTA on iPad |
| R2 | Read-Me spoken-text truncates at word boundary | ✅ | `App.spoken-text-limit.test.tsx`, `ReadMeButton.test.tsx` | 🔊 (optional) hear a long step truncate |
| R3 | Q&A gating: kiosk Next hidden until parent approves/skips | ✅ | `test_activities_question.py` (advance→409, approve, WS), `StepCard.test.tsx`, `ActivityPanel.test.tsx` | 👁 see Next hide→reappear on approve |
| R4 | Activity search; "Try this"/"Play again" proposes pinned template | ✅ | `test_search_api.py`, `SearchPanel.test.tsx` | 👁 glance search UX on iPad |
| S1 | Persona-appropriate gradient on approval (≥2 personas) | ✅ | `App.persona-gradient.test.tsx`, `theming.test.ts` (5 distinct) | 👁 colours look right |
| S2 | Avatar animates per step; changes on advance; no strobe | ✅ | `App.avatar-animation.test.tsx`, `PersonaAvatar.test.tsx`; safe durations in CSS | 👁 animates + no strobe over time |
| S3 | Step card readable at arm's length | 👁 | responsive `clamp()` sizing + scrim in `StepCard.tsx` (no behavior test) | 👁 legible at arm's length for ages 4 & 6 |
| O1 | Play tab shows the 5 sub-tabs (exact labels/order) | ✅ | `App.tab-migration.test.tsx` | **none** |
| O2 | Each tab filters; Transcriptions unchanged | ✅ | `PlayQueueList.test.tsx`, `App.tab-migration.test.tsx` | **none** |
| T1 | Offline catalog browse + chip filters, no backend AI | ✅ | `test_catalog_api.py` (0 AI calls), `CatalogPanel.test.tsx` | 👁 layout glance |
| V1 | Toy-action sprites render; no flashing; reduced-motion respected | ✅ | `ToyActionSprite.test.tsx` (+ `prefers-reduced-motion` CSS) | 👁 render + motion smoothness on device |
| W1 | Involvement + Complexity dials persist (stubs) | ✅ | `test_parent_involvement*.py`, `test_game_complexity*.py`, W-smoke round-trip | **none** |
| W2 | Linearity: linear→no choices; nonlinear→branching | ✅ | `test_propose_game_linearity.py`, W-smoke | 👁 (optional) confirm structure flips |
| W3 | Q&A grading off/lenient/strict; spoken-correct auto-advances | ✅ (logic) | `test_qa_grading.py`, `test_activities_qa_grading.py`, W-smoke | 🎤 **real mic/STT in the room** |
| W4 | Adventure beats reflect prior choices + spoken words; offline advances | ✅ (logic) | `test_adventure.py`, W-smoke (≥3 beats→reward) | 🎤 spoken words reflected + 👁 distinct beats |
| W5 | Boss fight: distinct non-flashing climax, boss-role toy, →reward; flag-off removes | ✅ | `test_boss_fight.py`, `StepCard.test.tsx` (banner, no-anim), W-smoke | 👁 banner + 🧒 resolves to reward |
| X1 | Paste listing → sensible breakdown + photo count | ✅ | room-import Playwright UAT **PASS** (2026-06-21) + `test_listing_parser.py` | **none** |
| X2 | Rooms named per type; names/types editable | ✅ | room-import Playwright UAT **PASS** + `test_room_naming.py` | **none** |
| X3 | Photos matched (filename + real CLIP); mismatch reassignable/N/A | ✅ (logic) | `test_room_match.py` (filename/CLIP/N/A paths) | 🖼 real-CLIP match quality on real photos (needs `--download` + a photo-bearing listing) — low priority |
| X4 | Commit persists; "stay out" (active=false) hidden on kiosk, still in manager | ✅ | `test_phase_x_room_import_smoke.py` (excluded from `resolve_rooms`/`get_room`; still in `/api/rooms`) | **none** |

**P/Q (separate hardware/operator work, not iPad UAT):**

| Row | Coverage | Residual human |
|---|---|---|
| P7 (#189) sprite smoke + IPA-scale tune | wiring ✅ (`test_image_gen_worker_e2e.py`) | 🖼 GPU render + eyeball identity/pose/quality; subjective scale-knob tune |
| P8 (#191) global regenerate flow + kiosk | ✅ (`test_toys_api_actions.py` + Playwright grid) | 👁 button/WS-badge flow (uat-ui can drive) + 🧒 kid recognises toy |
| Q7 (#202) generate + skim 221 song/joke entries | shape ✅; quality not | 📝 **human skim** for humour/accuracy/personification |
| Q8 (#205) render 118 MP3s via Coqui | file count = MECH | 🔊 listen to ~2 MP3s (after render) |

## The reduced human-only list

**Drop entirely** (behavior proven, residual *none*): **O1, O2, W1, X1, X2, X4.**

**One short "looks right on the iPad" glance** (behavior already proven — a quick look, not a re-test; uat-ui can pre-clear most of these on a desktop iPad-viewport so even this shrinks): R1, R3, R4, S1, S2, S3, T1, V1, W2, W5-banner.

**Genuinely needs a person — cannot be automated:**
1. 🎤 **Talk to it (W3, W4):** say a correct answer → it auto-advances; in an adventure, say a few words → reflected in the next beat. (Real microphone + STT.)
2. 🧒 **Kid pass (W4/W5 + general):** Child A & Child B each play one adventure — engagement, fork choices, boss fight resolves to a reward, they recognise their toys.
3. 🖼 **P7 sprite eyeball:** after a GPU render, judge toy-identity / pose / quality.
4. 📝 **Q7 content skim** (103 songs + 118 jokes) + 🔊 **Q8 listen** to ~2 MP3s — both *after* the operator runs the generators/render. (File-count is mechanical.)
5. 🖼 **X3 real-CLIP spot-check** (low priority): with the model downloaded and a photo-bearing listing, sanity-check a few photo→room matches.

## Next lever (optional): uat-ui desktop vision pass

The `uat-ui` skill (Playwright drive + vision judge, isolated throwaway DB) can convert the **👁 glance** rows into desktop vision-judged PASS/FAIL verdicts, leaving the human only the true-hardware bits (Guided Access, touch, kid eyes). It needs: (a) the operator's live M2 app on :8000/:4000 taken down first (uat-ui must own the ports — wrong-PIN attempts on the real DB lock the parent account), and (b) authoring a parent-UI flow + a kiosk-visual flow (today the harness only ships the proven `room-import` flow).

**Status:** triage + empirical test confirmation complete. The uat-ui visual pass is not yet run (pending operator go-ahead + port release).
