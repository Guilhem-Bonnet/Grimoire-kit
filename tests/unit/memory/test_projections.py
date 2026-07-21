"""Tests for Memory OS graph projection helpers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

from grimoire.codegraph.schemas import CodeEdge, EdgeKind
from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.memory.projections import (
    _unique_code_edges,
    build_code_vector_entries,
    build_task_code_reference_projection,
    build_task_vector_entries,
    graph_projection_verify,
    sync_code_graph_projection,
    sync_code_vector_projection,
    sync_task_memory_projection,
    sync_task_vector_projection,
    vector_projection_verify,
)
from grimoire.missions.ledger import MissionLedger


def test_unique_code_edges_matches_neo4j_relation_identity() -> None:
    edge = CodeEdge("pkg.mod:run", "pkg.mod:helper", EdgeKind.CALLS)

    assert _unique_code_edges([edge, edge, CodeEdge("pkg.mod", "pkg.mod:run", EdgeKind.DEFINES)]) == [
        edge,
        CodeEdge("pkg.mod", "pkg.mod:run", EdgeKind.DEFINES),
    ]


def test_sync_code_graph_projection(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def run():\n    return 'ok'\n",
        encoding="utf-8",
    )
    graph = MagicMock()

    stats = sync_code_graph_projection(graph, project_root=tmp_path, paths=[Path("src")])

    assert stats["files"] == 1
    assert stats["code_nodes"] >= 2
    graph.upsert_code_nodes.assert_called_once()
    graph.upsert_code_edges.assert_called_once()


def test_build_and_sync_code_vector_projection(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        '"""App module."""\n\ndef run():\n    """Run app."""\n    return "ok"\n',
        encoding="utf-8",
    )
    manager = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memory.json"))

    expected = build_code_vector_entries(tmp_path, [Path("src")])
    stats = sync_code_vector_projection(manager, project_root=tmp_path, paths=[Path("src")])
    verify = vector_projection_verify(manager, expected)

    assert stats == {
        "vector_entries": 3,
        "upserted": 3,
        "skipped": 0,
        "code_files": 1,
        "code_symbols": 1,
        "code_methods": 0,
        "code_tests": 0,
        "code_contracts": 1,
    }
    assert {entry["id"] for entry in expected} == {
        "code:src/app.py",
        "code-symbol:src.app:run",
        "code-contract:src.app:run",
    }
    file_entry = next(entry for entry in expected if entry["metadata"]["projection_kind"] == "code_chunk")
    assert "function run" in file_entry["text"]
    assert verify["ok"] is True


def test_build_code_vector_entries_supports_method_test_and_contract_chunks(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        'class Service:\n'
        '    """Service contract."""\n'
        "    def run(self):\n"
        '        """Run contract."""\n'
        "        return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_service():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    entries = build_code_vector_entries(tmp_path, [Path("src"), Path("tests")])

    kinds = Counter(str(entry["metadata"]["projection_kind"]) for entry in entries)
    assert kinds["code_chunk"] == 2
    assert kinds["code_symbol"] == 1
    assert kinds["code_method"] == 1
    assert kinds["code_test"] == 1
    assert kinds["code_contract"] == 2
    method = next(entry for entry in entries if entry["metadata"]["projection_kind"] == "code_method")
    assert method["metadata"]["code_node_id"] == "src.service:Service.run"
    assert "Source excerpt:" in method["text"]
    test = next(entry for entry in entries if entry["metadata"]["projection_kind"] == "code_test")
    assert test["id"] == "code-test:tests/test_service.py"


def test_build_code_vector_entries_can_limit_granularity(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    entries = build_code_vector_entries(tmp_path, [Path("src")], granularity=("file",))

    assert [entry["id"] for entry in entries] == ["code:src/app.py"]


def test_sync_task_memory_projection(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("Target", origin="user")
    task = ledger.create_task(mission.id, "Sync tasks", acceptance=("projection written",))
    ledger.open_incident(mission.id, task.id, "verification", "Need proof")
    evidence = EvidenceService(tmp_path / "evidence")
    item = EvidenceItem.from_text("ev-1", EvidenceKind.TEST, "projection written", summary="projection written")
    pack = evidence.create_pack(task.id, EvidenceProfile.LIGHT, [item], acceptance=task.acceptance)
    evidence.verify(pack, acceptance=task.acceptance)
    graph = MagicMock()

    stats = sync_task_memory_projection(graph, ledger=ledger, evidence=evidence)

    assert stats == {
        "missions": 1,
        "tasks": 1,
        "ledger_events": 2,
        "incidents": 1,
        "evidence_packs": 1,
        "verdicts": 1,
        "decisions": 1,
        "task_code_links": 0,
        "evidence_code_links": 0,
    }
    graph.upsert_mission.assert_called_once_with(mission)
    graph.upsert_task.assert_called_once_with(task)
    assert graph.upsert_ledger_event.call_count == 2
    assert graph.upsert_incident.call_count == 1
    assert graph.upsert_evidence_pack.call_count == 1
    assert graph.upsert_verdict.call_count == 1


def test_sync_task_memory_projection_links_task_and_evidence_to_code(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("Target", origin="user")
    task = ledger.create_task(
        mission.id,
        "Sync src/app.py",
        acceptance=("src/app.py is linked",),
        description="This task updates src/app.py.",
    )
    evidence = EvidenceService(tmp_path / "evidence")
    item = EvidenceItem.from_text(
        "ev-1",
        EvidenceKind.TEST,
        "projection written",
        uri="src/app.py",
        summary="Evidence covers src/app.py",
    )
    evidence.create_pack(task.id, EvidenceProfile.LIGHT, [item], acceptance=task.acceptance)
    graph = MagicMock()
    graph.replace_task_code_references.return_value = 1
    graph.replace_evidence_code_references.return_value = 1

    refs = build_task_code_reference_projection(
        tmp_path,
        [Path("src")],
        ledger=ledger,
        evidence=evidence,
    )
    stats = sync_task_memory_projection(
        graph,
        ledger=ledger,
        evidence=evidence,
        project_root=tmp_path,
        code_paths=[Path("src")],
    )

    assert refs["tasks"][0]["code_node_id"].startswith("src.app")
    assert refs["evidence"][0]["code_node_id"].startswith("src.app")
    assert stats["task_code_links"] == 1
    assert stats["evidence_code_links"] == 1
    graph.replace_task_code_references.assert_called_once()
    graph.replace_evidence_code_references.assert_called_once()


def test_build_and_sync_task_vector_projection(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("Target", origin="user")
    task = ledger.create_task(mission.id, "Sync tasks", acceptance=("projection written",))
    evidence = EvidenceService(tmp_path / "evidence")
    item = EvidenceItem.from_text("ev-1", EvidenceKind.TEST, "projection written", summary="projection written")
    pack = evidence.create_pack(task.id, EvidenceProfile.LIGHT, [item], acceptance=task.acceptance)
    evidence.verify(pack, acceptance=task.acceptance)
    manager = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memory.json"))

    expected = build_task_vector_entries(ledger, evidence=evidence)
    stats = sync_task_vector_projection(manager, ledger=ledger, evidence=evidence)
    verify = vector_projection_verify(manager, expected, groups={"task_memory"})

    assert stats == {"vector_entries": 6, "upserted": 6, "skipped": 0}
    assert {entry["metadata"]["projection_kind"] for entry in expected} == {
        "mission",
        "task",
        "ledger_event",
        "evidence_pack",
        "verdict",
    }
    assert verify["ok"] is True


def test_vector_projection_verify_detects_stale_entries(tmp_path: Path) -> None:
    manager = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memory.json"))
    manager.upsert(
        "code:src/app.py",
        "old",
        tags=("projection",),
        metadata={"projection_group": "code", "content_hash": "sha256-old"},
    )
    expected = [{
        "id": "code:src/app.py",
        "text": "new",
        "metadata": {"projection_group": "code", "content_hash": "sha256-new"},
    }]

    stats = vector_projection_verify(manager, expected)

    assert stats["ok"] is False
    assert stats["stale"] == ["code:src/app.py"]


def test_graph_projection_verify_detects_missing_counts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission("Target", origin="user")
    ledger.create_task(mission.id, "Sync tasks", acceptance=("projection written",))
    graph = MagicMock()
    graph.stats.return_value = {
        "code_nodes": 0,
        "code_edges": 0,
        "missions": 0,
        "tasks": 0,
        "ledger_events": 0,
        "incidents": 0,
        "evidence_packs": 0,
        "verdicts": 0,
    }

    stats = graph_projection_verify(graph, project_root=tmp_path, code_paths=[Path("src")], ledger=ledger)

    assert stats["ok"] is False
    assert stats["issues"]


# ── Docs projection ───────────────────────────────────────────────────────────

def test_build_docs_entries(tmp_path: Path) -> None:
    from grimoire.memory.projections import build_docs_entries

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide d'installation\n\nContenu du guide.\n", encoding="utf-8")
    (docs / "empty.md").write_text("", encoding="utf-8")
    (docs / "node_modules").mkdir()
    (docs / "node_modules" / "vendored.md").write_text("# Vendored\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Projet\n\nPrésentation.\n", encoding="utf-8")

    entries = build_docs_entries(tmp_path, [Path("docs"), Path("README.md")], exclude={"node_modules"})

    ids = {entry["id"] for entry in entries}
    assert ids == {"docs:docs/guide.md", "docs:README.md"}
    guide = next(entry for entry in entries if entry["id"] == "docs:docs/guide.md")
    assert guide["metadata"]["title"] == "Guide d'installation"
    assert guide["metadata"]["projection_group"] == "docs"
    assert guide["metadata"]["truncated"] is False
    assert "Contenu du guide." in guide["text"]


def test_sync_docs_projection_is_idempotent(tmp_path: Path) -> None:
    from grimoire.memory.projections import sync_docs_projection

    docs = tmp_path / "docs"
    docs.mkdir()
    page = docs / "page.md"
    page.write_text("# Page\n\nVersion initiale.\n", encoding="utf-8")
    manager = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memory.json"))

    first = sync_docs_projection(manager, project_root=tmp_path, paths=[Path("docs")])
    second = sync_docs_projection(manager, project_root=tmp_path, paths=[Path("docs")])
    assert first == {"expected": 1, "upserted": 1, "unchanged": 0}
    assert second == {"expected": 1, "upserted": 0, "unchanged": 1}

    page.write_text("# Page\n\nVersion modifiée.\n", encoding="utf-8")
    third = sync_docs_projection(manager, project_root=tmp_path, paths=[Path("docs")])
    assert third["upserted"] == 1
    results = manager.search("modifiée")
    assert len(results) == 1
    assert results[0].id == "docs:docs/page.md"
