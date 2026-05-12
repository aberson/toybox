# Archive

> **Not canonical.** Snapshots kept for historical reference. Do NOT use as a source of truth — read [`../../plan.md`](../../plan.md) and the active sub-docs instead.

## Completed phase docs (archived 2026-05-11)

All shipped successfully. Authoritative completion notes (dates, run-doc links, commit context) live in the [Status table in `../../plan.md`](../../plan.md#status). These docs are kept for module-level orientation and historical reconstruction.

| File | Phase | Shipped |
|---|---|---|
| `phase-a.md` | Closed-loop skeleton (v1) | 2026-05-02 |
| `phase-b.md` | Hearing (audio → VAD → STT → triggers → escalation) | 2026-05-03 |
| `phase-c.md` | Content (toy/room/child ingestion + eval scaffold) | 2026-05-03 |
| `phase-d.md` | Polish (anti-signal, PIN gate, transcripts, metrics) | 2026-05-03 |
| `phase-ipad-kiosk.md` | Child kiosk on iPad PWA | 2026-05-10 |
| `phase-f-5-sprite-cartoon-redo.md` | Sprite pipeline cartoon redo (SD 1.5 + LCM-LoRA + Tier C composite) — closed [#61](https://github.com/aberson/toybox/issues/61) | 2026-05-09 |
| `phase-g-branching-gameplay.md` | Branching gameplay — 200 templates shipped via overnight 4-agent soak | 2026-05-10 |
| `phase-h-parent-ux-revamp.md` | Parent UX revamp (tabs + global banned themes) | 2026-05-10 |
| `phase-i-transcript-retention.md` | Transcript retention + display refresh | 2026-05-11 |

## Retired / superseded

| File | What it is |
|---|---|
| `plan-pre-refactor-2026-05-07.md` | The single-file `plan.md` immediately before the 2026-05-07 progressive-disclosure refactor (commit context: `5bbdefb` / `master`). 2471 lines. Replaced by [`../../plan.md`](../../plan.md) (index) + [`../`](../) (sub-docs). Kept for one-stop reading and easy diffing while the new structure beds in. |
| `phase-f-toy-action-sprites.md` | Original Phase F build plan (SDXL + IP-Adapter + pixel-art-LoRA, child-side toy-action sprites). Retired 2026-05-09 after F9 hit a `c10.dll` access violation under sustained generation (see [`#61`](https://github.com/aberson/toybox/issues/61)). Superseded by [`phase-f-5-sprite-cartoon-redo.md`](phase-f-5-sprite-cartoon-redo.md), which switched the pipeline to SD 1.5 + LCM-LoRA + cartoon checkpoint and shipped F.5-1 through F.5-5 in early May 2026. References retired paths (`sdxl/`, `ip_adapter/`, `pixel_art_lora/`) + retired script `scripts/image_gen_setup.py`. |
| `phase-d-uat-m2.5.md` | Bundled UI smoke (the v1 release gate covering Phase C+D step visuals). Retired 2026-05-10 as a release gate — happy paths were de-facto exercised every operator session through Phases B-G; remaining edge-case checks become ad-hoc. See header note for retirement reasoning. |
