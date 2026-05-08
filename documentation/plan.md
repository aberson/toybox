# toybox

AI assistant for play with children. Passive-listening home device that watches for play opportunities, suggests activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

> **This file is the index.** It carries the elevator pitch, current status, and pointers into [`plan/`](plan/) for the rest. Each sub-doc opens with a one-line "scope" hint so you can decide whether to load it.

## What this is

A local-first family-private system that:

1. **Listens** to ambient audio in the play area (single mic on the home machine).
2. **Detects** play opportunities through a curated NLP layer with an optional Claude escalation path.
3. **Proposes** structured activities (linear scripts) to the parent.
4. **Runs** approved activities on a child-facing kiosk app — persona avatar, step cards, sound effects.
5. **Learns** from "this didn't work" parent feedback to avoid recurring flop patterns.

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth and the system degrades to a fully-offline mode without it.

**v1 ship point: end of Phase A** — the closed-loop demo with a manual "trigger" button instead of a real mic. v1 testing is **adult-only** (the user and their spouse) before children participate.

## Status

| Phase | Goal | Status |
|-------|------|--------|
| **A** — closed-loop skeleton (v1 ship) | trigger → suggestion → approve → child runs activity → done | ✅ COMPLETE 2026-05-02 |
| **B** — hearing | audio capture → VAD → faster-whisper → trigger registry → mode-aware Claude escalation | ✅ COMPLETE 2026-05-03 |
| **C** — content | toy/room/child ingestion + activity-quality eval scaffold + real catalog in generators | ✅ COMPLETE 2026-05-03 |
| **D** — polish | anti-signal feedback, parent PIN gate, transcripts, "why this?", metrics | ✅ COMPLETE 2026-05-03 |
| **D** — UAT release gate (M2.5) | bundled human UI verification of steps 16/17/18/21/22/23/24 | OUTSTANDING (run before Phase E) |
| **iPad-Kiosk** | child kiosk on iPad PWA | iK1–iK4 ✅ 2026-05-04; iK5 (operator doc + on-device UAT) outstanding |
| **E** — local model + tool-loop + non-linear gameplay (post-v1) | swap Claude OAuth for SFT'd local model, then tool-loop | NOT STARTED — prereq: ≥1 mo telemetry |

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

### Phase-by-phase build log

Each phase doc carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**` shape that `/build-phase` parses. Read the matching one when working a phase.

| Doc | Status |
|-----|--------|
| [plan/phase-a.md](plan/phase-a.md) — closed-loop skeleton | ✅ DONE |
| [plan/phase-b.md](plan/phase-b.md) — hearing | ✅ DONE |
| [plan/phase-c.md](plan/phase-c.md) — content | ✅ DONE |
| [plan/phase-d.md](plan/phase-d.md) — polish | ✅ DONE (M2.5 UAT pending) |
| [plan/phase-d-uat-m2.5.md](plan/phase-d-uat-m2.5.md) — v1 release gate (bundled UAT) | OUTSTANDING |
| [plan/phase-ipad-kiosk.md](plan/phase-ipad-kiosk.md) — child kiosk on iPad PWA | iK1–iK4 ✅; iK5 outstanding |
| [plan/phase-e.md](plan/phase-e.md) — local model + tool-loop + non-linear gameplay | NOT STARTED |
| [plan/phase-f-toy-action-sprites.md](plan/phase-f-toy-action-sprites.md) — toy action sprites | NOT STARTED |

### Other planning docs

| Doc | Purpose |
|-----|---------|
| [eval-fixtures.md](eval-fixtures.md) | activity-quality rubric anchors and held-out fixture index |

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
7. **`trigger_phrase` and `persona_reasoning` are PII-stripped from the `activity.state` ws topic.** REST GET remains full-fidelity for parent scope only. ([phase-d.md "Step 23"](plan/phase-d.md#step-23-live-activity-polish--suggestion-why-this))
8. **Slugs are server-derived from `display_name`.** Client cannot supply them. Empty/all-symbol display_names reject with `code=invalid_display_name`. ([data-model.md "Slug derivation"](plan/data-model.md#slug-derivation))
9. **Pydantic ↔ TypeScript codegen is a pre-commit hook.** Drift in `frontend/src/shared/types.ts` is a check failure. ([appendix.md ".pre-commit-config.yaml"](plan/appendix.md#pre-commit-configyaml))
10. **Forward-only migrations.** v1 has no rollback path and no DB backups; abort + preserve DB on failure, recover via `documentation/operator/recovery.md`. ([data-model.md "Storage"](plan/data-model.md#storage), [phase-d.md "Manual M5"](plan/phase-d.md#manual-m5--operator-recovery-procedures-referenced-from-documentationoperatorrecoverymd))

## Development process

Use `/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` where TDD makes sense — schema/CRUD steps are good TDD candidates).

**Prerequisite before the first `/build-phase` run:** run `/repo-init` to create the GitHub repo + per-step issues, then `/repo-sync` to populate the `**Issue:** #` lines in each step. `/build-phase` posts progress to those issues; missing issue numbers break the audit trail. Re-run `/repo-sync` after any plan-doc edits that change step shape or numbering.

Build order: Phase A → B → C → D → (M2.5 release gate) → iPad-Kiosk / E. Manual steps interleave as marked inside each phase doc.
