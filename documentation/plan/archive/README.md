# Archive

> **Not canonical.** Snapshots kept for historical reference. Do NOT use as a source of truth — read [`../../plan.md`](../../plan.md) and the active sub-docs instead.

| File | What it is |
|---|---|
| `plan-pre-refactor-2026-05-07.md` | The single-file `plan.md` immediately before the 2026-05-07 progressive-disclosure refactor (commit context: `5bbdefb` / `master`). 2471 lines. Replaced by [`../../plan.md`](../../plan.md) (index) + [`../`](../) (sub-docs). Kept for one-stop reading and easy diffing while the new structure beds in. |
| `phase-f-toy-action-sprites.md` | Original Phase F build plan (SDXL + IP-Adapter + pixel-art-LoRA, child-side toy-action sprites). Retired 2026-05-09 after F9 hit a `c10.dll` access violation under sustained generation (see [`#61`](https://github.com/aberson/toybox/issues/61)). Superseded by [`../phase-f-5-sprite-cartoon-redo.md`](../phase-f-5-sprite-cartoon-redo.md), which switched the pipeline to SD 1.5 + LCM-LoRA + cartoon checkpoint and shipped F.5-1 through F.5-5 in early May 2026. References retired paths (`sdxl/`, `ip_adapter/`, `pixel_art_lora/`) + retired script `scripts/image_gen_setup.py`. |
