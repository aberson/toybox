"""F.5-1 download helper: LCM-LoRA SD 1.5 from HF official."""

from huggingface_hub import snapshot_download

dest = "data/models/image_gen/sd15/lcm_lora"

allow_patterns = [
    "pytorch_lora_weights.safetensors",
    "*.json",
    "README.md",
]

print(f"downloading LCM-LoRA -> {dest}")
snapshot_download(
    repo_id="latent-consistency/lcm-lora-sdv1-5",
    local_dir=dest,
    allow_patterns=allow_patterns,
)
print("LCM-LoRA done")
