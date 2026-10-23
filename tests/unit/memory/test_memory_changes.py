"""Provenance et journal des écritures mémoire (P14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import standard_state
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.changes import CHANGES_RELPATH
from grimoire.memory.manager import MemoryManager
from grimoire.memory.sidecar import MemorySidecar

_ENVS = ("GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_TASK_ID")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def manager(tmp_path: Path) -> MemoryManager:
    backend = LocalMemoryBackend(tmp_path / "mem.json")
    sidecar = MemorySidecar(tmp_path / "sidecar.sqlite3")
    return MemoryManager(backend, sidecar=sidecar, project_root=tmp_path)


def _lines(root: Path) -> list[dict[str, Any]]:
    path = root / CHANGES_RELPATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestJournalLines:
    def test_store_writes_one_line(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("décision A", reason="premier jet")
        (line,) = _lines(tmp_path)
        assert line["op"] == "store"
        assert line["ids_before"] == []
        assert line["ids_after"] == [entry.id]
        assert len(line["hash"]) == 64
        assert line["reason"] == "premier jet"

    def test_update_logs_before_and_after_ids(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("v1")
        manager.update(entry.id, text="v2", reason="correction")
        line = _lines(tmp_path)[-1]
        assert (line["op"], line["ids_before"], line["ids_after"]) == ("update", [entry.id], [entry.id])
        assert line["reason"] == "correction"

    def test_update_hash_follows_text(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("v1")
        manager.update(entry.id, text="v2")
        manager.update(entry.id, text="v3")
        first, second = _lines(tmp_path)[-2:]
        assert first["hash"] != second["hash"]

    def test_supersede_is_one_line_not_two(self, manager: MemoryManager, tmp_path: Path) -> None:
        old = manager.store("ancien")
        new = manager.store("nouveau")
        manager.supersede(old.id, new.id, reason="remplacé")
        lines = _lines(tmp_path)
        assert [entry["op"] for entry in lines] == ["store", "store", "supersede"]
        assert lines[-1]["ids_before"] == [old.id]
        assert lines[-1]["ids_after"] == [new.id]

    def test_delete_logs_removed_id(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("à effacer")
        assert manager.delete(entry.id, reason="obsolète") is True
        line = _lines(tmp_path)[-1]
        assert (line["op"], line["ids_before"], line["ids_after"]) == ("delete", [entry.id], [])

    def test_delete_of_unknown_id_logs_nothing(self, manager: MemoryManager, tmp_path: Path) -> None:
        assert manager.delete("absent") is False
        assert not (tmp_path / CHANGES_RELPATH).exists()

    def test_invalidate_fact_logs_triple(self, manager: MemoryManager, tmp_path: Path) -> None:
        manager.add_fact("kit", "uses", "ruff")
        assert manager.invalidate_fact("kit", "uses", "ruff", reason="remplacé par autre chose") == 1
        line = _lines(tmp_path)[-1]
        assert line["op"] == "invalidate_fact"
        assert line["ids_before"] == ["kit|uses|ruff"]
        assert line["reason"] == "remplacé par autre chose"

    def test_reason_omitted_when_not_given(self, manager: MemoryManager, tmp_path: Path) -> None:
        manager.store("sans raison")
        assert "reason" not in _lines(tmp_path)[0]

    def test_no_project_root_writes_no_journal(self, tmp_path: Path) -> None:
        mgr = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.store("x")
        assert not (tmp_path / CHANGES_RELPATH).exists()


class TestProvenanceSources:
    def test_explicit_arguments_land_in_metadata_and_journal(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("x", actor="alice", session_id="s-1", task_ref="GAO-1")
        stored = manager.recall(entry.id)
        assert stored is not None
        assert stored.metadata["actor"] == "alice"
        assert stored.metadata["session_id"] == "s-1"
        assert stored.metadata["task_ref"] == "GAO-1"
        line = _lines(tmp_path)[0]
        assert (line["actor"], line["session_id"], line["task_ref"]) == ("alice", "s-1", "GAO-1")

    def test_explicit_wins_over_session_and_claim(
        self, manager: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GRIMOIRE_ACTOR", "env-actor")
        monkeypatch.setenv("GRIMOIRE_SESSION_ID", "env-session")
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "GAO-ENV-001")
        manager.store("x", actor="alice", session_id="s-1", task_ref="GAO-1")
        line = _lines(tmp_path)[0]
        assert (line["actor"], line["session_id"], line["task_ref"]) == ("alice", "s-1", "GAO-1")

    def test_fallback_to_active_session_and_claim(
        self, manager: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GRIMOIRE_ACTOR", "claude-p14")
        monkeypatch.setenv("GRIMOIRE_SESSION_ID", "sess-9")
        monkeypatch.setattr(
            standard_state,
            "resolve_active_task",
            lambda root, **kw: standard_state.ActiveTask("GAO-p14-provena-002", "session_claim"),
        )
        entry = manager.store("x")
        stored = manager.recall(entry.id)
        assert stored is not None
        assert stored.metadata["actor"] == "claude-p14"
        assert stored.metadata["session_id"] == "sess-9"
        assert stored.metadata["task_ref"] == "GAO-p14-provena-002"
        line = _lines(tmp_path)[0]
        assert (line["actor"], line["session_id"], line["task_ref"]) == (
            "claude-p14", "sess-9", "GAO-p14-provena-002",
        )

    def test_absent_everywhere_fields_are_omitted(self, manager: MemoryManager, tmp_path: Path) -> None:
        entry = manager.store("x")
        stored = manager.recall(entry.id)
        assert stored is not None
        for key in ("actor", "session_id", "task_ref"):
            assert key not in stored.metadata
        line = _lines(tmp_path)[0]
        for key in ("actor", "session_id", "task_ref"):
            assert key not in line

    def test_update_keeps_existing_metadata_and_stamps_provenance(
        self, manager: MemoryManager
    ) -> None:
        entry = manager.store("v1", metadata={"room": "decisions"})
        updated = manager.update(entry.id, text="v2", actor="bob")
        assert updated is not None
        assert updated.metadata["room"] == "decisions"
        assert updated.metadata["actor"] == "bob"


class TestDeadFieldsRemoved:
    def test_memory_entry_has_no_dead_fields(self) -> None:
        entry = MemoryEntry(id="1", text="t")
        for name in ("source", "provenance", "freshness", "task_ref"):
            assert not hasattr(entry, name)
            assert name not in entry.to_dict()
