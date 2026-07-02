"""Tests for the typed memory protocol — remember/recall_typed (ADR-003 parity)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MEMORY_TYPES, MemoryManager


@pytest.fixture()
def manager(tmp_path: Path) -> MemoryManager:
    backend = LocalMemoryBackend(tmp_path / "memories.json")
    return MemoryManager(backend, project_name="demo")


class TestMemoryTypes:
    def test_parity_with_legacy_bridge(self) -> None:
        # Les 5 collections du protocole agent (mem0-bridge MEMORY_TYPES).
        assert MEMORY_TYPES == (
            "shared-context", "decisions", "agent-learnings", "failures", "stories",
        )


class TestTypedEntryId:
    def test_deterministic(self, manager: MemoryManager) -> None:
        a = manager.typed_entry_id("dev", "le module X nécessite Y")
        b = manager.typed_entry_id("dev", "le module X nécessite Y")
        assert a == b
        uuid.UUID(a)  # forme valide

    def test_legacy_seed_format(self, manager: MemoryManager) -> None:
        # Même convention que mem0-bridge : uuid5(DNS, "grimoire-{proj}:{agent}:{text[:150]}")
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "grimoire-demo:dev:fact"))
        assert manager.typed_entry_id("dev", "fact") == expected

    def test_varies_by_agent(self, manager: MemoryManager) -> None:
        assert manager.typed_entry_id("dev", "fact") != manager.typed_entry_id("qa", "fact")


class TestRemember:
    def test_returns_typed_entry(self, manager: MemoryManager) -> None:
        entry = manager.remember("decisions", "dev", "PostgreSQL retenu pour la persistence")
        assert entry.metadata["type"] == "decisions"
        assert entry.metadata["agent"] == "dev"
        assert entry.user_id == "dev"

    def test_idempotent_same_text(self, manager: MemoryManager) -> None:
        first = manager.remember("agent-learnings", "dev", "toujours vérifier les migrations")
        second = manager.remember("agent-learnings", "dev", "toujours vérifier les migrations")
        assert first.id == second.id
        assert manager.count() == 1

    def test_different_agents_do_not_collide(self, manager: MemoryManager) -> None:
        manager.remember("failures", "dev", "timeout sur l'API")
        manager.remember("failures", "qa", "timeout sur l'API")
        assert manager.count() == 2

    def test_invalid_type_raises(self, manager: MemoryManager) -> None:
        with pytest.raises(GrimoireMemoryError, match="Invalid memory type"):
            manager.remember("gossip", "dev", "texte")

    def test_tags_are_stored(self, manager: MemoryManager) -> None:
        entry = manager.remember("decisions", "dev", "choix backend", tags=("infra", "db"))
        assert entry.tags == ("infra", "db")

    def test_fallback_without_upsert(self, tmp_path: Path) -> None:
        # Backend sans upsert : la relance du même remember ne duplique pas.
        class NoUpsert(LocalMemoryBackend):
            def upsert(self, entry_id: str, text: str, **kwargs: object) -> MemoryEntry:
                raise NotImplementedError

        manager = MemoryManager(NoUpsert(tmp_path / "m.json"), project_name="demo")
        first = manager.remember("decisions", "dev", "fallback dedup")
        second = manager.remember("decisions", "dev", "fallback dedup")
        assert manager.count() == 1
        assert first.id == second.id


class TestRecallTyped:
    def test_filters_by_type(self, manager: MemoryManager) -> None:
        manager.remember("decisions", "dev", "décision base de données postgres")
        manager.remember("failures", "dev", "échec migration base de données")
        results = manager.recall_typed("base de données", type_="failures")
        assert len(results) == 1
        assert results[0].metadata["type"] == "failures"

    def test_filters_by_agent(self, manager: MemoryManager) -> None:
        manager.remember("decisions", "dev", "convention de nommage python")
        manager.remember("decisions", "qa", "convention de tests python")
        results = manager.recall_typed("python convention", agent="qa")
        assert len(results) == 1
        assert results[0].metadata["agent"] == "qa"

    def test_invalid_type_raises(self, manager: MemoryManager) -> None:
        with pytest.raises(GrimoireMemoryError, match="Invalid memory type"):
            manager.recall_typed("x", type_="gossip")

    def test_no_filter_returns_matches(self, manager: MemoryManager) -> None:
        manager.remember("stories", "sm", "epic paiement découpée en 3 stories")
        assert manager.recall_typed("stories paiement")
