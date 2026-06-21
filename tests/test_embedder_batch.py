"""Regression guard: embed_texts must cap FastEmbed's batch size.

FastEmbed defaults to batch_size=256. The indexer hands every chunk's text to
embed_texts in one call, so an uncapped batch builds a single ~3 GB attention
buffer (256 x heads x 512^2 x 4 bytes) and OOMs ordinary machines — `karst
index` crashed with "Failed to allocate memory ... 3221225472".

This test fakes the model (no weights, no RAM) and asserts embed_texts passes a
bounded batch_size, so the OOM regression is caught on every `pytest` run.
"""
from __future__ import annotations

import pytest

import karst.embedder as emb_mod


class _RecordingModel:
    last_batch_size: int | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def embed(self, texts, batch_size: int = 256, **kwargs):
        _RecordingModel.last_batch_size = batch_size
        return ([0.0, 0.1, 0.2, 0.3] for _ in texts)


def test_embed_texts_caps_fastembed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Embedder.__init__ does `from fastembed import TextEmbedding`, so patch the
    # source attribute it will look up.
    monkeypatch.setattr("fastembed.TextEmbedding", _RecordingModel)

    embedder = emb_mod.Embedder(model_name="fake-model")
    vectors = embedder.embed_texts(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert vectors[0] == [0.0, 0.1, 0.2, 0.3]
    assert _RecordingModel.last_batch_size is not None, "model.embed was never called"
    assert _RecordingModel.last_batch_size <= emb_mod.DEFAULT_BATCH, (
        "embed_texts must pass a bounded batch_size to FastEmbed; its default of "
        "256 builds a ~3 GB attention buffer and OOMs ordinary machines."
    )
