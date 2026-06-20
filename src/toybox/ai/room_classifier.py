"""On-device ONNX CLIP photo→room-type matcher (Phase X Step X4).

Fully offline, family-private: a local CLIP image encoder scores an
uploaded room photo against the fixed :data:`toybox.core.room_types.ROOM_TYPES`
vocabulary. No cloud, no Claude call. This is the fallback path for
:mod:`toybox.core.room_match` when the filename keyword heuristic can't
identify a room.

Architecture (mirrors :mod:`toybox.audio.stt` and
:mod:`toybox.audio.vad`):

1. :class:`RoomClassifier` holds an **image-encoder ONNX session** plus a
   **cached text-embedding matrix** (one L2-normalized row per ROOM_TYPE,
   in ``ROOM_TYPES`` order). Both are constructor params, so a test can
   build the classifier with a fake session + canned matrix and exercise
   the full ``classify`` pipeline with **no model file** — the load-bearing
   model-free seam.
2. :meth:`RoomClassifier.classify` Pillow-decodes + CLIP-preprocesses the
   image (resize→224, center-crop, RGB, CLIP mean/std normalize → NCHW
   float32), runs the image encoder, L2-normalizes the embedding, and
   returns ``{room_type: cosine_similarity}`` against the cached text
   matrix.
3. :func:`load_default_classifier` lazy-loads the real ONNX session +
   cached ``.npy`` matrix from ``data/models/clip/``, raising
   :class:`RoomClassifierUnavailable` (pointing the operator at
   ``--download``) when the files are missing.
4. The ``--download`` operator entrypoint fetches the pinned ONNX CLIP
   (image + text encoders + tokenizer.json) from HuggingFace and
   **precomputes** the text-embedding matrix once — so RUNTIME never needs
   a tokenizer or the text encoder.

Scoring choice — RAW COSINE SIMILARITY (not softmax). ``classify``
returns the bare dot product of the L2-normalized image embedding with
each L2-normalized text row, i.e. a value in ``[-1, 1]`` (in practice
CLIP image/text cosines cluster in ``~[0.15, 0.35]``). We return raw
cosine rather than a temperature-scaled softmax because the downstream
consumer (:mod:`toybox.core.room_match`) gates on an **absolute
confidence floor** — "is this photo confidently a bedroom?" — and a
softmax would always sum to 1 across the nine labels, hiding the case
where the photo matches *none* of them well. Raw cosine preserves that
absolute signal so the ``CLIP_CONFIDENCE_THRESHOLD`` gate can return N/A.

Text-embedding precompute — the room-type label set is FIXED and small
(9 entries), so we tokenize ``"a photo of a {display_name}"`` for each
ROOM_TYPE, run the text encoder ONCE at download time, L2-normalize, and
persist the resulting ``(9, embed_dim)`` matrix to
``data/models/clip/room_text_embeds.npy`` in ROOM_TYPES order. Runtime
loads that matrix and never touches a tokenizer or text encoder.

Tokenizer choice (download-time only) — we use the model's own
``tokenizer.json`` (fetched alongside the ONNX weights) via the
``tokenizers`` library IF available, falling back to a vendored CLIP BPE
tokenizer is intentionally NOT done here: ``tokenizers`` ships as a
transitive dep of many ML stacks and is the simplest *correct* option
(it reproduces the exact merges the model was trained with). The
download path imports it lazily so the absence of ``tokenizers`` only
affects the operator ``--download`` step, never runtime or tests. If
``tokenizers`` is unavailable at download time we raise a clear error
telling the operator to ``uv add tokenizers``.

PROJECTED-EMBEDDING OUTPUT SELECTION (load-bearing correctness). CLIP's
zero-shot scoring compares an image and a text vector in the *shared*
512-d projected latent space — i.e. ``image_embeds`` and ``text_embeds``,
the post-projection-head outputs. The pinned
``Xenova/clip-vit-base-patch32`` ONNX files used here are the
``*WithProjection`` standalone exports:

* ``onnx/vision_model.onnx`` — a ``CLIPVisionModelWithProjection`` whose
  named outputs include ``image_embeds`` (the 512-d projected vector).
  Depending on the export it may ALSO expose ``last_hidden_state`` at
  output **index 0** — a ``(1, 50, 768)`` raw transformer tensor that is
  NOT in the shared CLIP space.
* ``onnx/text_model.onnx`` — a ``CLIPTextModelWithProjection`` whose named
  outputs include ``text_embeds`` (512-d), again possibly alongside a raw
  ``last_hidden_state`` / ``pooler_output`` at index 0.

Reading ``outputs[0]`` blindly is therefore a silent-death trap: it can
flatten the wrong (raw, 768*50-d) tensor, which then either errors on the
``text_embeds @ image_embed`` matmul (→ CLIP path always degrades to N/A)
or produces meaningless cosines. We instead resolve the output **by
name** — :func:`_resolve_projected_output_index` picks the
``image_embeds`` / ``text_embeds`` output (with a clear error if neither
projected output is present), mirroring the existing input-name
auto-resolution. A model-free test locks this so the dead-path regression
cannot recur. If a future operator pins a model whose only output is a
raw hidden state, ``--download`` and runtime both fail loudly instead of
silently scoring garbage.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from toybox.core.room_types import ROOM_TYPE_DISPLAY_NAMES, ROOM_TYPES, RoomType

_logger = logging.getLogger(__name__)

# Cache layout: the image encoder ONNX + the precomputed text-embedding
# matrix live here. Sits under ``data/models/`` which is gitignored
# wholesale (``data/models/*``), so no extra .gitignore entry is needed.
DEFAULT_CLIP_DIR: Final[Path] = Path("data") / "models" / "clip"

# Filenames inside ``DEFAULT_CLIP_DIR``.
IMAGE_ENCODER_FILENAME: Final[str] = "clip_image_encoder.onnx"
TEXT_ENCODER_FILENAME: Final[str] = "clip_text_encoder.onnx"
TOKENIZER_FILENAME: Final[str] = "tokenizer.json"
TEXT_EMBEDS_FILENAME: Final[str] = "room_text_embeds.npy"

# Pinned model. ``Xenova/clip-vit-base-patch32`` publishes ONNX exports of
# both the image and text encoders under ``onnx/``. Env-overridable so an
# operator behind a mirror (or pinning a different revision) can redirect.
DEFAULT_MODEL_REPO: Final[str] = "Xenova/clip-vit-base-patch32"
MODEL_URLS_ENV: Final[str] = "TOYBOX_CLIP_MODEL_URLS"

# Per-request download timeout (seconds). A stalled HuggingFace connection
# must not hang the operator's --download forever. Env-overridable via
# ``TOYBOX_CLIP_DOWNLOAD_TIMEOUT`` for slow links / large mirrors.
DOWNLOAD_TIMEOUT_ENV: Final[str] = "TOYBOX_CLIP_DOWNLOAD_TIMEOUT"
DEFAULT_DOWNLOAD_TIMEOUT: Final[float] = 120.0

# A real ONNX/tokenizer asset is comfortably larger than this; anything
# smaller is almost certainly an error page (captive portal, 404 body)
# that slipped through with a 200 status.
_MIN_ASSET_BYTES: Final[int] = 1024

# CLIP ViT-B/32 preprocessing constants. These are the published values
# baked into every CLIP release; both producer (this preprocess) and the
# pretrained weights assume them, so they live here as a single source.
CLIP_INPUT_SIZE: Final[int] = 224
CLIP_MEAN: Final[tuple[float, float, float]] = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD: Final[tuple[float, float, float]] = (0.26862954, 0.26130258, 0.27577711)

# Prompt template for the precomputed text embeddings. Single source of
# truth: the download path tokenizes exactly this for every ROOM_TYPE.
TEXT_PROMPT_TEMPLATE: Final[str] = "a photo of a {display_name}"

# Named ONNX outputs carrying the post-projection embeddings in the shared
# 512-d CLIP latent space — the ONLY outputs valid for cross-modal cosine
# scoring. The image encoder (``CLIPVisionModelWithProjection``) emits
# ``image_embeds``; the text encoder (``CLIPTextModelWithProjection``)
# emits ``text_embeds``. We accept either spelling on either session so a
# differently-named export still resolves. See module docstring §
# "PROJECTED-EMBEDDING OUTPUT SELECTION".
IMAGE_EMBED_OUTPUT_NAMES: Final[tuple[str, ...]] = ("image_embeds", "text_embeds")
TEXT_EMBED_OUTPUT_NAMES: Final[tuple[str, ...]] = ("text_embeds", "image_embeds")


class RoomClassifierUnavailable(Exception):
    """Raised when the local CLIP model assets are missing.

    :mod:`toybox.core.room_match` catches this and falls back to the
    filename heuristic / N-A — a missing model must never break upload.
    The message points the operator at the ``--download`` entrypoint.
    """


class _ImageSession(Protocol):
    """Minimal structural type for the injectable ONNX image session.

    Only :meth:`run` is exercised, so a test fake need only implement
    it. The real :class:`onnxruntime.InferenceSession` satisfies this.
    """

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]: ...

    def get_inputs(self) -> list[object]: ...

    def get_outputs(self) -> list[object]: ...


def _l2_normalize(vec: NDArray[np.float32], *, axis: int = -1) -> NDArray[np.float32]:
    """L2-normalize along ``axis``; zero-norm rows pass through unchanged."""
    norm = np.linalg.norm(vec, axis=axis, keepdims=True)
    # Avoid division by zero — a zero embedding stays zero (cosine 0).
    safe = np.where(norm == 0.0, 1.0, norm)
    return (vec / safe).astype(np.float32)


def _resolve_projected_output_index(
    session_outputs: list[object],
    *,
    preferred_names: tuple[str, ...],
    what: str,
) -> int:
    """Return the index of the projected-embedding output, selected BY NAME.

    Scans the session's declared outputs (``session.get_outputs()`` — each
    item exposes a ``.name``) for the first name in ``preferred_names``
    (the projected ``image_embeds`` / ``text_embeds`` spellings). Returns
    its positional index so the corresponding tensor can be plucked from
    ``session.run(...)``'s positional result list.

    This is the guard against the silent-death trap where a raw
    ``last_hidden_state`` sits at output index 0 and a blind ``outputs[0]``
    would flatten the wrong tensor (see module docstring). If NONE of the
    preferred projected outputs are present we raise
    :class:`RoomClassifierUnavailable` with the names we DID see, so a
    mis-pinned model fails loudly instead of scoring garbage.
    """
    names: list[str] = []
    for out in session_outputs:
        name = getattr(out, "name", None)
        names.append(name if isinstance(name, str) else "<unnamed>")
    for wanted in preferred_names:
        if wanted in names:
            return names.index(wanted)
    raise RoomClassifierUnavailable(
        f"the {what} ONNX session exposes no projected-embedding output "
        f"(looked for {preferred_names!r}); got outputs {names!r}. The pinned "
        "model must be a *WithProjection CLIP export that emits image_embeds / "
        "text_embeds — re-run `python -m toybox.ai.room_classifier --download` "
        f"or repin ${MODEL_URLS_ENV} to a projection-bearing export."
    )


def preprocess_image(image_bytes: bytes) -> NDArray[np.float32]:
    """Decode + CLIP-preprocess raw image bytes to an NCHW float32 tensor.

    Pipeline: Pillow-decode → convert RGB → resize the short edge to
    224 (bicubic) → center-crop 224x224 → scale to ``[0, 1]`` → subtract
    CLIP mean / divide CLIP std per channel → transpose HWC→CHW → add
    batch dim → ``(1, 3, 224, 224)`` float32.

    NOTE: this is **model preprocessing**, not upload validation. Upload
    MIME-sniff / dimension / size validation belongs in
    :mod:`toybox.storage.images` (:func:`validate_upload`) and is run by
    the upload router *before* bytes ever reach the classifier. We open
    the bytes here only to decode pixels for the encoder; we deliberately
    do not re-run validation (no duplicate source of truth) and rely on
    Pillow raising on a corrupt buffer, which the caller converts to a
    safe fallback.
    """
    with Image.open(io.BytesIO(image_bytes)) as opened:
        rgb = opened.convert("RGB")
        width, height = rgb.size
        # Resize the SHORT edge to CLIP_INPUT_SIZE, preserving aspect, then
        # center-crop — the canonical CLIP preprocessing.
        short_edge = min(width, height)
        if short_edge <= 0:  # pragma: no cover — defensive; Pillow rejects first
            raise ValueError("image has a zero-length edge")
        scale = CLIP_INPUT_SIZE / float(short_edge)
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = rgb.resize(new_size, Image.Resampling.BICUBIC)
        rw, rh = resized.size
        left = (rw - CLIP_INPUT_SIZE) // 2
        top = (rh - CLIP_INPUT_SIZE) // 2
        cropped = resized.crop((left, top, left + CLIP_INPUT_SIZE, top + CLIP_INPUT_SIZE))
        arr = np.asarray(cropped, dtype=np.float32) / 255.0  # (224, 224, 3) in [0,1]

    mean = np.array(CLIP_MEAN, dtype=np.float32)
    std = np.array(CLIP_STD, dtype=np.float32)
    arr = (arr - mean) / std
    # HWC -> CHW -> NCHW.
    chw = np.transpose(arr, (2, 0, 1))
    return np.expand_dims(chw, axis=0).astype(np.float32)


class RoomClassifier:
    """Local CLIP zero-shot room-type scorer.

    Both the image-encoder session AND the text-embedding matrix are
    injected — the load-bearing seam that lets tests run model-free.
    Production constructs it via :func:`load_default_classifier`.

    ``text_embeds`` must be a ``(len(ROOM_TYPES), embed_dim)`` float32
    matrix whose rows are L2-normalized and ordered to match
    :data:`ROOM_TYPES`. The constructor re-normalizes defensively so a
    caller passing un-normalized rows still gets valid cosine scores.
    """

    def __init__(
        self,
        *,
        image_session: _ImageSession,
        text_embeds: NDArray[np.float32],
        image_input_name: str | None = None,
    ) -> None:
        embeds = np.asarray(text_embeds, dtype=np.float32)
        if embeds.ndim != 2 or embeds.shape[0] != len(ROOM_TYPES):
            raise ValueError(
                "text_embeds must be a (len(ROOM_TYPES), embed_dim) matrix, "
                f"got shape {embeds.shape} for {len(ROOM_TYPES)} room types"
            )
        self._image_session = image_session
        # Defensive re-normalize so cosine == dot product holds.
        self._text_embeds = _l2_normalize(embeds, axis=1)
        self._image_input_name = image_input_name
        # Resolved lazily from the session's named outputs on first embed —
        # picks the projected ``image_embeds`` output, NOT a raw index-0
        # ``last_hidden_state`` (see _resolve_projected_output_index).
        self._image_output_index: int | None = None

    def _resolve_input_name(self) -> str:
        if self._image_input_name is not None:
            return self._image_input_name
        # Discover from the session — the real InferenceSession exposes
        # ``get_inputs()[0].name`` ("pixel_values" for Xenova CLIP).
        inputs = self._image_session.get_inputs()
        name = getattr(inputs[0], "name", None)
        if not isinstance(name, str):  # pragma: no cover — defensive
            raise RoomClassifierUnavailable(
                "could not resolve the image-encoder input name from the ONNX session"
            )
        self._image_input_name = name
        return name

    def _resolve_output_index(self) -> int:
        """Resolve (and cache) the projected ``image_embeds`` output index."""
        if self._image_output_index is None:
            self._image_output_index = _resolve_projected_output_index(
                self._image_session.get_outputs(),
                preferred_names=IMAGE_EMBED_OUTPUT_NAMES,
                what="image-encoder",
            )
        return self._image_output_index

    def embed_image(self, image_bytes: bytes) -> NDArray[np.float32]:
        """Return the L2-normalized image embedding (1-D float32).

        Plucks the projected ``image_embeds`` output BY NAME (not a blind
        ``outputs[0]``) so a raw ``last_hidden_state`` at index 0 can never
        be flattened into garbage — see the module docstring.
        """
        pixel_values = preprocess_image(image_bytes)
        input_name = self._resolve_input_name()
        output_index = self._resolve_output_index()
        outputs = self._image_session.run(None, {input_name: pixel_values})
        embedding = np.asarray(outputs[output_index], dtype=np.float32).reshape(1, -1)
        normalized: NDArray[np.float32] = _l2_normalize(embedding, axis=1)[0]
        return normalized

    def classify(self, image_bytes: bytes) -> dict[str, float]:
        """Return ``{room_type: raw_cosine_similarity}`` for every ROOM_TYPE.

        Keys are the :data:`ROOM_TYPES` strings; values are the dot product
        of the L2-normalized image embedding with each L2-normalized text
        row (raw cosine in ``[-1, 1]``). The dict preserves ROOM_TYPES
        order. See the module docstring for why raw cosine over softmax.
        """
        image_embed = self.embed_image(image_bytes)
        # (9, d) @ (d,) -> (9,) cosine similarities.
        scores = self._text_embeds @ image_embed
        return {
            room_type: float(score) for room_type, score in zip(ROOM_TYPES, scores, strict=True)
        }


# ----------------------------------------------------------------------
# Default (production) loader
# ----------------------------------------------------------------------


def load_default_classifier(clip_dir: Path | None = None) -> RoomClassifier:
    """Lazy-load the real image encoder + cached text matrix from disk.

    Reads ``clip_image_encoder.onnx`` and ``room_text_embeds.npy`` from
    ``clip_dir`` (default :data:`DEFAULT_CLIP_DIR`). Raises
    :class:`RoomClassifierUnavailable` — pointing the operator at
    ``python -m toybox.ai.room_classifier --download`` — when either is
    missing. ``onnxruntime`` is imported lazily so module import (and the
    model-free test path) never pays for it.
    """
    base = clip_dir if clip_dir is not None else DEFAULT_CLIP_DIR
    image_path = base / IMAGE_ENCODER_FILENAME
    embeds_path = base / TEXT_EMBEDS_FILENAME

    missing = [p.name for p in (image_path, embeds_path) if not p.is_file()]
    if missing:
        raise RoomClassifierUnavailable(
            f"local CLIP assets missing from {base!s}: {', '.join(missing)}. "
            "Run `python -m toybox.ai.room_classifier --download` to fetch them."
        )

    text_embeds = np.load(embeds_path).astype(np.float32)
    if text_embeds.shape[0] != len(ROOM_TYPES):
        raise RoomClassifierUnavailable(
            f"cached {TEXT_EMBEDS_FILENAME} has {text_embeds.shape[0]} rows but "
            f"{len(ROOM_TYPES)} room types are defined — re-run --download after a "
            "room-type vocabulary change."
        )

    import onnxruntime as ort  # noqa: PLC0415 — lazy: never paid for in tests

    session = ort.InferenceSession(
        str(image_path),
        providers=["CPUExecutionProvider"],
    )
    return RoomClassifier(image_session=session, text_embeds=text_embeds)


# ----------------------------------------------------------------------
# --download operator entrypoint (NOT run in tests/CI)
# ----------------------------------------------------------------------


def _model_urls() -> dict[str, str]:
    """Resolve the {image, text, tokenizer} download URLs.

    ``TOYBOX_CLIP_MODEL_URLS`` may override as a comma-separated
    ``key=url`` list (keys: ``image``, ``text``, ``tokenizer``).
    Otherwise we build HuggingFace ``resolve/main/...`` URLs for the
    pinned :data:`DEFAULT_MODEL_REPO`.
    """
    base = f"https://huggingface.co/{DEFAULT_MODEL_REPO}/resolve/main"
    urls = {
        "image": f"{base}/onnx/vision_model.onnx",
        "text": f"{base}/onnx/text_model.onnx",
        "tokenizer": f"{base}/tokenizer.json",
    }
    raw = os.environ.get(MODEL_URLS_ENV)
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, _, value = pair.partition("=")
            key = key.strip()
            value = value.strip()
            if key in urls and value:
                urls[key] = value
    return urls


def _download_timeout() -> float:
    """Resolve the download timeout (seconds); env-overridable, fail-safe."""
    raw = os.environ.get(DOWNLOAD_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_DOWNLOAD_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "ignoring non-numeric %s=%r; using default %.0fs",
            DOWNLOAD_TIMEOUT_ENV,
            raw,
            DEFAULT_DOWNLOAD_TIMEOUT,
        )
        return DEFAULT_DOWNLOAD_TIMEOUT
    return value if value > 0 else DEFAULT_DOWNLOAD_TIMEOUT


def _download_file(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` via urllib (no extra HTTP dep).

    Robustness (LOW finding): a per-request ``timeout`` so a stalled HF
    connection can't hang forever; the ``.part`` temp file is removed on
    ANY mid-stream failure (try/finally) so a half-written file is never
    left behind or promoted; and a basic sanity check rejects an HTML
    error body (captive portal / 404) or a suspiciously tiny payload
    masquerading as an ``.onnx``.
    """
    _logger.info("downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "toybox-room-classifier"})
    timeout = _download_timeout()
    written = 0
    promoted = False
    try:
        with (
            urllib.request.urlopen(req, timeout=timeout) as resp,  # noqa: S310 — pinned HF host
            tmp.open("wb") as fh,
        ):
            status = getattr(resp, "status", None)
            if status is not None and status != 200:
                raise OSError(f"download of {url} returned HTTP {status}")
            content_type = resp.headers.get("Content-Type", "") if resp.headers else ""
            if "text/html" in content_type.lower():
                raise OSError(
                    f"download of {url} returned an HTML body "
                    f"(Content-Type={content_type!r}) — likely an error page, "
                    "not the model asset"
                )
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
        if written < _MIN_ASSET_BYTES:
            raise OSError(
                f"download of {url} produced only {written} bytes "
                f"(< {_MIN_ASSET_BYTES}); treating as a failed/error response"
            )
        os.replace(tmp, dest)
        promoted = True
    finally:
        if not promoted:
            tmp.unlink(missing_ok=True)


def _precompute_text_embeds(
    *,
    text_encoder_path: Path,
    tokenizer_path: Path,
) -> NDArray[np.float32]:
    """Tokenize + encode the per-ROOM_TYPE prompts into an L2-normed matrix.

    Returns a ``(len(ROOM_TYPES), embed_dim)`` float32 matrix in
    ROOM_TYPES order. Uses the model's own ``tokenizer.json`` via the
    ``tokenizers`` library — the simplest correct tokenizer (reproduces
    the exact merges the CLIP text encoder was trained with). Imports are
    lazy + download-time-only.
    """
    try:
        from tokenizers import Tokenizer  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — operator-env dependent
        raise RoomClassifierUnavailable(
            "the `tokenizers` package is required at --download time to precompute "
            "text embeddings. Install it with `uv add tokenizers`, then re-run "
            "`python -m toybox.ai.room_classifier --download`."
        ) from exc

    import onnxruntime as ort  # noqa: PLC0415 — download-time only

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    # CLIP text encoders expect a fixed context length of 77.
    tokenizer.enable_padding(length=77)
    tokenizer.enable_truncation(max_length=77)

    prompts = [
        TEXT_PROMPT_TEMPLATE.format(display_name=ROOM_TYPE_DISPLAY_NAMES[RoomType(rt)].lower())
        for rt in ROOM_TYPES
    ]
    encodings = tokenizer.encode_batch(prompts)
    input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
    attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

    session = ort.InferenceSession(str(text_encoder_path), providers=["CPUExecutionProvider"])
    feed: dict[str, NDArray[np.int64]] = {"input_ids": input_ids}
    # Some exports also require attention_mask — feed it when present.
    input_names = {inp.name for inp in session.get_inputs()}
    if "attention_mask" in input_names:
        feed["attention_mask"] = attention_mask
    # Select the projected ``text_embeds`` output BY NAME — same silent-death
    # guard as the image path: a raw ``last_hidden_state`` / ``pooler_output``
    # at index 0 would otherwise be flattened into a non-CLIP-space matrix.
    output_index = _resolve_projected_output_index(
        session.get_outputs(),
        preferred_names=TEXT_EMBED_OUTPUT_NAMES,
        what="text-encoder",
    )
    outputs = session.run(None, feed)
    text_embeds = np.asarray(outputs[output_index], dtype=np.float32).reshape(len(ROOM_TYPES), -1)
    return _l2_normalize(text_embeds, axis=1)


def download_assets(clip_dir: Path | None = None) -> Path:
    """Fetch the ONNX CLIP encoders + tokenizer and precompute text embeds.

    Returns the populated ``clip_dir``. Idempotent for the downloads
    (re-uses existing files) but always recomputes the text-embedding
    matrix so a room-type vocabulary change is picked up.
    """
    base = clip_dir if clip_dir is not None else DEFAULT_CLIP_DIR
    base.mkdir(parents=True, exist_ok=True)
    urls = _model_urls()

    targets = {
        "image": base / IMAGE_ENCODER_FILENAME,
        "text": base / TEXT_ENCODER_FILENAME,
        "tokenizer": base / TOKENIZER_FILENAME,
    }
    for key, dest in targets.items():
        if dest.is_file():
            _logger.info("%s already present at %s; skipping download", key, dest)
            continue
        _download_file(urls[key], dest)

    _logger.info("precomputing text embeddings for %d room types", len(ROOM_TYPES))
    text_embeds = _precompute_text_embeds(
        text_encoder_path=targets["text"],
        tokenizer_path=targets["tokenizer"],
    )
    embeds_path = base / TEXT_EMBEDS_FILENAME
    np.save(embeds_path, text_embeds)
    _logger.info("wrote %s (shape=%s)", embeds_path, text_embeds.shape)
    return base


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.ai.room_classifier",
        description=(
            "Local ONNX CLIP room-type matcher operator entrypoint. Use "
            "--download to fetch the pinned model and precompute the fixed "
            "room-type text embeddings into data/models/clip/."
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            f"Fetch the pinned CLIP ONNX encoders ({DEFAULT_MODEL_REPO}; "
            f"override via ${MODEL_URLS_ENV}) into {DEFAULT_CLIP_DIR} and "
            "precompute room_text_embeds.npy. Exits cleanly when done."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.download:
        parser.print_help()
        return 0
    try:
        base = download_assets()
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        _logger.exception("room-classifier --download failed")
        print(
            f"download failed: dir={DEFAULT_CLIP_DIR}, exc_type={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(f"CLIP room-classifier assets ready in {base}")
    return 0


if __name__ == "__main__":  # pragma: no cover — operator entry
    raise SystemExit(main())


__all__ = [
    "CLIP_INPUT_SIZE",
    "CLIP_MEAN",
    "CLIP_STD",
    "DEFAULT_CLIP_DIR",
    "DEFAULT_DOWNLOAD_TIMEOUT",
    "DEFAULT_MODEL_REPO",
    "DOWNLOAD_TIMEOUT_ENV",
    "IMAGE_EMBED_OUTPUT_NAMES",
    "IMAGE_ENCODER_FILENAME",
    "MODEL_URLS_ENV",
    "TEXT_EMBED_OUTPUT_NAMES",
    "TEXT_EMBEDS_FILENAME",
    "RoomClassifier",
    "RoomClassifierUnavailable",
    "download_assets",
    "load_default_classifier",
    "main",
    "preprocess_image",
]
