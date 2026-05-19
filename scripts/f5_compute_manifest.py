"""F.5-1 helper: compute sha256s of all checkpoint files and write manifest.json.

Walks data/models/image_gen/, hashes every .safetensors / .onnx / .json /
.bin file >1 KB, writes the manifest as JSON sorted by path. Re-running
produces the same manifest from the same on-disk state (deterministic).

Honors TOYBOX_IMAGE_GEN_MODEL_DIR if set.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MODEL_DIR = Path(os.environ.get("TOYBOX_IMAGE_GEN_MODEL_DIR", "data/models/image_gen"))

# Files we hash. We DON'T hash the cache subdirs (.cache/) or huggingface
# internal trackers (.gitignore, CACHEDIR.TAG, .gitattributes) — those are
# bookkeeping, not weights.
EXTENSIONS = (".safetensors", ".onnx", ".bin")

# Phase P: known checkpoint paths we want to surface in the manifest even
# when absent. Recording these explicitly catches the "operator forgot to
# run the download script" case loudly — manifest entry value is
# {"status": "missing"} instead of the path silently disappearing from the
# manifest. Paths are relative to MODEL_DIR. Use forward slashes.
#
# The image_encoder config.json is included so a half-failed IPA download
# (adapter weight succeeded but encoder snapshot interrupted) is still loud.
# config.json is tiny + always present when the encoder snapshot succeeds.
EXPECTED_RELATIVE_PATHS = (
    "ip_adapter/models/ip-adapter-plus_sd15.bin",
    "ip_adapter/models/image_encoder/config.json",
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_entries(model_dir: Path) -> dict[str, dict[str, object]]:
    """Walk model_dir and produce the manifest entries dict.

    Hashes every file with one of EXTENSIONS, then layers EXPECTED_RELATIVE_PATHS
    in: any expected path absent from disk is recorded with {"status": "missing"}
    so the operator sees the gap rather than silent omission.
    """
    entries: dict[str, dict[str, object]] = {}

    for path in sorted(model_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(model_dir).parts):
            # skip .cache/, .gitignore, .gitattributes etc.
            continue
        if path.suffix.lower() not in EXTENSIONS:
            continue
        rel = path.relative_to(model_dir).as_posix()
        size = path.stat().st_size
        digest = sha256_of(path)
        entries[rel] = {
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
            "sha256": digest,
        }
        print(f"hashed {rel} ({entries[rel]['size_mb']} MB)")

    for expected_rel in EXPECTED_RELATIVE_PATHS:
        if expected_rel in entries:
            continue
        expected_path = model_dir / expected_rel
        if expected_path.is_file():
            # File exists but its extension isn't in EXTENSIONS (e.g. config.json).
            # Hash it anyway so a present-but-unhashed expected path is recorded.
            size = expected_path.stat().st_size
            digest = sha256_of(expected_path)
            entries[expected_rel] = {
                "size_bytes": size,
                "size_mb": round(size / 1024 / 1024, 2),
                "sha256": digest,
            }
            print(f"hashed {expected_rel} ({entries[expected_rel]['size_mb']} MB)")
        else:
            entries[expected_rel] = {"status": "missing"}
            print(f"missing {expected_rel}")

    return entries


def main() -> None:
    if not MODEL_DIR.exists():
        raise SystemExit(f"model dir not found: {MODEL_DIR}")

    entries = compute_entries(MODEL_DIR)

    manifest_path = MODEL_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {manifest_path} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
