# toybox

AI assistant for play with children. Passive-listening home device that watches for play opportunities, suggests activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

> **This file is the index.** It carries the elevator pitch, current status, and pointers into [`plan/`](plan/) for the rest. Each sub-doc opens with a one-line "scope" hint so you can decide whether to load it.

## What this is

A local-first family-private system that:

1. **Listens** to ambient audio in the play area (single mic on the home machine).
2. **Detects** play opportunities through a curated NLP layer with an optional Claude escalation path.
3. **Proposes** structured activities to the parent — linear scripts or branching multi-choice flows (post-Phase G).
4. **Runs** approved activities on a child-facing kiosk app — persona avatar, step cards, sound effects, persona-specific toy action sprites (post-Phase F.5).
5. **Learns** from "this didn't work" parent feedback to avoid recurring flop patterns.

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth and the system degrades to a fully-offline mode without it.

**v1 shipped 2026-05-02** at the end of Phase A — closed-loop demo with a manual "trigger" button instead of a real mic; adult-only testing. The system has since shipped phases B (hearing), C (content), D (polish), iPad-Kiosk (PWA on iPad), F.5 (image-gen sprite pipeline), G (branching gameplay), H (parent UX revamp), I (transcript retention + display refresh), and J (autonomous play queue) and is in family-private testing on real children. **Concurrent post-v1 work:** Phase E (local model + tool-loop, IN FLIGHT — backend substrate carve-outs done). Phase K (roles + songs + jokes + voice) ✅ COMPLETE 2026-05-16. Phase L (rewards system) ✅ COMPLETE 2026-05-17 at master `5aaf8ed`. Phase M (content depth — Periodic Table + SEL) ✅ COMPLETE 2026-05-18 at master `a096e11`. Phase N (element_microgame template shape) ✅ COMPLETE 2026-05-19. Phase O (parent UX 5-tab refresh) + Phase P (toy image-gen IP-Adapter redo) code-shipped 2026-05-19; bundled iPad UAT pending. Phase Q (element-specific rewards) Q1-Q6 code-shipped 2026-05-20 at master `f486568`; halt at Q7 operator step (LLM-authored corpus).

## Status

