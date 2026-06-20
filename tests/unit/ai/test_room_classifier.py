"""Model-free unit coverage for :mod:`toybox.ai.room_classifier` (Phase X X4).

The whole point of the injectable seam is that these tests run with NO
ONNX model file and NO download: a fake image session returns a canned
embedding, and a canned text-embedding matrix stands in for the cached
``.npy`` matrix. The only real Pillow work is decoding a tiny in-memory
PNG to exercise the preprocessing shape.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image

from toybox.ai.room_classifier import (
    CLIP_INPUT_SIZE,
    RoomClassifier,
    RoomClassifierUnavailable,
    load_default_classifier,
    preprocess_image,
)
from toybox.core.room_types import ROOM_TYPES


def _tiny_png(
    color: tuple[int, int, int] = (120, 30, 200),
    size: tuple[int, int] = (8, 5),
) -> bytes:
    """Encode a tiny non-square RGB PNG in memory (no disk, no model)."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeImageSession:
    """Fake ONNX session: returns a canned embedding regardless of input.

    Exposes a single named output ``image_embeds`` (the projected output)
    so the by-name resolver picks it at index 0.
    """

    def __init__(self, embedding: NDArray[np.float32], input_name: str = "pixel_values") -> None:
        self._embedding = embedding
        self._input_name = input_name
        self.run_calls: list[dict[str, NDArray[np.float32]]] = []

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]:
        self.run_calls.append(input_feed)
        return [self._embedding.reshape(1, -1)]

    def get_inputs(self) -> list[object]:
        class _Inp:
            name = self._input_name

        return [_Inp()]

    def get_outputs(self) -> list[object]:
        class _Out:
            name = "image_embeds"

        return [_Out()]


