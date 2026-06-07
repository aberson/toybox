"""Compare animation approaches A, B, C on two test toys.

Option A: CSS animation demo — generates an HTML page showing the existing
          static PNGs with keyframe animations applied.
Option B: AnimateDiff img2img — starts from the existing static PNG, applies
          motion at a configurable strength. Identity is preserved because
          the model denoises from a known-good frame rather than hallucinating.
Option C: Stable Video Diffusion — img2vid-xt; specifically designed for
          image-to-video with strong identity preservation.

Output: data/images/compare/{approach}/{toy_name}_{slot}.webp (B, C)
        data/images/compare/a/demo.html (A)

Usage:
    uv run python scripts/compare_animate.py --approach b
    uv run python scripts/compare_animate.py --approach b --strength 0.5
    uv run python scripts/compare_animate.py --approach c [--svd-path PATH]
    uv run python scripts/compare_animate.py --approach a
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

# Two test toys: Ax (axolotl) and Brown Bear
_TEST_TOYS = [
    {
        "toy_id": "2a59010ee89e4932bae4289d6de46977",
        "name": "Ax",
        "slot": "idle",
    },
    {
        "toy_id": "3f28321977e64f979d032eb0b16ea668",
        "name": "Brown_Bear",
        "slot": "idle",
    },
]

_STATIC_DIR = _ROOT / "data" / "images" / "toy_actions"
_OUT_DIR = _ROOT / "data" / "images" / "compare"

_DEFAULT_SVD_PATH = _ROOT / "data" / "models" / "image_gen" / "svd"
_DEFAULT_MOTION_ADAPTER = _ROOT / "data" / "models" / "image_gen" / "animatelcm"
_DEFAULT_BASE_MODEL = _ROOT / "data" / "models" / "image_gen" / "sd15" / "base"
_DEFAULT_IP_ADAPTER = _ROOT / "data" / "models" / "image_gen" / "ip_adapter"


# ---------------------------------------------------------------------------
# Option A — CSS animation HTML demo
# ---------------------------------------------------------------------------

_CSS_ANIMATIONS = {
    "idle": "breathe 3s ease-in-out infinite",
    "pointing": "sway 1.5s ease-in-out infinite",
    "looking": "nod 2s ease-in-out infinite",
    "jumping": "bounce 0.8s ease-in-out infinite",
    "cheering": "wiggle 0.6s ease-in-out infinite",
    "thinking": "tilt 2s ease-in-out infinite",
    "waving": "wave 1s ease-in-out infinite",
    "running": "run 0.5s ease-in-out infinite",
    "sleeping": "breathe 5s ease-in-out infinite",
    "confused": "tilt 1.5s ease-in-out infinite alternate",
}

_CSS_KEYFRAMES = """
@keyframes breathe {
  0%, 100% { transform: scale(1.0); }
  50%       { transform: scale(1.04); }
}
@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  40%       { transform: translateY(-18px); }
  60%       { transform: translateY(-8px); }
}
@keyframes sway {
  0%, 100% { transform: rotate(-4deg); }
  50%       { transform: rotate(4deg); }
}
@keyframes nod {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  30%       { transform: translateY(-6px) rotate(-3deg); }
  60%       { transform: translateY(0) rotate(2deg); }
}
@keyframes wiggle {
  0%, 100% { transform: rotate(-6deg) scale(1.0); }
  50%       { transform: rotate(6deg) scale(1.05); }
}
@keyframes tilt {
  0%, 100% { transform: rotate(-8deg); }
  50%       { transform: rotate(8deg); }
}
@keyframes wave {
  0%   { transform: rotate(0deg) translateY(0); }
  25%  { transform: rotate(-10deg) translateY(-4px); }
  50%  { transform: rotate(10deg) translateY(-8px); }
  75%  { transform: rotate(-10deg) translateY(-4px); }
  100% { transform: rotate(0deg) translateY(0); }
}
@keyframes run {
  0%, 100% { transform: translateX(0) rotate(-3deg); }
  50%       { transform: translateX(4px) rotate(3deg); }
}
"""


def run_a() -> None:
    out_dir = _OUT_DIR / "a"
    out_dir.mkdir(parents=True, exist_ok=True)

    cards = []
    for toy in _TEST_TOYS:
        tid = toy["toy_id"]
        name = toy["name"]
        for slot, anim in _CSS_ANIMATIONS.items():
            png = _STATIC_DIR / tid / f"{slot}.png"
            if not png.exists():
                continue
            # Use relative path from the HTML file's location for src
            rel = f"../../../toy_actions/{tid}/{slot}.png"
            cards.append(
                f'<div class="card">'
                f'<img src="{rel}" style="animation: {anim}" />'
                f'<p>{name} — {slot}</p>'
                f"</div>"
            )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Option A — CSS animated sprites</title>
<style>
body {{ background: #1a1a2e; display: flex; flex-wrap: wrap; gap: 12px; padding: 16px; }}
.card {{ display: flex; flex-direction: column; align-items: center; background: #16213e;
         border-radius: 8px; padding: 8px; }}
.card img {{ width: 112px; height: 112px; object-fit: contain; transform-origin: center bottom; }}
.card p {{ color: #eee; font: 11px sans-serif; margin: 4px 0 0; text-align: center; }}
{_CSS_KEYFRAMES}
</style></head>
<body>{"".join(cards)}</body></html>
"""
    (out_dir / "demo.html").write_text(html, encoding="utf-8")
    print(f"Option A: wrote {out_dir / 'demo.html'}")
    print("Open in a browser to see all CSS animations live.")


