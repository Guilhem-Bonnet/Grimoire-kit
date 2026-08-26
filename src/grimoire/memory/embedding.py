"""Dense text embedding, shared by the Qdrant and Weaviate backends.

Two engines are supported, in this order:

``fastembed``
    The default. ONNX runtime, no torch — the whole stack weighs ~200 MB.
    Loads from a local directory with no network at all, which is what makes
    :mod:`grimoire.memory.bundle` usable on a closed site.

``sentence-transformers``
    Fallback, used only when it is already installed and fastembed is not. It
    pulls torch and the CUDA wheels — measured at 4.8 GB against fastembed's
    203 MB for the same default model.

Both produce the same vectors: for ``sentence-transformers/all-MiniLM-L6-v2``,
the two engines agree to 2e-7 per component (cosine difference 5e-13), because
Qdrant's ONNX export is faithful rather than quantised. Switching engines on an
existing store therefore needs no re-indexing.

The dimension is never guessed from a lookup table — it is read from a probe
vector, so it is right by construction for any model.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Model used when the project declares none.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_PROBE = "grimoire embedding probe"

#: Hub switches honoured by both engines when offline mode is requested.
_OFFLINE_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


class EmbeddingEngineError(RuntimeError):
    """No usable embedding engine, or the configured model cannot load."""


@contextlib.contextmanager
def offline_env(enabled: bool = True) -> Iterator[None]:
    """Set the hub offline switches for the duration of the block."""
    if not enabled:
        yield
        return
    previous = {k: os.environ.get(k) for k in _OFFLINE_ENV}
    for key in _OFFLINE_ENV:
        os.environ[key] = "1"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass(slots=True)
class Embedder:
    """A loaded embedding model, with the engine and dimension it resolved to."""

    engine: str
    model_name: str
    dim: int
    _encode: Any

    def encode(self, text: str) -> list[float]:
        vector: list[float] = self._encode(text)
        return vector


def _build_fastembed(model: str, cache_dir: str, model_path: str) -> Any:
    """Return a fastembed encode callable, or None when fastembed is absent."""
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None

    kwargs: dict[str, Any] = {"model_name": model}
    if model_path:
        # Bypasses every network path: fastembed returns the directory as-is.
        kwargs["specific_model_path"] = model_path
    elif cache_dir:
        kwargs["cache_dir"] = cache_dir

    embedder = TextEmbedding(**kwargs)

    def encode(text: str) -> list[float]:
        vector = next(iter(embedder.embed([text])))
        return [float(x) for x in vector]

    return encode


def _build_sentence_transformers(model: str, cache_dir: str, model_path: str) -> Any:
    """Return a sentence-transformers encode callable, or None when absent."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None

    kwargs: dict[str, Any] = {}
    if cache_dir:
        kwargs["cache_folder"] = cache_dir
    encoder = SentenceTransformer(model_path or model, **kwargs)

    def encode(text: str) -> list[float]:
        return [float(x) for x in encoder.encode(text)]

    return encode


def build_embedder(
    model: str = "",
    *,
    cache_dir: str = "",
    model_path: str = "",
    offline: bool = False,
) -> Embedder:
    """Load *model* with the best available engine.

    Parameters
    ----------
    model:
        Model identifier. With fastembed this must be a name it knows, even when
        the weights come from *model_path* — the registry entry is what carries
        the pooling and normalisation rules.
    cache_dir:
        Where the engine may store downloaded weights.
    model_path:
        A local model directory, typically installed by ``grimoire memory
        bundle install``. When set, no network path is taken at all.
    offline:
        Set the hub offline switches while loading.
    """
    model = model or DEFAULT_MODEL
    if model_path and not Path(model_path).expanduser().is_dir():
        msg = f"Embedding model path does not exist: {model_path}"
        raise EmbeddingEngineError(msg)

    with offline_env(offline):
        for engine, factory in (
            ("fastembed", _build_fastembed),
            ("sentence-transformers", _build_sentence_transformers),
        ):
            try:
                encode = factory(model, cache_dir, model_path)
            except Exception as exc:
                msg = f"Embedding engine '{engine}' failed to load '{model_path or model}': {exc}"
                raise EmbeddingEngineError(msg) from exc
            if encode is None:
                continue
            # The probe both validates the model and settles the dimension.
            try:
                vector = encode(_PROBE)
            except Exception as exc:
                msg = f"Embedding engine '{engine}' loaded '{model_path or model}' but could not embed: {exc}"
                raise EmbeddingEngineError(msg) from exc
            return Embedder(engine=engine, model_name=model, dim=len(vector), _encode=encode)

    msg = (
        "No embedding engine installed.\n"
        "  → pip install grimoire-kit[qdrant]  (fastembed, ~200 MB)\n"
        "  → or use a backend that needs no model: memory.retrieval_mode: lexical"
    )
    raise EmbeddingEngineError(msg)