class _NamedOutput:
    """A minimal ONNX output descriptor exposing only ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name


class _MultiOutputSession:
    """Fake session with MULTIPLE named outputs in a fixed order.

    ``run`` returns one array per declared output (same order as
    ``get_outputs``), so a by-name resolver must index correctly. The
    ``image_embeds`` tensor is the projected 512-d-style vector; the
    index-0 ``last_hidden_state`` is the raw-encoder trap.
    """

    def __init__(
        self,
        outputs: list[tuple[str, NDArray[np.float32]]],
        input_name: str = "pixel_values",
    ) -> None:
        self._outputs = outputs
        self._input_name = input_name

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]:
        return [arr for _name, arr in self._outputs]

    def get_inputs(self) -> list[object]:
        return [_NamedOutput(self._input_name)]

    def get_outputs(self) -> list[object]:
        return [_NamedOutput(name) for name, _arr in self._outputs]


def test_preprocess_image_produces_nchw_224_float32() -> None:
    tensor = preprocess_image(_tiny_png())
    assert tensor.shape == (1, 3, CLIP_INPUT_SIZE, CLIP_INPUT_SIZE)
    assert tensor.dtype == np.float32


def test_classify_returns_score_per_room_type_in_order() -> None:
    embed_dim = 4
    # Image embedding aligned with the "kitchen" row so it scores highest.
    kitchen_idx = ROOM_TYPES.index("kitchen")
    image_embed = np.zeros(embed_dim, dtype=np.float32)
    image_embed[0] = 1.0

    text_embeds = np.full((len(ROOM_TYPES), embed_dim), 0.01, dtype=np.float32)
    text_embeds[kitchen_idx, 0] = 1.0  # this row points straight at the image

    clf = RoomClassifier(
        image_session=_FakeImageSession(image_embed),
        text_embeds=text_embeds,
    )
    scores = clf.classify(_tiny_png())

    # One score per ROOM_TYPE, in ROOM_TYPES order.
    assert list(scores.keys()) == list(ROOM_TYPES)
    # Sane cosine range.
    assert all(-1.0001 <= v <= 1.0001 for v in scores.values())
    # The aligned row is the unambiguous top label.
    top = max(scores, key=lambda k: scores[k])
    assert top == "kitchen"
    assert scores["kitchen"] > 0.9


def test_classify_passes_preprocessed_input_to_session() -> None:
    embed_dim = 3
    session = _FakeImageSession(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    text_embeds = np.eye(len(ROOM_TYPES), embed_dim, dtype=np.float32)
    clf = RoomClassifier(image_session=session, text_embeds=text_embeds)

    clf.classify(_tiny_png())

    assert len(session.run_calls) == 1
    fed = session.run_calls[0]["pixel_values"]
    assert fed.shape == (1, 3, CLIP_INPUT_SIZE, CLIP_INPUT_SIZE)
    assert fed.dtype == np.float32


def test_text_embeds_wrong_row_count_rejected() -> None:
    with pytest.raises(ValueError, match="ROOM_TYPES"):
        RoomClassifier(
            image_session=_FakeImageSession(np.array([1.0], dtype=np.float32)),
            text_embeds=np.zeros((len(ROOM_TYPES) - 1, 4), dtype=np.float32),
        )


def test_load_default_classifier_raises_when_assets_missing(tmp_path) -> None:
    # Empty temp dir → both files absent → RoomClassifierUnavailable with
    # the --download hint, NO onnxruntime import, NO network.
    with pytest.raises(RoomClassifierUnavailable, match="--download"):
        load_default_classifier(clip_dir=tmp_path)


def test_load_default_classifier_mentions_missing_files(tmp_path) -> None:
    with pytest.raises(RoomClassifierUnavailable) as excinfo:
        load_default_classifier(clip_dir=tmp_path)
    msg = str(excinfo.value)
    assert "clip_image_encoder.onnx" in msg
    assert "room_text_embeds.npy" in msg


# ---- MEDIUM-1: projected-embedding output is selected BY NAME, not index 0


def test_embed_image_selects_projected_output_by_name_not_index_zero() -> None:
    """The classic silent-death trap: a raw ``last_hidden_state`` sits at
    output index 0 and the projected ``image_embeds`` at index 1. The
    classifier MUST pick ``image_embeds`` by name, never the index-0 raw
    tensor (which would flatten to a wrong-shape, wrong-space vector).
    """
    embed_dim = 4
    kitchen_idx = ROOM_TYPES.index("kitchen")

    # Index 0: a raw (1, 50, 768)-shaped hidden state — the wrong tensor.
    raw_hidden = np.ones((1, 50, 768), dtype=np.float32)
    # Index 1: the projected image embed, aligned with the kitchen row.
    projected = np.zeros((1, embed_dim), dtype=np.float32)
    projected[0, 0] = 1.0

    session = _MultiOutputSession(
        [("last_hidden_state", raw_hidden), ("image_embeds", projected)],
    )
    text_embeds = np.full((len(ROOM_TYPES), embed_dim), 0.01, dtype=np.float32)
    text_embeds[kitchen_idx, 0] = 1.0

    clf = RoomClassifier(image_session=session, text_embeds=text_embeds)
    scores = clf.classify(_tiny_png())

    # If the index-0 raw tensor had been used, the reshape→matmul would
    # have raised (caught nowhere here) or produced garbage; instead we get
    # the correct top label from the projected embed.
    top = max(scores, key=lambda k: scores[k])
    assert top == "kitchen"
    assert scores["kitchen"] > 0.9


def test_embed_image_resolves_image_embeds_even_when_not_first() -> None:
    # Sanity: the resolver returns index 1 when image_embeds is second.
    embed_dim = 3
    session = _MultiOutputSession(
        [
            ("last_hidden_state", np.zeros((1, 7, 5), dtype=np.float32)),
            ("image_embeds", np.array([[0.0, 1.0, 0.0]], dtype=np.float32)),
        ],
    )
    text_embeds = np.eye(len(ROOM_TYPES), embed_dim, dtype=np.float32)
    clf = RoomClassifier(image_session=session, text_embeds=text_embeds)
    embed = clf.embed_image(_tiny_png())
    # The projected (index-1) vector [0,1,0] L2-normalizes to itself.
    assert embed.shape == (embed_dim,)
    assert np.allclose(embed, np.array([0.0, 1.0, 0.0], dtype=np.float32))


def test_embed_image_raises_when_no_projected_output_present() -> None:
    """A mis-pinned raw-encoder export (only ``last_hidden_state``) must
    fail LOUDLY with RoomClassifierUnavailable, not silently score garbage.
    """
    session = _MultiOutputSession(
        [
            ("last_hidden_state", np.zeros((1, 50, 768), dtype=np.float32)),
            ("pooler_output", np.zeros((1, 768), dtype=np.float32)),
        ],
    )
    text_embeds = np.zeros((len(ROOM_TYPES), 4), dtype=np.float32)
    clf = RoomClassifier(image_session=session, text_embeds=text_embeds)
    with pytest.raises(RoomClassifierUnavailable, match="projected-embedding output"):
        clf.classify(_tiny_png())


# ---- MEDIUM-2: real Pillow decode path on bad bytes raises (not simulated)


def test_classify_real_bad_bytes_raises_decode_error() -> None:
    """A REAL RoomClassifier on garbage bytes must raise from the genuine
    Pillow decode path (UnidentifiedImageError / OSError), proving the
    decode→raise behavior that match_photo relies on is real, not stubbed.
    """
    clf = RoomClassifier(
        image_session=_FakeImageSession(np.array([1.0, 0.0, 0.0], dtype=np.float32)),
        text_embeds=np.eye(len(ROOM_TYPES), 3, dtype=np.float32),
    )
    with pytest.raises((OSError, ValueError)):
        clf.classify(b"not-a-png")
