# M2.5 UAT fixtures

Test images for [Manual M2.5](../../../../documentation/plan.md) — the v1 bundled UI smoke. Generated from [scripts/uat/generate_m2_5_fixtures.py](../../../../scripts/uat/generate_m2_5_fixtures.py); regenerate any time with:

```powershell
uv run python scripts/uat/generate_m2_5_fixtures.py
# or, to overwrite existing files:
uv run python scripts/uat/generate_m2_5_fixtures.py --force
```

All fixtures pass `toybox.storage.images.validate_upload()` (correct magic bytes, allowed MIME, under the dimension and byte caps). Vision will return generic suggestions because the images are programmatic, not real photos — that's fine for UAT; the goal is to exercise the upload + dedup + assignment pipeline, not to evaluate vision quality.

## Files

| File | Used by | Purpose |
|---|---|---|
| `toy-1.png` (800x600 PNG) | M2.5.3 step 1 | Toy ingest happy-path upload |
| `toy-1-dup.png` (byte-copy of `toy-1.png`) | M2.5.3 step 3 | SHA-256 dedup → expect 409 |
| `room-1.jpg` … `room-5.jpg` (1024x768 JPG, distinct backgrounds) | M2.5.4 steps 1–3 | Bulk room ingest happy path |
| `room-bulk-51/photo-01.jpg` … `photo-51.jpg` (640x480 JPG) | M2.5.4 step 4 | Bulk-cap negative path → expect 413 |

## Notes

- The `room-bulk-51/` folder has exactly 51 files so a single upload trips the `bulk_cap_exceeded` (413) error. Don't trim it to 50 — that would make the cap pass silently.
- All images are tiny (single-digit KB to ~17 KB) so the whole fixture set is well under 500 KB total.
- The pipeline's SHA-256 dedup keys on raw bytes, so `toy-1-dup.png` is a `Copy-Item` of `toy-1.png` rather than a re-encoded "visually identical" image. Re-encoding would change the hash and defeat the dedup test.
