"""F.5-1 load-only smoke: confirms checkpoints load without OOM.

Tests both cartoon-mode-checkpoint (Mode A) and cartoon-mode-LoRA (Mode B)
configurations. Mode B requires a cartoon_lora to be present; if it isn't,
the script reports skip and exits 0 (the cartoon LoRA is optional per F.5-1).

This is the load-only check from F.5-1's Done when. The full pipeline
inference is exercised by F.5-2's @pytest.mark.requires_gpu integration test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch
from diffusers import LCMScheduler, StableDiffusionPipeline

MODEL_DIR = Path("data/models/image_gen")


def smoke_mode_a() -> bool:
    """Mode A: cartoon checkpoint + LCM-LoRA stacked."""
    print("=== Mode A: cartoon checkpoint + LCM-LoRA ===")
    try:
        pipe = StableDiffusionPipeline.from_pretrained(
            str(MODEL_DIR / "cartoon_checkpoint"),
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
            safety_checker=None,           # toy-sprite use case; checker adds VRAM + false positives
            requires_safety_checker=False,
        )
        pipe.load_lora_weights(
            str(MODEL_DIR / "sd15" / "lcm_lora"),
            adapter_name="lcm",
        )
        pipe.set_adapters(["lcm"], adapter_weights=[1.0])
        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)

        # Don't move to CUDA in the smoke (we just want to confirm load
        # succeeds without OOM at construction time; CUDA-move is exercised
        # by the real pipeline integration test).
        components_loaded = sorted(pipe.components.keys())
        print(f"loaded ok; components: {components_loaded}")

        # Free memory before mode B
        del pipe
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return True
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return False


def smoke_mode_b() -> bool:
    """Mode B: SD 1.5 base + cartoon LoRA + LCM-LoRA."""
    print("\n=== Mode B: SD 1.5 base + cartoon LoRA + LCM-LoRA ===")
    cartoon_lora = MODEL_DIR / "cartoon_lora"
    if not any(cartoon_lora.glob("*.safetensors")):
        print(f"SKIPPED: no cartoon LoRA at {cartoon_lora} (optional A/B alt)")
        return True  # skip is OK; mode B is optional
    try:
        pipe = StableDiffusionPipeline.from_pretrained(
            str(MODEL_DIR / "sd15" / "base"),
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
            safety_checker=None,
            requires_safety_checker=False,
        )
        pipe.load_lora_weights(
            str(cartoon_lora),
            adapter_name="cartoon",
        )
        pipe.load_lora_weights(
            str(MODEL_DIR / "sd15" / "lcm_lora"),
            adapter_name="lcm",
        )
        pipe.set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])
        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
        components_loaded = sorted(pipe.components.keys())
        print(f"loaded ok; components: {components_loaded}")
        del pipe
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return True
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    a_ok = smoke_mode_a()
    b_ok = smoke_mode_b()
    print()
    if a_ok and b_ok:
        print("F.5-1 load smoke: PASS")
        return 0
    print("F.5-1 load smoke: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
