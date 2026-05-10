# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "huggingface-hub>=1.0",
# ]
# ///
"""Phase F operator setup: populate data/models/image_gen/ with the four checkpoints.

Idempotent. Run as:

    uv run scripts/image_gen_setup.py

What it does:
    1. Downloads SDXL base 1.0 fp16 weights to
       data/models/image_gen/sdxl/stable-diffusion-xl-base-1.0/
       (filtered to fp16 variants only -- saves ~7 GB vs the full repo)
    2. Downloads IP-Adapter SDXL ViT-H weights + the matching CLIP image encoder to
       data/models/image_gen/ip_adapter/
    3. Downloads pixel-art-XL LoRA to data/models/image_gen/pixel_art_lora/
    4. Downloads rembg u2net.onnx to data/models/image_gen/bg_remove/
    5. Computes sha256 of each top-level safetensors / onnx file and writes
       data/models/image_gen/manifest.json

GPU FLOOR: 12 GB recommended; 8 GB hosts must use the canonical config in
documentation/operator/image-gen-runtime.md (model_cpu_offload + vae.enable_slicing()).
Empirically validated on RTX 4070 Laptop 8 GB at peak 6.11 GB / 30 s per 1024x1024 fp16
sprite -- see documentation/runs/2026-05-06-phase-f-8gb-feasibility.md.

Re-running this script is safe; it skips files that already exist with matching size.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO_ROOT / "data" / "models" / "image_gen"

SDXL_DIR = MODEL_DIR / "sdxl" / "stable-diffusion-xl-base-1.0"
IPA_DIR = MODEL_DIR / "ip_adapter"
LORA_DIR = MODEL_DIR / "pixel_art_lora"
BG_DIR = MODEL_DIR / "bg_remove"

# fp16-only filter for SDXL base -- avoids pulling the fp32 duplicates.
SDXL_ALLOW_PATTERNS: list[str] = [
    "model_index.json",
    "scheduler/*.json",
    "text_encoder/config.json",
    "text_encoder/model.fp16.safetensors",
    "text_encoder_2/config.json",
    "text_encoder_2/model.fp16.safetensors",
    "tokenizer/*",
    "tokenizer_2/*",
    "unet/config.json",
    "unet/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.fp16.safetensors",
]

# u2net.onnx canonical URL (rembg release; stable since 2020).
U2NET_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_url(url: str, dst: Path) -> None:
    """Stream-download a URL to disk with a progress hook."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        print(f"  exists: {dst.relative_to(REPO_ROOT)} ({dst.stat().st_size / 1024**2:.1f} MB)")
        return
    print(f"  fetching: {url}")
    t0 = time.time()
    last_pct = -1

    def _hook(blocks: int, block_size: int, total: int) -> None:
        nonlocal last_pct
        if total <= 0:
            return
        pct = min(100, int(blocks * block_size * 100 / total))
        if pct != last_pct and pct % 10 == 0:
            mb = blocks * block_size / 1024**2
            print(f"    {pct}% ({mb:.1f} MB)")
            last_pct = pct

    tmp = dst.with_suffix(dst.suffix + ".partial")
    urllib.request.urlretrieve(url, tmp, _hook)  # noqa: S310 -- vetted URL
    tmp.replace(dst)
    print(f"  done in {time.time() - t0:.1f}s -> {dst.relative_to(REPO_ROOT)}")


def _hf_snapshot(repo_id: str, local_dir: Path, allow_patterns: list[str] | None = None) -> None:
    """Use huggingface_hub.snapshot_download to populate local_dir."""
    from huggingface_hub import snapshot_download

    print(f"  snapshot: {repo_id} -> {local_dir.relative_to(REPO_ROOT)}")
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        allow_patterns=allow_patterns,
    )


def stage_sdxl_base() -> None:
    print("== SDXL base 1.0 (fp16 only) ==")
    _hf_snapshot(
        "stabilityai/stable-diffusion-xl-base-1.0",
        SDXL_DIR,
        allow_patterns=SDXL_ALLOW_PATTERNS,
    )


def stage_ip_adapter() -> None:
    print("== IP-Adapter SDXL ViT-H + image encoder ==")
    # Pull only the two files we need from the (multi-GB) IP-Adapter repo.
    _hf_snapshot(
        "h94/IP-Adapter",
        IPA_DIR,
        allow_patterns=[
            "sdxl_models/ip-adapter_sdxl_vit-h.safetensors",
            "models/image_encoder/config.json",
            "models/image_encoder/model.safetensors",
        ],
    )


def stage_lora() -> None:
    print("== pixel-art-XL LoRA ==")
    _hf_snapshot(
        "nerijs/pixel-art-xl",
        LORA_DIR,
        allow_patterns=["pixel-art-xl.safetensors"],
    )


def stage_rembg() -> None:
    print("== rembg u2net.onnx ==")
    _download_url(U2NET_URL, BG_DIR / "u2net.onnx")


def write_manifest() -> None:
    print("== sha256 manifest ==")
    targets: dict[str, Path] = {
        "sdxl_unet": SDXL_DIR / "unet" / "diffusion_pytorch_model.fp16.safetensors",
        "sdxl_vae": SDXL_DIR / "vae" / "diffusion_pytorch_model.fp16.safetensors",
        "sdxl_text_encoder": SDXL_DIR / "text_encoder" / "model.fp16.safetensors",
        "sdxl_text_encoder_2": SDXL_DIR / "text_encoder_2" / "model.fp16.safetensors",
        "ip_adapter_sdxl_vit_h": IPA_DIR / "sdxl_models" / "ip-adapter_sdxl_vit-h.safetensors",
        "ip_adapter_image_encoder": IPA_DIR / "models" / "image_encoder" / "model.safetensors",
        "pixel_art_xl_lora": LORA_DIR / "pixel-art-xl.safetensors",
        "rembg_u2net": BG_DIR / "u2net.onnx",
    }

    manifest: dict[str, dict[str, str | int]] = {}
    for name, path in targets.items():
        if not path.exists():
            print(f"  MISSING: {name} ({path.relative_to(REPO_ROOT)})")
            sys.exit(1)
        size = path.stat().st_size
        digest = _sha256(path)
        manifest[name] = {
            "path": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "size_bytes": size,
            "sha256": digest,
        }
        print(f"  {name}: {digest[:16]}... ({size / 1024**2:.1f} MB)")

    out = MODEL_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest written: {out.relative_to(REPO_ROOT)}")


def main() -> int:
    print(f"target: {MODEL_DIR}")
    free_gb = shutil.disk_usage(REPO_ROOT).free / 1024**3
    print(f"free disk: {free_gb:.1f} GB (need ~13 GB for full set)")
    if free_gb < 13:
        print("WARN: low disk; aborting before fetch")
        return 1

    stage_sdxl_base()
    stage_ip_adapter()
    stage_lora()
    stage_rembg()
    write_manifest()
    print("done. data/models/image_gen/ populated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