| Phase | Goal | Status |
|-------|------|--------|
| **A** — closed-loop skeleton (v1 ship) | trigger → suggestion → approve → child runs activity → done | ✅ COMPLETE 2026-05-02 |
| **B** — hearing | audio capture → VAD (Voice Activity Detection) → faster-whisper → trigger registry → mode-aware Claude escalation | ✅ COMPLETE 2026-05-03 |
| **C** — content | toy/room/child ingestion + activity-quality eval scaffold + real catalog in generators | ✅ COMPLETE 2026-05-03 |
| **D** — polish | anti-signal feedback, parent PIN gate, transcripts, "why this?", metrics | ✅ COMPLETE 2026-05-03 |
| **iPad-Kiosk** | child kiosk on iPad PWA (Progressive Web App) | ✅ COMPLETE 2026-05-10 |
| **F.5** — sprite pipeline cartoon redo | SD 1.5 + LCM-LoRA (Latent Consistency Model + Low-Rank Adaptation) + Tier C composite | ✅ COMPLETE 2026-05-09 — all 5 steps shipped, [#61](https://github.com/aberson/toybox/issues/61) closed via F.5-5 soft-pass |
| **G** — branching gameplay | multi-option choice steps + variable-length activities (offline templates only) | ✅ COMPLETE 2026-05-10 — 200 branching templates (50 per intent) shipped via overnight 4-agent soak; iPad UAT PASS |
| **E** — local model + tool-loop (post-v1) | tool-loop refactor + locally-hosted SFT (Supervised Fine-Tuned) model swap-in for Claude OAuth | IN FLIGHT — backend substrate carve-outs done; local-runtime probe + benchmark work IN FLIGHT. Step 28 carve-out 2026-05-05 (commit `33a4b3c`: tool registry + ClaudeActivityGenerator wrapper + env-var dispatch + `is_local_capable()` stub). Step 27 (E3) backend carve-out 2026-05-13 (commit `4f735a0`: PII redactor `src/toybox/ai/redact.py` + migration 0013 `redact_for_sft` opt-out column + `eval_dump.py --sft-export` mode + `data/models/lora/REGISTRY.md` template + end-to-end smoke gate; see [`e3-backend-carveout-plan.md`](e3-backend-carveout-plan.md)). Step 25b 2026-05-13 (commit `2330de7`: probe CLI for local OpenAI-compat runtime, [#36](https://github.com/aberson/toybox/issues/36)). Step 25c-pre 2026-05-14 (commit `e90d027`: `local_benchmark` CLI for E1c, [#112](https://github.com/aberson/toybox/issues/112)). Phase-gate cut 2026-05-21: ≥50 SFT-filter-row gate dropped as not load-bearing; Step 27 free to resume when operator picks it up. **Phase K K6 + K15 touch `src/toybox/api/activities.py` — sequence merges with care.** |
| **H** — parent UX revamp (tabs + global banned themes) | parent app moves from panel-toggle nav to two-level tabbed shell (Play / Kids & Toyboxes / Settings); `banned_themes` promoted from per-child column to a single `settings.banned_themes_global` key (formalizes existing UNION-across-children runtime behavior) | ✅ COMPLETE 2026-05-10 — all 6 steps (H1-H6) shipped; iPad UAT PASS; [run doc](runs/2026-05-10-phase-h-uat.md); follow-up [#84](https://github.com/aberson/toybox/issues/84) for unrelated Phase G slot-fill defect surfaced during UAT |
| **I** — transcript retention + display refresh | configurable transcript retention (1m/3m/5m/10m/15m, default 1m) with backend sweep + filter-on-read; parent UI drops per-row delete (wipe-all stays) and adds local-timer fade-out animation as rows cross expiry | ✅ COMPLETE 2026-05-11 — all 5 steps (I1–I5) shipped; #86–#91 closed; iPad UAT PASS; [run doc](runs/2026-05-11-phase-i-uat.md); key shipped patterns: byte-identical ISO format pinning across pipeline/sweep/filter, `fadingIdsRef` mirror to keep `setInterval` cadence stable across fades, snap-to-nearest defensive `aria-pressed` for non-canonical retention values |
| **J** — autonomous play queue (cadence + transcript loop + queue UI) | parent **Play** surface becomes a scrolling queue fed by an autonomous cadence task + transcript-driven `on_intent` wire; user-tunable `play_target_depth` ∈ {1, 3, 5} and `play_cadence_seconds` ∈ {0, 10, 30, 60}; ActivityPanel pins as queue head when one is approved | ✅ COMPLETE 2026-05-14 — all 10 automated steps (J1-J10) shipped via overnight `/build-phase`; umbrella [#92](https://github.com/aberson/toybox/issues/92); J11 smoke + J12 iPad UAT PASS (operator-confirmed 2026-05-14); see [`play-queue-plan.md`](play-queue-plan.md) for full design |
| **K** — roles + songs + jokes + voice | toy role taxonomy (10 roles) + slot-fill engine + proposed-only recast; pre-rendered song corpus (Coqui TTS) + Web Speech joke corpus across 5 delivery surfaces (standalone / theme-tagged embedded / endings / parent-inserted / persona-or-character spontaneity); click-to-read on kiosk (word taps + watermarked Read Me button); 8 parent feature flags; 1000 existing templates backfilled via overnight soak | ✅ COMPLETE 2026-05-16 — K1-K18 all shipped; K1-K15 substrate 2026-05-15; K16 + K16b backfill brought catalog to 1000 templates (250 × 4 intents); K17 smoke gate green; K18 iPad UAT 12/14 PASS, 2 cosmetic defects filed as non-blocking follow-ups ([#137](https://github.com/aberson/toybox/issues/137) Read Me watermark on fork pages, [#138](https://github.com/aberson/toybox/issues/138) corpus-pool collision between embedded + ending pickers). Umbrella [#113](https://github.com/aberson/toybox/issues/113) ready to close. Full design in [`phase-k-plan.md`](phase-k-plan.md); UAT run doc: [`runs/2026-05-16-phase-k-uat.md`](runs/2026-05-16-phase-k-uat.md). **Phase K6 + K15 touched [activities.py](../src/toybox/api/activities.py); Phase E sequencing already accounted for in the row above.** |

## Stack

| Layer | Tool | Why |
|-------|------|-----|
| Backend | Python 3.12 + FastAPI | dev/ standard; async-native; ws built-in |
| ASR (Automatic Speech Recognition) | faster-whisper (`small`) | local STT (Speech-To-Text); GPU when available, CPU fallback |
| VAD | silero-vad (ONNX) | gates STT on detected speech only; ~1 MB model, runs on CPU |
| AI | Claude (subscription OAuth) | per the `claude-oauth-auth` user skill (`~/.claude/skills/claude-oauth-auth/SKILL.md`); capability-gated for offline mode |
| Curated NLP | Python regex + intent registry | fast, deterministic, offline-capable |
| DB | SQLite (WAL mode) | local, file-based, family-private; single-writer |
| Password hashing | argon2-cffi (argon2id) | parent PIN hashing |
| Mic capture | sounddevice | cross-platform, low-latency, callback-based (bridged to asyncio via thread-safe queue) |
| Image decoding | Pillow + pillow-heif | JPEG/PNG/WebP via Pillow; iPhone HEIC via pillow-heif |
| Image generation | SD 1.5 + LCM-LoRA + Tier C composite (Phase F.5) | local toy action sprites for kiosk personas; CPU-feasible on 8 GB; falls back to no-sprite render on failure |
| Slugify | python-slugify | deterministic slug derivation from `display_name` for entity IDs |
| Frontend | React + TypeScript + Vite | dev/ standard; single project, two routes (`/parent`, `/child`) |
| Frontend state | Zustand | minimal boilerplate |
| Real-time | WebSockets (FastAPI) | bidirectional parent ↔ backend ↔ child |
| Type sync | pydantic-to-typescript | codegen TS types from Pydantic models on backend changes |
| Tests | pytest + Playwright | unit + integration + UI smoke |
| Lint/format | ruff (line-length=100) | dev/ standard |
| Type check | mypy strict | dev/ standard |
| Package mgmt | uv | dev/ standard |

## Document map

Read top-down by need. Each sub-doc is self-contained — load only the ones relevant to the task.

### Reference (load when touching that surface)

| Doc | Read when … |
|-----|-------------|
| [plan/architecture.md](plan/architecture.md) | adding a module, navigating the tree, arguing about a design decision |
| [plan/data-model.md](plan/data-model.md) | adding/altering a column, writing a migration, reasoning about FKs/indexes |
| [plan/api.md](plan/api.md) | adding an endpoint, changing a payload, debugging auth/CORS/Origin/LAN issues |
| [plan/runtime.md](plan/runtime.md) | reasoning about offline degradation, error surfaces, OAuth/capability, privacy posture |
| [plan/activity-loop.md](plan/activity-loop.md) | changing how activities are proposed/run, NLP triggers, ingestion, slot registry |
| [plan/how-to-run.md](plan/how-to-run.md) | bootstrapping a fresh dev machine, debugging the run loop, adjusting an env knob |
| [plan/risks.md](plan/risks.md) | scoping a feature touching one of the known risk surfaces |
| [plan/appendix.md](plan/appendix.md) | reference for persona/trigger JSON, frontend/Python configs, fixtures, future scope |
| [src/toybox/activities/templates/branching/](../src/toybox/activities/templates/branching/) | adding/editing branching templates — `boredom.json`, `request_play.json`, `request_story.json`, `request_activity.json` (50 templates each post-Phase G) |
| [documentation/runs/](runs/) | phase verification artifacts (UAT pass docs, soak runs, feasibility reports), dated `<YYYY-MM-DD>-<topic>.md` |

### Phase-by-phase build log

Each phase doc carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**` shape that `/build-phase` parses. Read the matching one when working a phase.

| Doc | Status |
|-----|--------|
| [phase-l-plan.md](phase-l-plan.md) — rewards system | ✅ COMPLETE 2026-05-17 — L1-L12 + 2 emergent fix rounds shipped at master `5aaf8ed`; UAT iter 3 all PASS |
| [phase-m-plan.md](phase-m-plan.md) — Periodic Table Professor + SEL (content depth) | ✅ COMPLETE 2026-05-18 at master `a096e11` — autonomous block + M2b sprite render + M7b TTS render + M14 iPad UAT all shipped. UAT verdict: 11/12 PASS (row #4 `shrink_into_helium_balloon_voyage` retest no longer gated — Phase N N0 commit `edc3cc9` shipped the D2 fix). Umbrella [#152](https://github.com/aberson/toybox/issues/152) closed; 3 defects + 1 feature request folded into Phase N + Phase O. Run-doc: [`runs/2026-05-18-phase-m-uat.md`](runs/2026-05-18-phase-m-uat.md). |
| [phase-n-plan.md](phase-n-plan.md) — Element microgame template shape (4-step + 2-fork) + Phase M defect fold-ins | ✅ COMPLETE 2026-05-19 — code-shipping steps N0/N0b/N1-prep/N1.5/N1/N2/N3/N4/N5 all merged on master; umbrella [#167](https://github.com/aberson/toybox/issues/167) closed. N6 dedicated UAT step folded into the next bundled N+O+P iPad UAT session rather than run as a standalone gate. |
| [phase-o-plan.md](phase-o-plan.md) — Parent UX 5-tab refresh (All / Adventures / Elements / Feelings & Friends / Transcriptions) | 📋 STAGED 2026-05-18 — 3 steps (O1, O2, O3). Umbrella [#177](https://github.com/aberson/toybox/issues/177) + step issues [#178-#180](https://github.com/aberson/toybox/issues/177) minted. Plan-review + plan-wrap complete. Independent of Phase N for kickoff; O2 depends on Phase N N2 (#172) for codegen baseline. |
| [phase-p-plan.md](phase-p-plan.md) — Toy image-gen quality redo (IP-Adapter Plus on SD 1.5) | CODE-SHIPPED 2026-05-19 — P1-P6 merged; P7 operator smoke gate ([#189](https://github.com/aberson/toybox/issues/189)) + P8 iPad UAT ([#191](https://github.com/aberson/toybox/issues/191)) pending. Umbrella [#182](https://github.com/aberson/toybox/issues/182). |
| [phase-q-plan.md](phase-q-plan.md) — Element-specific rewards (1:1 element_id → song/joke mapping) | CODE-SHIPPED 2026-05-20 — Q1-Q6 merged at master `f486568` (+99 tests: 40 schema, 25 backfill, 20 song-gen iter-2, 13 joke-gen, 30 picker, 11 smoke gate); halt at Q7 operator step ([#202](https://github.com/aberson/toybox/issues/202)). Umbrella [#195](https://github.com/aberson/toybox/issues/195). Q7+Q8 pending (operator runs generators + Coqui audio render); Q9 standalone iPad UAT cut 2026-05-21 (de-facto validated; rolled into bundled UAT). |
| [phase-r-plan.md](phase-r-plan.md) — UX refinements (cadence removal, spoken text limit, Q&A gating, activity search) | CODE-SHIPPED 2026-06-05 at master `a84de0a` — R1-R4 merged; umbrella [#211](https://github.com/aberson/toybox/issues/211), steps [#212-#215](https://github.com/aberson/toybox/issues/211) closed. R5 iPad UAT ([#216](https://github.com/aberson/toybox/issues/216)) bundled with Phase S UAT. 2,288 pytest + 682 vitest. |
| [phase-s-plan.md](phase-s-plan.md) — Kiosk visual refresh + character animation | CODE-SHIPPED 2026-06-05 at master `2040322` — S1 (persona gradients + step card) + S2 (Claude approve-time avatar animation) merged; umbrella [#217](https://github.com/aberson/toybox/issues/217), steps [#218](https://github.com/aberson/toybox/issues/218) + [#219](https://github.com/aberson/toybox/issues/219) closed. S3 iPad UAT ([#220](https://github.com/aberson/toybox/issues/220)) deferred (bundled with Phase T UAT). 2,298 pytest + 709 vitest. |
| [phase-t-plan.md](phase-t-plan.md) — offline template catalog browse + bundled UAT clearance | CODE-SHIPPED 2026-06-06 at master `45ab1f8` — T2 (GET /api/catalog + CatalogEntry/CatalogResponse) + T3 (CatalogPanel + browse toggle + categorizeTemplate) merged; umbrella [#222](https://github.com/aberson/toybox/issues/222) open. T1 bundled iPad UAT (R5+S3+O1-O3, [#223](https://github.com/aberson/toybox/issues/223)) + T4 catalog UAT ([#226](https://github.com/aberson/toybox/issues/226)) pending operator. |
| [phase-u-plan.md](phase-u-plan.md) — AnimateDiff toy action animations | CODE + BATCH COMPLETE 2026-06-07 at master `7d12c20` — U1 (AnimateDiff pipeline + ToyActionSprite WebP-first fallback) + U2 (batch_animate.py) merged; U3 overnight batch complete (140/140 WebPs, 0 failed); U4 iPad UAT ([#232](https://github.com/aberson/toybox/issues/232)) pending. **Note:** AnimateDiff-from-scratch approach produces poor identity preservation; Phase V replaces with SVD-idle + CSS-slot-entry hybrid. 2,323 pytest + 731 vitest. |
| [phase-v-plan.md](phase-v-plan.md) — Hybrid toy action animation (SVD idle + CSS slot-entry) | 📋 PLANNED 2026-06-07 — 3 steps (V1 CSS state machine, V2 SVD idle batch, V3 iPad UAT); issues to be minted via /repo-sync. Replaces Phase U AnimateDiff approach with SVD-for-idle + CSS-for-actions hybrid. |
| [plan/phase-e.md](plan/phase-e.md) — local model + tool-loop | IN FLIGHT — Step 28 carve-out shipped 2026-05-05; Step 27 (E3) carve-out shipped 2026-05-13; ≥50 SFT-filter-row gate cut 2026-05-21; Step 27 free to resume |

Completed phase docs (A, B, C, D, iPad-Kiosk, F.5, G, H, I) are in [`plan/archive/`](plan/archive/) — see the archive [README](plan/archive/README.md) for the per-doc index. The Status table above is the authoritative completion record.

### Archive

[`plan/archive/`](plan/archive/) holds the pre-refactor single-file plan and all completed phase docs. Snapshots only — do not read as a source of truth. Use the Status table above for current truth, and the [archive README](plan/archive/README.md) as the per-doc index.

## Key invariants (must respect on every edit)

These are the load-bearing rules. Several are runtime-checked or wired into hooks; breaking one is a bug, not a style choice.

1. **Single uvicorn worker.** SQLite WAL is single-writer; `--workers >1` corrupts silently. ([architecture.md "Process model"](plan/architecture.md#process-model), [data-model.md](plan/data-model.md))
2. **Default bind is `127.0.0.1`.** LAN binding requires a parent PIN; the startup `LAN binding guard` exits non-zero with `code=lan_bind_requires_pin` otherwise. ([api.md "LAN binding guard"](plan/api.md#lan-binding-guard))
3. **Every activity mutation requires `If-Match-Version`.** 409 + current version on mismatch. ([data-model.md "activities"](plan/data-model.md#activities), [api.md](plan/api.md))
4. **Every Claude call goes through the capability gate.** `is_capable()` returning `False` falls back to the offline path with a stable `capability_reason`. ([runtime.md](plan/runtime.md))
5. **Photo uploads always go through the validation pipeline.** No direct `Image.open` on user bytes outside `src/toybox/storage/images.py`. ([activity-loop.md "Upload validation rules"](plan/activity-loop.md#upload-validation-rules-apply-to-all-photo-endpoints))
6. **Transcript text never logged at INFO+.** A pre-commit hook enforces this. ([runtime.md "Logging policy"](plan/runtime.md#logging-policy))
7. **`trigger_phrase` and `persona_reasoning` are PII-stripped (Personally Identifiable Information) from the `activity.state` ws topic.** REST GET remains full-fidelity for parent scope only. ([archive/phase-d.md "Step 23"](plan/archive/phase-d.md#step-23-live-activity-polish--suggestion-why-this))
8. **Slugs are server-derived from `display_name`.** Client cannot supply them. Empty/all-symbol display_names reject with `code=invalid_display_name`. ([data-model.md "Slug derivation"](plan/data-model.md#slug-derivation))
9. **Pydantic ↔ TypeScript codegen is a pre-commit hook.** Drift in `frontend/src/shared/types.ts` is a check failure. ([appendix.md ".pre-commit-config.yaml"](plan/appendix.md#pre-commit-configyaml))
10. **Forward-only migrations.** v1 has no rollback path and no DB backups; abort + preserve DB on failure, recover via `documentation/operator/recovery.md`. ([data-model.md "Storage"](plan/data-model.md#storage), [archive/phase-d.md "Manual M5"](plan/archive/phase-d.md#manual-m5--operator-recovery-procedures-referenced-from-documentationoperatorrecoverymd))

## Development process

**To run the system,** see [plan/how-to-run.md](plan/how-to-run.md) for fresh-machine bootstrap, env vars, and the run loop.

The slash-commands referenced below are skills under `.claude/skills/` (project-scoped under `dev/.claude/skills/<name>/SKILL.md`; user-global ones at `~/.claude/skills/<name>/SKILL.md`). Read each skill's SKILL.md before invoking. Quick guide: `/build-phase` orchestrates a multi-step phase end-to-end, `/build-step` builds one step, `/build-step-tdd` is the TDD (Test-Driven Development) variant, `/repo-init` and `/repo-sync` bootstrap and reconcile GitHub issues from the plan.

Use `/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` where TDD makes sense — schema/CRUD steps are good TDD candidates).

**Prerequisite before the first `/build-phase` run:** run `/repo-init` to create the GitHub repo + per-step issues, then `/repo-sync` to populate the `**Issue:** #` lines in each step. `/build-phase` posts progress to those issues; missing issue numbers break the audit trail. Re-run `/repo-sync` after any plan-doc edits that change step shape or numbering.

Build order: Phase A → B → C → D → iPad-Kiosk → F.5 → G → H → I → J → K → L → M → N ✅ (all shipped). Phase O (parent UX 5-tab refresh) + Phase P (toy image-gen IP-Adapter redo) code-shipped 2026-05-19, awaiting bundled iPad UAT. Phase Q (element-specific rewards) Q1-Q6 code-shipped 2026-05-20 at master `f486568`; Q7+Q8 operator steps pending (Q9 standalone UAT cut 2026-05-21). Phase E (local model + tool-loop) is in flight — Step 28 + Step 27 (E3) carve-outs shipped 2026-05-05 / 2026-05-13; ≥50 SFT-filter-row gate cut 2026-05-21, Step 27 ready to resume.
