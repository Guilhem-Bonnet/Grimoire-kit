"""Tests for grimoire.memory.backends.qdrant — QdrantBackend.

All external dependencies (qdrant_client, sentence_transformers) are fully mocked.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryEntry

# ── Mock infrastructure ───────────────────────────────────────────────────────

def _make_qdrant_mocks() -> tuple[MagicMock, MagicMock, dict[str, Any]]:
    """Build mock qdrant_client module + SentenceTransformer class."""
    # Qdrant client mock
    mock_client = MagicMock()
    mock_client.get_collections.return_value.collections = []
    mock_client.count.return_value.count = 0

    # qdrant_client module
    qdrant_mod = MagicMock()
    qdrant_mod.QdrantClient.return_value = mock_client

    # qdrant_client.models
    models_mod = MagicMock()
    qdrant_mod.models = models_mod

    # sentence_transformers module
    mock_encoder = MagicMock()
    import numpy as np

    mock_encoder.encode.return_value = np.zeros(384)

    st_mod = MagicMock()
    st_mod.SentenceTransformer.return_value = mock_encoder

    return mock_client, mock_encoder, {
        "qdrant_client": qdrant_mod,
        "qdrant_client.models": models_mod,
        "sentence_transformers": st_mod,
    }


@pytest.fixture()
def qdrant_env(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Patch imports so QdrantBackend can be instantiated without real deps."""
    mock_client, mock_encoder, modules = _make_qdrant_mocks()

    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Clear any cached import of the backend module
    monkeypatch.delitem(sys.modules, "grimoire.memory.backends.qdrant", raising=False)

    return mock_client, mock_encoder


def _make_backend(qdrant_env: tuple[MagicMock, MagicMock]) -> Any:
    """Import and instantiate QdrantBackend with mocked deps."""
    from grimoire.memory.backends.qdrant import QdrantBackend

    return QdrantBackend(qdrant_path="/tmp/test_qdrant")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestQdrantBackendConstruct:
    def test_creates_collection_when_missing(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.get_collections.return_value.collections = []
        _make_backend(qdrant_env)
        client.create_collection.assert_called_once()

    def test_skips_create_when_collection_exists(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        coll = MagicMock()
        coll.name = "grimoire"
        client.get_collections.return_value.collections = [coll]
        _make_backend(qdrant_env)
        client.create_collection.assert_not_called()


class TestQdrantBackendStore:
    def test_store_returns_memory_entry(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        backend = _make_backend(qdrant_env)
        entry = backend.store("test memory")
        assert isinstance(entry, MemoryEntry)
        assert entry.text == "test memory"
        assert entry.user_id == "global"
        assert entry.id

    def test_store_calls_upsert(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        backend = _make_backend(qdrant_env)
        backend.store("data")
        client.upsert.assert_called_once()

    def test_store_with_user_id(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        backend = _make_backend(qdrant_env)
        entry = backend.store("fact", user_id="alice")
        assert entry.user_id == "alice"

    def test_store_with_metadata(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        backend = _make_backend(qdrant_env)
        entry = backend.store("fact", metadata={"tag": "important"})
        assert entry.metadata == {"tag": "important"}


class TestQdrantBackendRecall:
    def test_recall_found(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        pt = MagicMock()
        pt.id = "abc-123"
        pt.payload = {"memory": "recalled", "user_id": "global", "created_at": "2025-01-01T00:00:00"}
        client.retrieve.return_value = [pt]

        backend = _make_backend(qdrant_env)
        result = backend.recall("abc-123")
        assert result is not None
        assert result.text == "recalled"
        assert result.id == "abc-123"

    def test_recall_not_found(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.retrieve.return_value = []

        backend = _make_backend(qdrant_env)
        assert backend.recall("nonexistent") is None


class TestQdrantBackendSearch:
    def test_search_returns_scored_entries(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        pt = MagicMock()
        pt.id = "s-1"
        pt.score = 0.95
        pt.payload = {"memory": "relevant", "user_id": "global", "created_at": "2025-01-01T00:00:00"}
        resp = MagicMock()
        resp.points = [pt]
        client.query_points.return_value = resp

        backend = _make_backend(qdrant_env)
        results = backend.search("query")
        assert len(results) == 1
        assert results[0].score == 0.95
        assert results[0].text == "relevant"

    def test_search_empty(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        resp = MagicMock()
        resp.points = []
        client.query_points.return_value = resp

        backend = _make_backend(qdrant_env)
        assert backend.search("nothing") == []


class TestQdrantBackendGetAll:
    def test_get_all_returns_entries(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        pt = MagicMock()
        pt.id = "g-1"
        pt.payload = {"memory": "item", "user_id": "global", "created_at": ""}
        client.scroll.return_value = ([pt], None)

        backend = _make_backend(qdrant_env)
        results = backend.get_all()
        assert len(results) == 1
        assert results[0].text == "item"

    def test_get_all_with_user_filter(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.scroll.return_value = ([], None)

        backend = _make_backend(qdrant_env)
        results = backend.get_all(user_id="bob")
        assert results == []
        # Verify a filter was passed
        call_kwargs = client.scroll.call_args
        assert call_kwargs is not None


class TestQdrantBackendCount:
    def test_count(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.count.return_value.count = 42
        backend = _make_backend(qdrant_env)
        assert backend.count() == 42


class TestQdrantBackendHealth:
    def test_health_check_healthy(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.count.return_value.count = 10
        backend = _make_backend(qdrant_env)
        status = backend.health_check()
        assert isinstance(status, BackendStatus)
        assert status.healthy is True
        assert status.entries == 10
        assert "qdrant" in status.backend

    def test_health_check_unhealthy(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        client, _ = qdrant_env
        client.count.side_effect = RuntimeError("connection lost")
        backend = _make_backend(qdrant_env)
        status = backend.health_check()
        assert status.healthy is False
        assert "error" in status.detail


class TestQdrantBackendConsolidate:
    def test_consolidate_returns_zero(self, qdrant_env: tuple[MagicMock, MagicMock]) -> None:
        backend = _make_backend(qdrant_env)
        assert backend.consolidate() == 0


class TestQdrantBackendImportError:
    def test_missing_qdrant_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify clear error when qdrant-client is not installed."""
        monkeypatch.delitem(sys.modules, "grimoire.memory.backends.qdrant", raising=False)

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "qdrant_client":
                raise ImportError("No module named 'qdrant_client'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.delitem(sys.modules, "grimoire.memory.backends.qdrant", raising=False)

        from grimoire.memory.backends.qdrant import QdrantBackend

        with pytest.raises(ImportError, match="qdrant-client"):
            QdrantBackend(qdrant_path="/tmp/x")
