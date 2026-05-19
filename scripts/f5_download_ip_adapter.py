"""Phase P download helper: IP-Adapter Plus SD 1.5 + CLIP ViT-L image encoder.

`h94/IP-Adapter` hosts many adapter variants; we only need the SD 1.5 "plus"
weights plus the image encoder the adapter conditions on.
"""

from huggingface_hub import snapshot_download

dest = "data/models/image_gen/ip_adapter"

allow_patterns = [
    "models/ip-adapter-plus_sd15.bin",
    "models/image_encoder/*",
]

print(f"downloading IP-Adapter Plus -> {dest}")
snapshot_download(
    repo_id="h94/IP-Adapter",
    local_dir=dest,
    allow_patterns=allow_patterns,
)
print("IP-Adapter Plus done")
