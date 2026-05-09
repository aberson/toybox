"""F.5-1 download helper: SD 1.5 base from HF official mirror.

Skip files we don't need: ONNX, CKPT (we use safetensors), training-only files,
non-fp16 variants where fp16 exists.
"""

from huggingface_hub import snapshot_download

dest = "data/models/image_gen/sd15/base"

# Allow only what diffusers needs at fp16 inference time. The model_index.json
# orchestrates the rest.
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

print(f"downloading SD 1.5 base -> {dest}")
snapshot_download(
    repo_id="stable-diffusion-v1-5/stable-diffusion-v1-5",
    local_dir=dest,
    allow_patterns=allow_patterns,
)
print("SD 1.5 base done")
