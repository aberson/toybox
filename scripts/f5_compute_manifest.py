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


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if not MODEL_DIR.exists():
        raise SystemExit(f"model dir not found: {MODEL_DIR}")

    entries: dict[str, dict[str, object]] = {}

    for path in sorted(MODEL_DIR.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(MODEL_DIR).parts):
            # skip .cache/, .gitignore, .gitattributes etc.
            continue
        if path.suffix.lower() not in EXTENSIONS:
            continue
        rel = path.relative_to(MODEL_DIR).as_posix()
        size = path.stat().st_size
        digest = sha256_of(path)
        entries[rel] = {
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
            "sha256": digest,
        }
        print(f"hashed {rel} ({entries[rel]['size_mb']} MB)")

    manifest_path = MODEL_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {manifest_path} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
