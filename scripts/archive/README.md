# scripts/archive

> **Not canonical.** Scripts kept for historical reference. Do NOT run — they reference retired model paths or workflows that no longer exist on disk.

| File | What it was |
|---|---|
| `image_gen_setup.py` | Original Phase F (F1-era) all-in-one downloader for SDXL base + IP-Adapter ViT-H + pixel-art-XL LoRA. Retired 2026-05-09 after Phase F.5 replaced the SDXL+IPA pipeline with SD 1.5 + LCM-LoRA + cartoon checkpoint (see [`documentation/plan/phase-f-5-sprite-cartoon-redo.md`](../../documentation/plan/phase-f-5-sprite-cartoon-redo.md)). The new download path is the per-component `f5_download_*.py` scripts. The retired checkpoint dirs (`sdxl/`, `ip_adapter/`, `pixel_art_lora/`) were removed from disk on 2026-05-09 post-F.5-5; running this script would re-download ~9 GB of unused data. |
