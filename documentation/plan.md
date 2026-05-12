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

**v1 shipped 2026-05-02** at the end of Phase A — closed-loop demo with a manual "trigger" button instead of a real mic; adult-only testing. The system has since shipped phases B (hearing), C (content), D (polish), iPad-Kiosk (PWA on iPad), F.5 (image-gen sprite pipeline), G (branching gameplay), H (parent UX revamp), and I (transcript retention + display refresh) and is in family-private testing on real children. **Next post-v1 increment** is Phase E (local model + tool-loop).

## Status

| Phase | Goal | Status |
|-------|------|--------|
| **A** — closed-loop skeleton (v1 ship) | trigger → suggestion → approve → child runs activity → done | ✅ COMPLETE 2026-05-02 |
| **B** — hearing | audio capture → VAD (Voice Activity Detection) → faster-whisper → trigger registry → mode-aware Claude escalation | ✅ COMPLETE 2026-05-03 |
| **C** — content | toy/room/child ingestion + activity-quality eval scaffold + real catalog in generators | ✅ COMPLETE 2026-05-03 |
| **D** — polish | anti-signal feedback, parent PIN gate, transcripts, "why this?", metrics | ✅ COMPLETE 2026-05-03. M2.5 (manual milestone 2.5 — bundled UAT/User Acceptance Testing release gate between Phase D and E) retired 2026-05-10; see row below |
| **iPad-Kiosk** | child kiosk on iPad PWA (Progressive Web App) | ✅ COMPLETE 2026-05-10 — iK1–iK4 ✅ 2026-05-04; iK5 closed via Phase G iPad UAT (TOYBOX_LAN_IP doc + troubleshooting matrix landed during the run) |
| **F** — toy action sprites (SDXL/Stable Diffusion XL pipeline) | sprite generation for kiosk personas | ARCHIVED 2026-05-09 — F1-F8 shipped 2026-05-06; F9 hit c10.dll crash ([#61](https://github.com/aberson/toybox/issues/61)); superseded by F.5 |
| **F.5** — sprite pipeline cartoon redo | SD 1.5 + LCM-LoRA (Latent Consistency Model + Low-Rank Adaptation) + Tier C composite | ✅ COMPLETE 2026-05-09 — all 5 steps shipped, [#61](https://github.com/aberson/toybox/issues/61) closed via F.5-5 soft-pass |
| **G** — branching gameplay | multi-option choice steps + variable-length activities (offline templates only) | ✅ COMPLETE 2026-05-10 — 200 branching templates (50 per intent) shipped via overnight 4-agent soak; iPad UAT PASS |
| **M2.5** — bundled UAT release gate (formerly Phase D) | bundled human UI verification of steps 16/17/18/21/22/23/24 | RETIRED 2026-05-10 — happy paths de-facto validated by every phase B-G operator session; remaining edge-case checks (dedup 409, bulk-cap 51, wipe-all PIN, ws→REST fallback) optional, not gating; script preserved as reference |
| **E** — local model + tool-loop (post-v1) | tool-loop refactor + locally-hosted SFT (Supervised Fine-Tuned) model swap-in for Claude OAuth | IN FLIGHT — Step 28 carve-out shipped 2026-05-05 (commit `33a4b3c`: tool registry + ClaudeActivityGenerator wrapper + env-var dispatch + `is_local_capable()` stub). Remainder gated on ≥50 SFT-filter rows in `labeled_events` (the parent-feedback signal table from Phase D step 20 — populated naturally as parents tag activities; schema in [plan/data-model.md](plan/data-model.md)) |
| **H** — parent UX revamp (tabs + global banned themes) | parent app moves from panel-toggle nav to two-level tabbed shell (Play / Kids & Toyboxes / Settings); `banned_themes` promoted from per-child column to a single `settings.banned_themes_global` key (formalizes existing UNION-across-children runtime behavior) | ✅ COMPLETE 2026-05-10 — all 6 steps (H1-H6) shipped; iPad UAT PASS; [run doc](runs/2026-05-10-phase-h-uat.md); follow-up [#84](https://github.com/aberson/toybox/issues/84) for unrelated Phase G slot-fill defect surfaced during UAT |
| **I** — transcript retention + display refresh | configurable transcript retention (1m/3m/5m/10m/15m, default 1m) with backend sweep + filter-on-read; parent UI drops per-row delete (wipe-all stays) and adds local-timer fade-out animation as rows cross expiry | ✅ COMPLETE 2026-05-11 — all 5 steps (I1–I5) shipped; #86–#91 closed; iPad UAT PASS; [run doc](runs/2026-05-11-phase-i-uat.md); key shipped patterns: byte-identical ISO format pinning across pipeline/sweep/filter, `fadingIdsRef` mirror to keep `setInterval` cadence stable across fades, snap-to-nearest defensive `aria-pressed` for non-canonical retention values |

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
| [plan/phase-a.md](plan/phase-a.md) — closed-loop skeleton | ✅ DONE |
| [plan/phase-b.md](plan/phase-b.md) — hearing | ✅ DONE |
| [plan/phase-c.md](plan/phase-c.md) — content | ✅ DONE |
| [plan/phase-d.md](plan/phase-d.md) — polish | ✅ DONE |
| [plan/phase-d-uat-m2.5.md](plan/phase-d-uat-m2.5.md) — v1 release gate (bundled UAT) | RETIRED 2026-05-10 — kept as reference UAT script |
| [plan/archive/phase-f-toy-action-sprites.md](plan/archive/phase-f-toy-action-sprites.md) — toy action sprites (SDXL pipeline) | ARCHIVED 2026-05-09 — superseded by F.5 |
| [plan/phase-ipad-kiosk.md](plan/phase-ipad-kiosk.md) — child kiosk on iPad PWA | ✅ COMPLETE 2026-05-10 |
| [plan/phase-e.md](plan/phase-e.md) — local model + tool-loop | NOT STARTED |
| [plan/phase-f-5-sprite-cartoon-redo.md](plan/phase-f-5-sprite-cartoon-redo.md) — sprite pipeline cartoon redo (SD 1.5 + LCM-LoRA + Tier C composite) | ✅ DONE 2026-05-09 — all 5 steps shipped, [#61](https://github.com/aberson/toybox/issues/61) closed via F.5-5 soft-pass |
| [plan/phase-g-branching-gameplay.md](plan/phase-g-branching-gameplay.md) — branching gameplay (multi-option steps + variable length) | ✅ COMPLETE 2026-05-10 — 200 branching templates shipped, iPad UAT PASS |
| [plan/phase-h-parent-ux-revamp.md](plan/phase-h-parent-ux-revamp.md) — parent UX revamp (tabs + global banned themes) | ✅ COMPLETE 2026-05-10 — all 6 steps shipped, iPad UAT PASS, [run doc](runs/2026-05-10-phase-h-uat.md) |
| [plan/phase-i-transcript-retention.md](plan/phase-i-transcript-retention.md) — transcript retention + display refresh | PLANNED 2026-05-10 — 5 steps (I1–I5); awaiting `/repo-sync` for issue minting |

### Archive

[`plan/archive/`](plan/archive/) holds the pre-refactor single-file plan for historical diffing only. Not canonical — do not read it as a source of truth.

## Key invariants (must respect on every edit)

These are the load-bearing rules. Several are runtime-checked or wired into hooks; breaking one is a bug, not a style choice.

1. **Single uvicorn worker.** SQLite WAL is single-writer; `--workers >1` corrupts silently. ([architecture.md "Process model"](plan/architecture.md#process-model), [data-model.md](plan/data-model.md))
2. **Default bind is `127.0.0.1`.** LAN binding requires a parent PIN; the startup `LAN binding guard` exits non-zero with `code=lan_bind_requires_pin` otherwise. ([api.md "LAN binding guard"](plan/api.md#lan-binding-guard))
3. **Every activity mutation requires `If-Match-Version`.** 409 + current version on mismatch. ([data-model.md "activities"](plan/data-model.md#activities), [api.md](plan/api.md))
4. **Every Claude call goes through the capability gate.** `is_capable()` returning `False` falls back to the offline path with a stable `capability_reason`. ([runtime.md](plan/runtime.md))
5. **Photo uploads always go through the validation pipeline.** No direct `Image.open` on user bytes outside `src/toybox/storage/images.py`. ([activity-loop.md "Upload validation rules"](plan/activity-loop.md#upload-validation-rules-apply-to-all-photo-endpoints))
6. **Transcript text never logged at INFO+.** A pre-commit hook enforces this. ([runtime.md "Logging policy"](plan/runtime.md#logging-policy))
7. **`trigger_phrase` and `persona_reasoning` are PII-stripped (Personally Identifiable Information) from the `activity.state` ws topic.** REST GET remains full-fidelity for parent scope only. ([phase-d.md "Step 23"](plan/phase-d.md#step-23-live-activity-polish--suggestion-why-this))
8. **Slugs are server-derived from `display_name`.** Client cannot supply them. Empty/all-symbol display_names reject with `code=invalid_display_name`. ([data-model.md "Slug derivation"](plan/data-model.md#slug-derivation))
9. **Pydantic ↔ TypeScript codegen is a pre-commit hook.** Drift in `frontend/src/shared/types.ts` is a check failure. ([appendix.md ".pre-commit-config.yaml"](plan/appendix.md#pre-commit-configyaml))
10. **Forward-only migrations.** v1 has no rollback path and no DB backups; abort + preserve DB on failure, recover via `documentation/operator/recovery.md`. ([data-model.md "Storage"](plan/data-model.md#storage), [phase-d.md "Manual M5"](plan/phase-d.md#manual-m5--operator-recovery-procedures-referenced-from-documentationoperatorrecoverymd))

## Development process

**To run the system,** see [plan/how-to-run.md](plan/how-to-run.md) for fresh-machine bootstrap, env vars, and the run loop.

The slash-commands referenced below are skills under `.claude/skills/` (project-scoped under `dev/.claude/skills/<name>/SKILL.md`; user-global ones at `~/.claude/skills/<name>/SKILL.md`). Read each skill's SKILL.md before invoking. Quick guide: `/build-phase` orchestrates a multi-step phase end-to-end, `/build-step` builds one step, `/build-step-tdd` is the TDD (Test-Driven Development) variant, `/repo-init` and `/repo-sync` bootstrap and reconcile GitHub issues from the plan.

Use `/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` where TDD makes sense — schema/CRUD steps are good TDD candidates).

**Prerequisite before the first `/build-phase` run:** run `/repo-init` to create the GitHub repo + per-step issues, then `/repo-sync` to populate the `**Issue:** #` lines in each step. `/build-phase` posts progress to those issues; missing issue numbers break the audit trail. Re-run `/repo-sync` after any plan-doc edits that change step shape or numbering.

Build order: Phase A → B → C → D → iPad-Kiosk → F (archived) → F.5 → G → H ✅ (all shipped). Phase I (transcript retention + display refresh) planned 2026-05-10 — [plan/phase-i-transcript-retention.md](plan/phase-i-transcript-retention.md), 5 steps, awaiting `/repo-sync`. Phase E (local model + tool-loop) is in flight — Step 28 carve-out shipped 2026-05-05; remainder is gated on a data-volume threshold (≥50 SFT-filter rows in `labeled_events`), not calendar time. One Phase G follow-up [#84](https://github.com/aberson/toybox/issues/84) — slot-fill skips placeholders that only appear in choice labels (surfaced by Phase H iPad UAT).
