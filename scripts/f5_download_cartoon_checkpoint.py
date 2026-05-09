"""F.5-1 download helper: cartoon SD 1.5 checkpoint candidate.

Lykon/dreamshaper-7 is HF-hosted with a permissive CreativeML OpenRAIL-M
license — the operator's fallback if civitai is undesirable. It's a strong
SD-1.5 fine-tune that produces smoother, slightly stylized output (not as
strongly cartoon as ToonYou but solid for our use case where action verb
legibility matters more than aesthetic perfection).
"""

from huggingface_hub import snapshot_download

dest = "data/models/image_gen/cartoon_checkpoint"

allow_patterns = [
    "model_index.json",
    "scheduler/*.json",
    "text_encoder/config.json",
    "text_encoder/model.fp16.safetensors",
    "tokenizer/*",
    "unet/config.json",
    "unet/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.fp16.safetensors",
    "feature_extractor/*",
    "safety_checker/config.json",
]

print(f"downloading dreamshaper-7 -> {dest}")
snapshot_download(
    repo_id="Lykon/dreamshaper-7",
    local_dir=dest,
    allow_patterns=allow_patterns,
)
print("cartoon checkpoint done")
