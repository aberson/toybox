# Sonnet-Window Revisit Plan — toybox

**Status:** Open — Opus diff re-review pending
**Created:** 2026-06-16
**Owner:** operator + Opus session

## Why this exists

A `latest`-channel CLI auto-update (~2026-06-05) silently reset the model from Opus to
Sonnet. Interactive main-loop sessions ran on Sonnet from ~Jun 5 to ~Jun 8 in toybox.
Root cause + fix: `dev/CLAUDE.md` Environment section, memory
`feedback_model_pin_opus_autoupdate_reset`.

toybox landed **four `/build-phase` phases (R, S, T, U/V)** under Sonnet. Revisit depth:
**diff re-review with Opus**.

## Scope caveat

These were `/build-phase` runs (sub-agent developers/reviewers). Sub-agent pinning may
differ, but **plan authoring, phase orchestration, UAT-bundle decisions, and pipeline
fixes were Sonnet**. toybox is also a child-facing device — content/safety judgment in
activity-step gating (Phase R Q&A gating) deserves a careful Opus pass.

## Commits in window (Jun 5–8)

### Phase R — UX refinements (HIGH — child-facing + safety gating)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| 5ac5860 | 06-05 | plan Phase R UX refinements | Plan soundness |
| 222a804 | 06-05 | R1: remove cadence loop + restyle TriggerButton | Behavior change correct? |
| c6d4816 | 06-05 | R2: spoken text character limit | Limit value sane for kids |
| a7ebf84 | 06-05 | **R3: Q&A gating for activity steps (#214)** | **Safety-critical — gating logic for child content** |
| fafb9c5 | 06-05 | R4: activity search GET /api/search + SearchPanel (#215) | Endpoint + search correctness |

### Phase S — kiosk visual refresh + animation (MEDIUM)

| Commit | Date | What |
|--------|------|------|
| 2e78fa4 | 06-05 | plan Phase S |
| e50a88e | 06-05 | S1: persona-keyed kiosk gradients + step card prominence |
| c7b19e0 / 2040322 | 06-05 | S2: Claude approve-time avatar animations |

### Phase T — bundled UAT + offline catalog browse (MEDIUM)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| a20b7dd | 06-06 | plan Phase T | — |
| 601e329 | 06-06 | T2: GET /api/catalog + CatalogEntry/Response + tests | **Wire-shape: response read directly by frontend** (see code-quality rule) |
| 45ab1f8 | 06-06 | T3: CatalogPanel + browse toggle + categorizeTemplate | Categorization correctness |

### Phase U/V — AnimateDiff animations (MEDIUM — heavy ML pipeline)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| 1d7a05a | 06-06 | plan Phase U | — |
| a25f4a1 | 06-06 | U1: AnimateDiff wrapper + ToyActionSprite WebP fallback | Pipeline correctness |
| 7d12c20 | 06-06 | U2/U3: pipeline fixes; 140 WebPs generated | Output quality (visual) |
| 44fba96 | 06-07 | plan Phase V | — |
| 4908131 | 06-07 | V1: ToyActionSprite CSS intro + idle WebP state machine | State machine correctness |
| 241eaf0 | 06-07 | V2: --approach svd to batch_animate.py | — |

> Phase V left **M1 (iPad UAT) deferred** (07f7013). Confirm that UAT was completed correctly
> or is still pending — a Sonnet-authored deferral decision.

## Procedure (per commit)

1. Opus session: `git -C c:/Users/abero/dev/toybox show <sha>`.
2. **Start with R3 (#214) Q&A gating** — highest safety stakes for a child device.
3. Re-run test suite; verify wire-shape on T2 catalog endpoint (frontend renders it directly).
4. Verdict: `OK | needs-fix | revert`.

## Re-review checklist

- [ ] R3 Q&A gating logic safe + correct (a7ebf84) — TOP priority
- [ ] R2 character limit appropriate (c6d4816)
- [ ] T2 catalog response wire-shape matches frontend (601e329)
- [ ] Phase V M1 iPad UAT status confirmed (not silently skipped)
- [ ] Full test suite green at current HEAD
