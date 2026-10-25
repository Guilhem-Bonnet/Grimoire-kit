"""P14 — provenance et journal des écritures mémoire.

Le journal ``_grimoire-output/memory-changes.jsonl`` est écrit par
``MemoryManager`` (jamais par une route), une ligne par mutation : store,
update, supersede, delete, invalidate_fact. ``actor``, ``session_id`` et
``task_ref`` viennent des arguments explicites, sinon de la session et du
claim actifs ; absents, ils sont omis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.journal import JOURNAL_RELPATH, fingerprint
from grimoire.memory.manager import MemoryManager
from grimoire.memory.sidecar import MemorySidecar
from grimoire.missions.service import TaskService

SESSION = "11111111-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mgr(tmp_path: Path) -> MemoryManager:
    return MemoryManager(
        LocalMemoryBackend(tmp_path / "mem.json"),
        sidecar=MemorySidecar(tmp_path / "sidecar.sqlite3"),
        project_root=tmp_path,
    )


def _rows(root: Path) -> list[dict[str, Any]]:
    path = root / JOURNAL_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestJournalLines:
    def test_store_writes_a_line(self, mgr: MemoryManager, tmp_path: Path) -> None:
        entry = mgr.store("decision alpha", actor="claude", reason="premiere decision")
        (row,) = _rows(tmp_path)
        assert row["op"] == "store"
        assert row["after_id"] == entry.id
        assert row["actor"] == "claude"
        assert row["fingerprint"] == fingerprint("decision alpha")
        assert row["reason"] == "premiere decision"
        assert "decision alpha" not in json.dumps(row)

    def test_update_writes_before_and_after_ids(self, mgr: MemoryManager, tmp_path: Path) -> None:
        entry = mgr.store("v1")
        mgr.update(entry.id, text="v2", reason="correction")
        row = _rows(tmp_path)[-1]
        assert row["op"] == "update"
        assert row["before_id"] == row["after_id"] == entry.id
        assert row["fingerprint"] == fingerprint("v2")
        assert row["reason"] == "correction"

    def test_supersede_links_old_to_new_with_a_single_line(self, mgr: MemoryManager, tmp_path: Path) -> None:
        old, new = mgr.store("ancien"), mgr.store("nouveau")
        mgr.supersede(old.id, new.id, reason="remplace")
        rows = [r for r in _rows(tmp_path) if r["op"] != "store"]
        assert [r["op"] for r in rows] == ["supersede"]
        assert rows[0]["before_id"] == old.id
        assert rows[0]["after_id"] == new.id
        assert rows[0]["reason"] == "remplace"

    def test_delete_fingerprints_the_deleted_content(self, mgr: MemoryManager, tmp_path: Path) -> None:
        entry = mgr.store("a supprimer")
        assert mgr.delete(entry.id, reason="obsolete") is True
        row = _rows(tmp_path)[-1]
        assert row["op"] == "delete"
        assert row["before_id"] == entry.id
        assert "after_id" not in row
        assert row["fingerprint"] == fingerprint("a supprimer")

    def test_failed_delete_writes_nothing(self, mgr: MemoryManager, tmp_path: Path) -> None:
        assert mgr.delete("inconnu") is False
        assert _rows(tmp_path) == []

    def test_invalidate_fact_writes_a_line(self, mgr: MemoryManager, tmp_path: Path) -> None:
        mgr.add_fact("api", "uses", "v1")
        assert mgr.invalidate_fact("api", "uses", "v1", reason="migre vers v2") == 1
        row = _rows(tmp_path)[-1]
        assert row["op"] == "invalidate_fact"
        assert row["fact"] == {"subject": "api", "predicate": "uses", "object": "v1"}
        assert row["reason"] == "migre vers v2"
        assert row["fingerprint"]

    def test_manager_without_project_root_writes_no_journal(self, tmp_path: Path) -> None:
        manager = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        manager.store("x", actor="claude")
        assert not (tmp_path / JOURNAL_RELPATH).exists()


class TestProvenanceSources:
    def test_explicit_arguments_land_in_metadata_and_journal(self, mgr: MemoryManager, tmp_path: Path) -> None:
        entry = mgr.store("fait", actor="alice", session_id="s-explicit", task_ref="GAO-x-001")
        assert entry.metadata["actor"] == "alice"
        assert entry.metadata["session_id"] == "s-explicit"
        assert entry.metadata["task_ref"] == "GAO-x-001"
        row = _rows(tmp_path)[0]
        assert (row["actor"], row["session_id"], row["task_ref"]) == ("alice", "s-explicit", "GAO-x-001")

    def test_active_session_and_actor_come_from_the_environment(
        self, mgr: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
        monkeypatch.setenv("GRIMOIRE_ACTOR", "bob")
        entry = mgr.store("fait de session")
        assert entry.metadata["session_id"] == SESSION
        assert entry.metadata["actor"] == "bob"
        assert _rows(tmp_path)[0]["session_id"] == SESSION

    def test_explicit_argument_beats_the_session(
        self, mgr: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
        entry = mgr.store("fait", session_id="autre")
        assert entry.metadata["session_id"] == "autre"

    def test_active_claim_provides_the_task(self, tmp_path: Path) -> None:
        setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
        registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
        registry.write_text(
            registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
        )
        service = TaskService(tmp_path)
        added = service.add("tache", ("ok",), owner="guilhem", actor="claude", ready=True)
        service.context(added.task.id)
        service.claim(added.task.id, "claude", host="local")
        manager = MemoryManager(LocalMemoryBackend(tmp_path / "mem.json"), project_root=tmp_path)
        entry = manager.store("fait sous claim")
        assert entry.metadata["task_ref"] == added.task.id
        assert _rows(tmp_path)[0]["task_ref"] == added.task.id

    def test_absent_fields_are_omitted_never_blank(self, mgr: MemoryManager, tmp_path: Path) -> None:
        entry = mgr.store("anonyme")
        for key in ("actor", "session_id", "task_ref"):
            assert key not in entry.metadata
            assert key not in _rows(tmp_path)[0]
        assert _rows(tmp_path)[0].get("reason") is None

    def test_update_stamps_provenance_and_keeps_existing_metadata(self, mgr: MemoryManager) -> None:
        entry = mgr.store("v1", metadata={"wing": "ops"}, actor="alice")
        updated = mgr.update(entry.id, text="v2", actor="bob")
        assert updated is not None
        assert updated.metadata["actor"] == "bob"
        assert updated.metadata["wing"] == "ops"


class TestEntryFieldsStayAlive:
    def test_task_ref_is_derived_from_metadata_when_served(self, mgr: MemoryManager) -> None:
        mgr.store("souvenir sur la tache", task_ref="GAO-y-002")
        served = mgr.search("souvenir")
        assert served
        assert served[0].task_ref == "GAO-y-002"
        assert served[0].freshness
        assert served[0].provenance
