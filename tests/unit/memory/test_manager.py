"""Tests for bmad.memory.manager — MemoryManager unified API."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from bmad.core.config import BmadConfig, MemoryConfig
from bmad.core.exceptions import BmadMemoryError
from bmad.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from bmad.memory.manager import MemoryManager, _resolve_auto

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_backend() -> MagicMock:
    """A mock that satisfies MemoryBackend interface."""
    b = MagicMock(spec=MemoryBackend)
    b.store.return_value = MemoryEntry(id="m-1", text="stored", user_id="global")
    b.recall.return_value = MemoryEntry(id="m-1", text="stored", user_id="global")
    b.search.return_value = [MemoryEntry(id="m-1", text="stored", user_id="global", score=0.9)]
    b.get_all.return_value = [MemoryEntry(id="m-1", text="stored", user_id="global")]
    b.count.return_value = 1
    b.health_check.return_value = BackendStatus(backend="mock", healthy=True, entries=1)
    b.consolidate.return_value = 0
    return b


def _make_config(backend: str = "local", **overrides: str) -> BmadConfig:
    """Build a minimal BmadConfig with given memory settings."""
    mem_data: dict[str, Any] = {"backend": backend, **overrides}
    return BmadConfig.from_dict({
        "project": {"name": "test-project"},
        "memory": mem_data,
    })


# ── from_backend / delegation ────────────────────────────────────────────────

class TestFromBackend:
    def test_wraps_backend(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        assert mgr.backend is mock_backend

    def test_store_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        result = mgr.store("hello")
        mock_backend.store.assert_called_once_with("hello", user_id="", metadata=None)
        assert result.text == "stored"

    def test_recall_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        mgr.recall("m-1")
        mock_backend.recall.assert_called_once_with("m-1")

    def test_search_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        results = mgr.search("query", user_id="alice", limit=3)
        mock_backend.search.assert_called_once_with("query", user_id="alice", limit=3)
        assert len(results) == 1

    def test_get_all_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        mgr.get_all(user_id="bob")
        mock_backend.get_all.assert_called_once_with(user_id="bob")

    def test_count_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        assert mgr.count() == 1

    def test_health_check_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        status = mgr.health_check()
        assert status.healthy is True

    def test_consolidate_delegates(self, mock_backend: MagicMock) -> None:
        mgr = MemoryManager.from_backend(mock_backend)
        assert mgr.consolidate() == 0


# ── auto-resolution ──────────────────────────────────────────────────────────

class TestResolveAuto:
    def test_auto_defaults_to_local(self) -> None:
        cfg = _make_config("auto")
        assert _resolve_auto(cfg) == "local"

    def test_auto_with_ollama_url(self) -> None:
        cfg = _make_config("auto", ollama_url="http://gpu:11434")
        assert _resolve_auto(cfg) == "ollama"

    def test_auto_with_qdrant_url(self) -> None:
        cfg = _make_config("auto", qdrant_url="http://qdrant:6333")
        assert _resolve_auto(cfg) == "qdrant-server"

    def test_ollama_takes_priority_over_qdrant(self) -> None:
        cfg = _make_config("auto", ollama_url="http://gpu:11434", qdrant_url="http://q:6333")
        assert _resolve_auto(cfg) == "ollama"


# ── from_config with local backend ───────────────────────────────────────────

class TestFromConfigLocal:
    def test_creates_local_backend(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config("local")
        mgr = MemoryManager.from_config(cfg)
        assert mgr.backend is not None
        # Verify it's a LocalMemoryBackend
        from bmad.memory.backends.local import LocalMemoryBackend

        assert isinstance(mgr.backend, LocalMemoryBackend)

    def test_local_store_and_search(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config("local")
        mgr = MemoryManager.from_config(cfg)
        mgr.store("python is great")
        results = mgr.search("python")
        assert len(results) == 1
        assert "python" in results[0].text.lower()

    def test_local_auto_resolves(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config("auto")
        mgr = MemoryManager.from_config(cfg)
        from bmad.memory.backends.local import LocalMemoryBackend

        assert isinstance(mgr.backend, LocalMemoryBackend)


# ── error handling ────────────────────────────────────────────────────────────

class TestFromConfigErrors:
    def test_unknown_backend_after_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Force an unknown backend through by patching _VALID_BACKENDS."""

        # Manually create a config with a sneaky backend (bypass validation)
        cfg = _make_config("local")
        # Monkey-patch the config's memory backend
        mem = MemoryConfig(backend="unknown-xyz")
        object.__setattr__(cfg, "memory", mem)

        with pytest.raises(BmadMemoryError, match="Unknown memory backend"):
            MemoryManager.from_config(cfg)

    def test_import_error_wrapped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Simulate missing qdrant-client → BmadMemoryError."""
        monkeypatch.chdir(tmp_path)

        from bmad.memory import manager as mgr_mod

        def _fake_create(config: BmadConfig) -> MemoryBackend:
            raise ImportError("No module named 'qdrant_client'")

        monkeypatch.setattr(mgr_mod, "_create_backend", _fake_create)
        cfg = _make_config("qdrant-local")

        with pytest.raises(BmadMemoryError, match="Missing dependency"):
            MemoryManager.from_config(cfg)