# ---------------------------------------------------------------------------
# Option B — AnimateDiff img2img
# ---------------------------------------------------------------------------

def _save_webp(frames: list, out_path: Path, fps: int = 8) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = 1000 // fps
    frames[0].save(
        out_path,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=duration,
    )
    print(f"  saved {out_path} ({len(frames)} frames @ {fps}fps)")


def _save_frame_png(frames: list, out_path: Path, frame_idx: int = 8) -> None:
    """Save a representative mid-animation frame as PNG for easy comparison."""
    idx = min(frame_idx, len(frames) - 1)
    png_path = out_path.with_suffix(".png")
    frames[idx].save(png_path, format="PNG")
    print(f"  preview frame → {png_path}")


def run_b(strength: float = 0.7, ip_scale: float = 0.85) -> None:
    import torch
    from diffusers import AnimateDiffVideoToVideoPipeline, LCMScheduler, MotionAdapter
    from PIL import Image

    print(f"Option B: AnimateDiff video-to-video (strength={strength}, ip_scale={ip_scale})")

    adapter = MotionAdapter.from_pretrained(  # type: ignore[no-untyped-call]
        str(_DEFAULT_MOTION_ADAPTER), torch_dtype=torch.float16
    )
    pipe = AnimateDiffVideoToVideoPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        str(_DEFAULT_BASE_MODEL),
        motion_adapter=adapter,
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
        local_files_only=True,
    )
    pipe.scheduler = LCMScheduler.from_config(  # type: ignore[no-untyped-call]
        pipe.scheduler.config, beta_schedule="linear"
    )
    pipe.load_ip_adapter(
        str(_DEFAULT_IP_ADAPTER), subfolder="models", weight_name="ip-adapter-plus_sd15.bin"
    )
    pipe.set_ip_adapter_scale(ip_scale)
    pipe.to("cuda")
    pipe.vae.enable_slicing()

    out_dir = _OUT_DIR / "b"
    out_dir.mkdir(parents=True, exist_ok=True)

    for toy in _TEST_TOYS:
        tid = toy["toy_id"]
        name = toy["name"]
        slot = toy["slot"]

        static_png = _STATIC_DIR / tid / f"{slot}.png"
        if not static_png.exists():
            print(f"  SKIP {name}/{slot} — no static PNG")
            continue

        # Load existing static sprite as starting frame.
        static_img = Image.open(static_png).convert("RGBA")
        # Composite over white so the model sees clean RGB without alpha channel issues.
        bg = Image.new("RGB", static_img.size, (255, 255, 255))
        bg.paste(static_img, mask=static_img.split()[3])
        input_img = bg.resize((256, 256))

        # VideoToVideo takes a list of frames as the initial "video".
        # Repeat the single static sprite for all 16 frames — the model then
        # applies motion while starting from this identity-preserving baseline.
        num_frames = 16
        input_frames = [input_img] * num_frames

        print(f"  generating {name}/{slot} …")
        output = pipe(
            video=input_frames,
            ip_adapter_image=input_img,
            prompt=f"cartoon character, {slot}, cute, expressive, {name.lower()}",
            negative_prompt="photorealistic, 3d render, blurry, watermark, text, distorted",
            strength=strength,
            guidance_scale=1.0,
            num_inference_steps=8,
            generator=torch.Generator("cuda").manual_seed(42),
        )

        frames = output.frames[0]
        out_path = out_dir / f"{name}_{slot}.webp"
        _save_webp(frames, out_path)
        _save_frame_png(frames, out_path)


