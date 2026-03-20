"""Tests for grimoire.memory.backends.ollama — OllamaBackend.

All external dependencies (qdrant_client, ollama HTTP) are fully mocked.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryEntry

_FAKE_VECTOR = [0.1] * 768


# ── Mock infrastructure ───────────────────────────────────────────────────────

def _make_qdrant_mock() -> MagicMock:
    """Build a mock qdrant client instance."""
    mock_client = MagicMock()
    mock_client.get_collections.return_value.collections = []
    mock_client.count.return_value.count = 0
    return mock_client


@pytest.fixture()
def ollama_env(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch imports + ollama_embed so OllamaBackend can be instantiated."""
    mock_client = _make_qdrant_mock()

    # Mock qdrant_client module
    qdrant_mod = MagicMock()
    qdrant_mod.QdrantClient.return_value = mock_client
    models_mod = MagicMock()
    qdrant_mod.models = models_mod

    for name, mod in {"qdrant_client": qdrant_mod, "qdrant_client.models": models_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Patch ollama_embed to avoid real HTTP calls
    # Note: do NOT delete grimoire.memory.backends.ollama from sys.modules before setattr.
    # qdrant_client is only imported inside __init__ (not at module level), so forcing a
    # re-import is unnecessary. Worse, delitem causes setattr to patch the stale module
    # object found via the parent package attribute, while _make_backend then does a fresh
    # import — the two end up referencing different module objects.
    monkeypatch.setattr(
        "grimoire.memory.backends.ollama.ollama_embed",
        lambda text, model, url, **kw: list(_FAKE_VECTOR),
    )

    return mock_client


def _make_backend(ollama_env: MagicMock) -> Any:
    """Import and instantiate OllamaBackend with mocked deps."""
    from grimoire.memory.backends.ollama import OllamaBackend

    return OllamaBackend(qdrant_path="/tmp/test_qdrant_ollama")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOllamaBackendConstruct:
    def test_creates_collection(self, ollama_env: MagicMock) -> None:
        ollama_env.get_collections.return_value.collections = []
        _make_backend(ollama_env)
        ollama_env.create_collection.assert_called_once()

    def test_skips_create_when_exists(self, ollama_env: MagicMock) -> None:
        coll = MagicMock()
        coll.name = "grimoire"
        ollama_env.get_collections.return_value.collections = [coll]
        _make_backend(ollama_env)
        ollama_env.create_collection.assert_not_called()


class TestOllamaBackendStore:
    def test_store_returns_entry(self, ollama_env: MagicMock) -> None:
        backend = _make_backend(ollama_env)
        entry = backend.store("remember this")
        assert isinstance(entry, MemoryEntry)
        assert entry.text == "remember this"
        assert entry.user_id == "global"

    def test_store_calls_upsert(self, ollama_env: MagicMock) -> None:
        backend = _make_backend(ollama_env)
        backend.store("data")
        ollama_env.upsert.assert_called_once()

    def test_store_with_user_id(self, ollama_env: MagicMock) -> None:
        backend = _make_backend(ollama_env)
        entry = backend.store("fact", user_id="carol")
        assert entry.user_id == "carol"


class TestOllamaBackendRecall:
    def test_recall_found(self, ollama_env: MagicMock) -> None:
        pt = MagicMock()
        pt.id = "o-123"
        pt.payload = {"memory": "recalled", "user_id": "global", "created_at": "2025-01-01T00:00:00"}
        ollama_env.retrieve.return_value = [pt]

        backend = _make_backend(ollama_env)
        result = backend.recall("o-123")
        assert result is not None
        assert result.text == "recalled"

    def test_recall_not_found(self, ollama_env: MagicMock) -> None:
        ollama_env.retrieve.return_value = []
        backend = _make_backend(ollama_env)
        assert backend.recall("nope") is None


class TestOllamaBackendSearch:
    def test_search_returns_entries(self, ollama_env: MagicMock) -> None:
        pt = MagicMock()
        pt.id = "s-1"
        pt.score = 0.88
        pt.payload = {"memory": "hit", "user_id": "global", "created_at": ""}
        resp = MagicMock()
        resp.points = [pt]
        ollama_env.query_points.return_value = resp

        backend = _make_backend(ollama_env)
        results = backend.search("test")
        assert len(results) == 1
        assert results[0].score == 0.88

    def test_search_empty(self, ollama_env: MagicMock) -> None:
        resp = MagicMock()
        resp.points = []
        ollama_env.query_points.return_value = resp

        backend = _make_backend(ollama_env)
        assert backend.search("nothing") == []


class TestOllamaBackendGetAll:
    def test_get_all(self, ollama_env: MagicMock) -> None:
        pt = MagicMock()
        pt.id = "a-1"
        pt.payload = {"memory": "entry", "user_id": "global", "created_at": ""}
        ollama_env.scroll.return_value = ([pt], None)

        backend = _make_backend(ollama_env)
        results = backend.get_all()
        assert len(results) == 1


class TestOllamaBackendCount:
    def test_count(self, ollama_env: MagicMock) -> None:
        ollama_env.count.return_value.count = 7
        backend = _make_backend(ollama_env)
        assert backend.count() == 7


class TestOllamaBackendHealth:
    def test_healthy(self, ollama_env: MagicMock) -> None:
        ollama_env.count.return_value.count = 3
        backend = _make_backend(ollama_env)
        status = backend.health_check()
        assert isinstance(status, BackendStatus)
        assert status.healthy is True
        assert status.backend == "ollama"

    def test_unhealthy(self, ollama_env: MagicMock) -> None:
        ollama_env.count.side_effect = RuntimeError("boom")
        backend = _make_backend(ollama_env)
        status = backend.health_check()
        assert status.healthy is False


class TestOllamaBackendConsolidate:
    def test_consolidate_returns_zero(self, ollama_env: MagicMock) -> None:
        backend = _make_backend(ollama_env)
        assert backend.consolidate() == 0


class TestOllamaBackendTags:
    def test_store_with_tags(self, ollama_env: MagicMock) -> None:
        backend = _make_backend(ollama_env)
        entry = backend.store("tagged", tags=("ollama", "test"))
        assert entry.tags == ("ollama", "test")


class TestOllamaBackendPagination:
    def test_get_all_with_offset_limit(self, ollama_env: MagicMock) -> None:
        pts = []
        for i in range(5):
            pt = MagicMock()
            pt.id = f"p-{i}"
            pt.payload = {"memory": f"item-{i}", "user_id": "global", "created_at": "", "tags": [], "updated_at": ""}
            pts.append(pt)
        ollama_env.scroll.return_value = (pts, None)

        backend = _make_backend(ollama_env)
        results = backend.get_all(offset=1, limit=2)
        assert len(results) == 2
        assert results[0].text == "item-1"


class TestOllamaBackendDelete:
    def test_delete_existing(self, ollama_env: MagicMock) -> None:
        pt = MagicMock()
        pt.id = "del-1"
        ollama_env.retrieve.return_value = [pt]

        backend = _make_backend(ollama_env)
        assert backend.delete("del-1") is True
        ollama_env.delete.assert_called_once()

    def test_delete_missing(self, ollama_env: MagicMock) -> None:
        ollama_env.retrieve.return_value = []
        backend = _make_backend(ollama_env)
        assert backend.delete("nope") is False


class TestOllamaBackendUpdate:
    def test_update_text(self, ollama_env: MagicMock) -> None:
        pt = MagicMock()
        pt.id = "u-1"
        pt.payload = {"memory": "old", "user_id": "global", "created_at": "2025", "tags": [], "updated_at": ""}
        pt.vector = _FAKE_VECTOR
        ollama_env.retrieve.return_value = [pt]

        backend = _make_backend(ollama_env)
        result = backend.update("u-1", text="new")
        assert result is not None
        assert result.text == "new"

    def test_update_missing(self, ollama_env: MagicMock) -> None:
        ollama_env.retrieve.return_value = []
        backend = _make_backend(ollama_env)
        assert backend.update("nope", text="x") is None


class TestOllamaEmbed:
    """Test the ollama_embed helper function."""

    def test_ollama_embed_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(sys.modules, "grimoire.memory.backends.ollama", raising=False)
        from grimoire.memory.backends.ollama import ollama_embed

        response_data = json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: mock_resp)

        result = ollama_embed("hello", "nomic-embed-text", "http://localhost:11434")
        assert result == [0.1, 0.2, 0.3]

    def test_ollama_embed_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(sys.modules, "grimoire.memory.backends.ollama", raising=False)
        import urllib.error
        import urllib.request

        from grimoire.memory.backends.ollama import ollama_embed

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            MagicMock(side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None)),
        )

        with pytest.raises(RuntimeError, match="Ollama API error 404"):
            ollama_embed("test", "bad-model", "http://localhost:11434")

    def test_ollama_embed_url_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(sys.modules, "grimoire.memory.backends.ollama", raising=False)
        import urllib.error
        import urllib.request

        from grimoire.memory.backends.ollama import ollama_embed

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            MagicMock(side_effect=urllib.error.URLError("Connection refused")),
        )

        with pytest.raises(RuntimeError, match="Ollama unreachable"):
            ollama_embed("test", "model", "http://localhost:11434")
