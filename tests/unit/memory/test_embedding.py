"""Tests for grimoire.memory.embedding — engine selection and probing.

Both engines are faked. What is asserted is the contract the backends rely on:
which engine wins, that the dimension comes from a real probe rather than a
lookup table, and that a local model path never takes a network path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from grimoire.memory import embedding as mod
from grimoire.memory.embedding import (
    DEFAULT_MODEL,
    EmbeddingEngineError,
    build_embedder,
    offline_env,
)


def _fake_fastembed(dim: int = 384, recorder: dict[str, Any] | None = None) -> MagicMock:
    """A stand-in fastembed module whose TextEmbedding records its kwargs."""
    module = MagicMock()

    class _TextEmbedding:
        def __init__(self, **kwargs: Any) -> None:
            if recorder is not None:
                recorder.update(kwargs)

        def embed(self, texts: list[str]) -> Any:
            return iter([[0.5] * dim for _ in texts])

    module.TextEmbedding = _TextEmbedding
    return module


def _fake_sentence_transformers(dim: int = 768, recorder: dict[str, Any] | None = None) -> MagicMock:
    module = MagicMock()

    class _SentenceTransformer:
        def __init__(self, name_or_path: str, **kwargs: Any) -> None:
            if recorder is not None:
                recorder["name_or_path"] = name_or_path
                recorder.update(kwargs)

        def encode(self, text: str) -> Any:
            return [0.25] * dim

    module.SentenceTransformer = _SentenceTransformer
    return module


@pytest.fixture
def no_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make both engines unimportable."""
    for name in ("fastembed", "sentence_transformers"):
        monkeypatch.setitem(sys.modules, name, None)


# ── Engine selection ──────────────────────────────────────────────────────────


def test_fastembed_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed(dim=384))
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_sentence_transformers(dim=768))

    emb = build_embedder()

    assert emb.engine == "fastembed"
    assert emb.dim == 384
    assert emb.model_name == DEFAULT_MODEL


def test_sentence_transformers_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_sentence_transformers(dim=768))

    emb = build_embedder()

    assert emb.engine == "sentence-transformers"
    assert emb.dim == 768


def test_no_engine_gives_an_actionable_error(no_engines: None) -> None:
    with pytest.raises(EmbeddingEngineError, match="No embedding engine installed"):
        build_embedder()


def test_error_names_the_lexical_way_out(no_engines: None) -> None:
    with pytest.raises(EmbeddingEngineError, match="retrieval_mode: lexical"):
        build_embedder()


# ── Dimension comes from a probe, never a table ───────────────────────────────


@pytest.mark.parametrize("dim", [128, 384, 512, 768, 1024, 4096])
def test_dimension_is_read_from_the_probe(monkeypatch: pytest.MonkeyPatch, dim: int) -> None:
    """An unknown model with an unusual width must still report the truth."""
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed(dim=dim))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    assert build_embedder("acme/never-seen-before").dim == dim


def test_encode_returns_plain_floats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed(dim=4))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    vector = build_embedder().encode("bonjour")

    assert vector == [0.5, 0.5, 0.5, 0.5]
    assert all(isinstance(x, float) for x in vector)


# ── Local model path ──────────────────────────────────────────────────────────


def test_model_path_uses_specific_model_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The bundle path must bypass every download path in fastembed."""
    recorder: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed(recorder=recorder))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    build_embedder(DEFAULT_MODEL, model_path=str(model_dir))

    assert recorder["specific_model_path"] == str(model_dir)
    assert "cache_dir" not in recorder


def test_cache_dir_is_used_when_no_model_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed(recorder=recorder))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    build_embedder(DEFAULT_MODEL, cache_dir=str(tmp_path))

    assert recorder["cache_dir"] == str(tmp_path)
    assert "specific_model_path" not in recorder


def test_sentence_transformers_loads_the_path_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    recorder: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "fastembed", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_sentence_transformers(recorder=recorder))
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    build_embedder(DEFAULT_MODEL, model_path=str(model_dir))

    assert recorder["name_or_path"] == str(model_dir)


def test_missing_model_path_fails_before_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", _fake_fastembed())
    with pytest.raises(EmbeddingEngineError, match="does not exist"):
        build_embedder(DEFAULT_MODEL, model_path=str(tmp_path / "absent"))


# ── Failure reporting ─────────────────────────────────────────────────────────


def test_engine_load_failure_names_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    module = MagicMock()

    class _Boom:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("corrupt onnx")

    module.TextEmbedding = _Boom
    monkeypatch.setitem(sys.modules, "fastembed", module)

    with pytest.raises(EmbeddingEngineError, match="acme/broken"):
        build_embedder("acme/broken")


def test_embed_failure_is_distinguished_from_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = MagicMock()

    class _LoadsButCannotEmbed:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def embed(self, texts: list[str]) -> Any:
            raise RuntimeError("tokenizer missing")

    module.TextEmbedding = _LoadsButCannotEmbed
    monkeypatch.setitem(sys.modules, "fastembed", module)

    with pytest.raises(EmbeddingEngineError, match="could not embed"):
        build_embedder("acme/half-broken")


# ── Offline switches ──────────────────────────────────────────────────────────


def test_offline_env_sets_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")

    with offline_env():
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"

    assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["TRANSFORMERS_OFFLINE"] == "0"


def test_offline_env_disabled_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with offline_env(False):
        assert "HF_HUB_OFFLINE" not in os.environ


def test_build_embedder_applies_offline_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    module = MagicMock()

    class _Recording:
        def __init__(self, **kwargs: Any) -> None:
            seen["hf_offline_at_load"] = os.environ.get("HF_HUB_OFFLINE")

        def embed(self, texts: list[str]) -> Any:
            return iter([[0.1] * 8])

    module.TextEmbedding = _Recording
    monkeypatch.setitem(sys.modules, "fastembed", module)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    build_embedder(offline=True)

    assert seen["hf_offline_at_load"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ


def test_default_model_constant_is_the_documented_one() -> None:
    assert mod.DEFAULT_MODEL == "sentence-transformers/all-MiniLM-L6-v2"