# ---------------------------------------------------------------------------
# Option C — Stable Video Diffusion
# ---------------------------------------------------------------------------

def run_c(svd_path: str | None = None, download: bool = False) -> None:
    import torch
    from diffusers import StableVideoDiffusionPipeline
    from PIL import Image

    model_path = Path(svd_path) if svd_path else _DEFAULT_SVD_PATH

    if download and not model_path.exists():
        print("Option C: downloading SVD model (~10GB) …")
        from huggingface_hub import snapshot_download  # type: ignore[import]
        snapshot_download(
            "stabilityai/stable-video-diffusion-img2vid-xt",
            local_dir=str(model_path),
        )

    if not model_path.exists():
        print(f"Option C: SVD model not found at {model_path}")
        print("Re-run with --download to fetch it (~10GB).")
        return

    print("Option C: Stable Video Diffusion img2vid-xt")
    pipe = StableVideoDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        str(model_path), torch_dtype=torch.float16, variant="fp16"
    )
    pipe.to("cuda")
    pipe.enable_model_cpu_offload()

    out_dir = _OUT_DIR / "c"
    out_dir.mkdir(parents=True, exist_ok=True)

    for toy in _TEST_TOYS:
        tid = toy["toy_id"]
        name = toy["name"]
        slot = toy["slot"]

        static_png = _STATIC_DIR / tid / f"{slot}.png"
        if not static_png.exists():
            print(f"  SKIP {name}/{slot} — no static PNG")
            continue

        # SVD expects 1024×576 or similar. We'll use 512×512 square and let
        # the model figure out motion — square is fine for xt variant.
        static_img = Image.open(static_png).convert("RGBA")
        bg = Image.new("RGB", static_img.size, (255, 255, 255))
        bg.paste(static_img, mask=static_img.split()[3])
        input_img = bg.resize((512, 512))

        print(f"  generating {name}/{slot} …")
        output = pipe(
            input_img,
            num_frames=16,
            num_inference_steps=25,
            decode_chunk_size=4,
            generator=torch.Generator("cuda").manual_seed(42),
        )

        frames = output.frames[0]
        out_path = out_dir / f"{name}_{slot}.webp"
        _save_webp(frames, out_path, fps=8)
        _save_frame_png(frames, out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Compare animation approaches A/B/C.")
    p.add_argument("--approach", choices=["a", "b", "c"], required=True)
    p.add_argument("--strength", type=float, default=0.7,
                   help="Option B: denoising strength (0=frozen, 1=full gen). Default 0.7")
    p.add_argument("--ip-scale", type=float, default=0.85,
                   help="Option B: IP-Adapter scale. Default 0.85")
    p.add_argument("--svd-path", metavar="PATH",
                   help="Option C: path to SVD model dir.")
    p.add_argument("--download", action="store_true",
                   help="Option C: download SVD from HuggingFace if not present.")
    args = p.parse_args()

    if args.approach == "a":
        run_a()
    elif args.approach == "b":
        run_b(strength=args.strength, ip_scale=args.ip_scale)
    else:
        run_c(svd_path=args.svd_path, download=args.download)


if __name__ == "__main__":
    main()
