"""Tests for the optional Neo4j memory graph projection."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from grimoire.codegraph.schemas import CodeEdge, CodeNode, EdgeKind, NodeKind
from grimoire.evidence.schemas import (
    EvidenceProfile,
    VerdictDecision,
    VerdictResult,
    VerificationVerdict,
)
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact
from grimoire.missions.schemas import Mission, MissionState, MissionTask, RiskProfile, TaskState, TaskType


@dataclass(slots=True)
class _FakeCall:
    statement: str
    kwargs: dict[str, Any]


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[_FakeCall] = []
        self.closed = False
        self.connectivity_checked = False
        self.counts = {
            "GrimoireMemory": 2,
            "DeletedGrimoireMemory": 0,
            "GrimoireFact": 1,
            "GrimoireDiary": 1,
            "TAGGED_WITH": 3,
        }

    def execute_query(self, statement: str, **kwargs: Any) -> tuple[list[dict[str, int]], None, None]:
        self.calls.append(_FakeCall(statement=statement, kwargs=kwargs))
        if "coalesce(n.deleted, false) = $deleted" in statement:
            count_key = "DeletedGrimoireMemory" if kwargs["deleted"] else "GrimoireMemory"
            return ([{"count": self.counts[count_key]}], None, None)
        for label in ("GrimoireMemory", "GrimoireFact", "GrimoireDiary"):
            if f"(n:{label})" in statement:
                return ([{"count": self.counts[label]}], None, None)
        if "[r:TAGGED_WITH]" in statement:
            return ([{"count": self.counts["TAGGED_WITH"]}], None, None)
        return ([], None, None)

    def verify_connectivity(self) -> None:
        self.connectivity_checked = True

    def close(self) -> None:
        self.closed = True


class _FakeGraphDatabase:
    driver_instance = _FakeDriver()
    driver_calls: ClassVar[list[tuple[str, tuple[str, str]]]] = []

    @classmethod
    def driver(cls, uri: str, *, auth: tuple[str, str]) -> _FakeDriver:
        cls.driver_calls.append((uri, auth))
        return cls.driver_instance


@pytest.fixture()
def fake_graph(monkeypatch: pytest.MonkeyPatch) -> tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]:
    _FakeGraphDatabase.driver_instance = _FakeDriver()
    _FakeGraphDatabase.driver_calls = []
    monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_FakeGraphDatabase))
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    return Neo4jMemoryGraph, _FakeGraphDatabase.driver_instance, _FakeGraphDatabase


def _find_call(driver: _FakeDriver, needle: str) -> _FakeCall:
    return next(call for call in driver.calls if needle in call.statement)


def test_init_creates_driver_and_schema(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, graph_database = fake_graph

    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret", database="grimoire")

    assert graph_database.driver_calls == [("bolt://localhost:7687", ("neo4j", "secret"))]
    assert sum("CREATE CONSTRAINT" in call.statement for call in driver.calls) == 15

    graph.close()
    assert driver.closed is True


def test_upsert_memory_writes_node_and_tag_edges(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    entry = MemoryEntry(
        id="mem-1",
        text="Neo4j sync",
        user_id="guilhem",
        tags=("decision", "graph"),
        metadata={"room": "decisions"},
        created_at="2026-05-08T00:00:00",
        provenance={"source": "test"},
    )

    graph.upsert_memory(entry)

    memory_call = _find_call(driver, "MERGE (m:GrimoireMemory")
    assert memory_call.kwargs["id"] == "mem-1"
    assert memory_call.kwargs["tags"] == ["decision", "graph"]
    assert json.loads(memory_call.kwargs["metadata_json"]) == {"room": "decisions"}
    assert json.loads(memory_call.kwargs["provenance_json"]) == {"source": "test"}
    assert _find_call(driver, "UNWIND $tags AS tag_name").kwargs["tags"] == ["decision", "graph"]


def test_upsert_memory_writes_weaviate_reference(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    entry = MemoryEntry(
        id="mem-1",
        text="Neo4j sync",
        metadata={
            "weaviate_id": "wv-1",
            "weaviate_collection": "GrimoireKitMemory",
            "vector_backend": "weaviate-server",
        },
    )

    graph.upsert_memory(entry)

    vector_call = _find_call(driver, "MERGE (w:WeaviateObject")
    assert vector_call.kwargs["weaviate_id"] == "wv-1"
    assert vector_call.kwargs["weaviate_collection"] == "GrimoireKitMemory"
    assert "VECTORIZED_AS" in vector_call.statement
    assert "VECTOR_FOR" in vector_call.statement


def test_upsert_memory_links_projection_to_code_node(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    entry = MemoryEntry(
        id="code:src/app.py",
        text="code projection",
        metadata={
            "projection_kind": "code_chunk",
            "code_node_id": "src.app",
        },
    )

    graph.upsert_memory(entry)

    link_call = _find_call(driver, "MERGE (m)-[r:MEMORY_FOR")
    assert link_call.kwargs["memory_id"] == "code:src/app.py"
    assert link_call.kwargs["target_id"] == "src.app"
    assert link_call.kwargs["kind"] == "code_chunk"


def test_upsert_memory_links_granular_code_projection_to_code_node(
    fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]],
) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    entry = MemoryEntry(
        id="code-method:src.app:Service.run",
        text="method projection",
        metadata={
            "projection_kind": "code_method",
            "code_node_id": "src.app:Service.run",
        },
    )

    graph.upsert_memory(entry)

    link_call = _find_call(driver, "MERGE (m)-[r:MEMORY_FOR")
    assert link_call.kwargs["memory_id"] == "code-method:src.app:Service.run"
    assert link_call.kwargs["target_id"] == "src.app:Service.run"
    assert link_call.kwargs["kind"] == "code_method"


def test_delete_fact_and_diary_operations(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    fact = KnowledgeFact(
        id="fact-1",
        subject="team",
        predicate="chose",
        object="neo4j",
        source_memory_id="mem-1",
        wing="architecture",
        hall="hall_facts",
        room="decisions",
    )
    diary = DiaryRecord(
        id="diary-1",
        agent_name="atlas",
        topic="memory",
        entry="Graph projection verified",
        related_memory_id="mem-1",
    )

    graph.delete_memory("mem-1")
    graph.upsert_fact(fact)
    graph.invalidate_fact("team", "chose", "neo4j", ended="2026-05-08")
    graph.upsert_diary(diary)

    assert _find_call(driver, "SET m.deleted = true").kwargs["id"] == "mem-1"
    assert _find_call(driver, "MERGE (f:GrimoireFact").kwargs["object"] == "neo4j"
    assert _find_call(driver, "MERGE (f)-[:SOURCED_FROM]->(m)").kwargs["source_memory_id"] == "mem-1"
    assert _find_call(driver, "SET f.valid_to = $ended").kwargs["ended"] == "2026-05-08"
    assert _find_call(driver, "MERGE (d:GrimoireDiary").kwargs["agent_name"] == "atlas"
    assert _find_call(driver, "MERGE (d)-[:RELATED_TO]->(m)").kwargs["related_memory_id"] == "mem-1"


def test_health_check_returns_projection_stats(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret", database="grimoire")

    status = graph.health_check()

    assert status.healthy is True
    assert driver.connectivity_checked is True
    assert status.detail["database"] == "grimoire"
    assert status.detail["memories"] == 2
    assert status.detail["deleted_memories"] == 0
    assert status.detail["facts"] == 1
    assert status.detail["diary_entries"] == 1
    assert status.detail["tag_edges"] == 3


def test_code_and_task_projection_operations(fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]]) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    node = CodeNode(
        id="pkg.mod:run",
        kind=NodeKind.FUNCTION,
        name="run",
        file_path="src/pkg/mod.py",
        line_start=1,
        line_end=2,
        module="pkg.mod",
    )
    edge = CodeEdge(from_node="pkg.mod", to_node="pkg.mod:run", kind=EdgeKind.DEFINES)
    mission = Mission(
        id="MIS-target-001",
        title="Target",
        status=MissionState.OPEN,
        origin="user",
        created_at="2026-05-08T00:00:00",
    )
    task = MissionTask(
        id="GAO-target-001",
        mission_id=mission.id,
        title="Sync task memory",
        status=TaskState.READY,
        type=TaskType.IMPLEMENTATION,
        risk_profile=RiskProfile.STANDARD,
        acceptance=("projection written",),
        created_at="2026-05-08T00:00:00",
    )

    assert graph.upsert_code_nodes([node]) == 1
    assert graph.upsert_code_edges([edge]) == 1
    graph.upsert_mission(mission)
    graph.upsert_task(task)

    edge_call = _find_call(driver, "MERGE (a)-[r:CODE_EDGE")
    node_call = _find_call(driver, "MERGE (n:CodeNode")
    assert node_call.kwargs["nodes"][0]["id"] == "pkg.mod:run"
    assert "n.placeholder = false" in node_call.statement
    assert edge_call.kwargs["edges"][0]["kind"] == "defines"
    assert "a.placeholder = true" in edge_call.statement
    assert "b.placeholder = true" in edge_call.statement
    assert _find_call(driver, "MERGE (m:GrimoireMission").kwargs["id"] == "MIS-target-001"
    assert _find_call(driver, "MERGE (t:GrimoireTask").kwargs["id"] == "GAO-target-001"


def test_upsert_verdict_creates_decision_node(
    fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]],
) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")
    verdict = VerificationVerdict(
        id="ver-GAO-target-001-001",
        task_id="GAO-target-001",
        evidence_pack_id="EVD-GAO-target-001-001",
        verdict=VerdictResult.PASSED,
        profile=EvidenceProfile.LIGHT,
        checks=(),
        decision=VerdictDecision(close_task=True, reopen_task=False, create_incident=False),
        created_at="2026-05-08T00:00:00",
    )

    graph.upsert_verdict(verdict)

    call = _find_call(driver, "MERGE (d:GrimoireDecision")
    assert call.kwargs["decision_id"] == "decision:ver-GAO-target-001-001"
    assert call.kwargs["decision_close_task"] is True
    assert "MERGE (v)-[:PRODUCED_DECISION]->(d)" in call.statement
    assert "MERGE (t)-[:HAS_DECISION]->(d)" in call.statement


def test_replace_code_reference_edges(
    fake_graph: tuple[type[Any], _FakeDriver, type[_FakeGraphDatabase]],
) -> None:
    neo4j_memory_graph, driver, _ = fake_graph
    graph = neo4j_memory_graph(uri="bolt://localhost:7687", user="neo4j", password="secret")

    task_count = graph.replace_task_code_references(
        ["GAO-target-001"],
        [
            {
                "task_id": "GAO-target-001",
                "code_node_id": "src.app",
                "source": "task-context",
                "reason": "matched file",
            },
            {
                "task_id": "GAO-target-001",
                "code_node_id": "src.app",
                "source": "task-context",
                "reason": "duplicate",
            },
        ],
    )
    evidence_count = graph.replace_evidence_code_references(
        ["EVD-GAO-target-001-001"],
        [{
            "evidence_pack_id": "EVD-GAO-target-001-001",
            "code_node_id": "src.app",
            "source": "evidence-uri",
            "reason": "matched uri",
        }],
    )

    assert task_count == 1
    assert evidence_count == 1
    assert _find_call(driver, "DELETE old").kwargs["task_ids"] == ["GAO-target-001"]
    assert _find_call(driver, "MERGE (t)-[r:TOUCHES_CODE").kwargs["refs"][0]["code_node_id"] == "src.app"
    assert _find_call(driver, "MERGE (p)-[r:COVERS_CODE").kwargs["refs"][0]["evidence_pack_id"] == (
        "EVD-GAO-target-001-001"
    )


# ── #527 — orphan GrimoireMemory nodes, bounded purge ─────────────────────────


class _OrphanQueryDriver:
    """Just enough of the driver surface for orphan find/purge — no schema, no counts."""

    def __init__(self, orphans: list[dict[str, str]] | None = None) -> None:
        self._orphans = orphans or []
        self.calls: list[_FakeCall] = []

    def execute_query(self, statement: str, **kwargs: Any) -> tuple[list[dict[str, Any]], None, None]:
        self.calls.append(_FakeCall(statement=statement, kwargs=kwargs))
        if "NOT m.id IN $known_ids" in statement:
            return (list(self._orphans), None, None)
        return ([], None, None)


def _bare_graph(driver: Any) -> Any:
    """A Neo4jMemoryGraph wired straight to *driver*, skipping __init__'s schema DDL."""
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    graph = object.__new__(Neo4jMemoryGraph)
    graph._driver = driver  # type: ignore[attr-defined]
    graph._database = "neo4j"  # type: ignore[attr-defined]
    return graph


class TestFindOrphanMemoryNodes:
    def test_returns_nodes_the_query_reports(self) -> None:
        driver = _OrphanQueryDriver([{"id": "orphan-1", "collection": "GrimoireMemory"}])
        graph = _bare_graph(driver)

        orphans = graph.find_orphan_memory_nodes(known_ids=frozenset({"kept-1"}), collection="ProjectMemory")

        assert orphans == [{"id": "orphan-1", "collection": "GrimoireMemory"}]
        call = _find_call(driver, "NOT m.id IN $known_ids")
        assert call.kwargs["known_ids"] == ["kept-1"]

    def test_scope_is_the_project_collection_plus_the_legacy_generic_one(self) -> None:
        driver = _OrphanQueryDriver()
        graph = _bare_graph(driver)

        graph.find_orphan_memory_nodes(known_ids=frozenset(), collection="ProjectMemory")

        call = _find_call(driver, "NOT m.id IN $known_ids")
        assert set(call.kwargs["collections"]) == {"ProjectMemory", "GrimoireMemory"}

    def test_scope_does_not_duplicate_when_project_collection_is_the_legacy_one(self) -> None:
        driver = _OrphanQueryDriver()
        graph = _bare_graph(driver)

        graph.find_orphan_memory_nodes(known_ids=frozenset(), collection="GrimoireMemory")

        call = _find_call(driver, "NOT m.id IN $known_ids")
        assert call.kwargs["collections"] == ["GrimoireMemory"]

    def test_never_scans_the_whole_database_unscoped(self) -> None:
        """A purge must never be able to reach a different project's collection."""
        driver = _OrphanQueryDriver()
        graph = _bare_graph(driver)

        graph.find_orphan_memory_nodes(known_ids=frozenset(), collection="ProjectMemory")

        call = _find_call(driver, "NOT m.id IN $known_ids")
        assert "AnotherProjectMemory" not in call.kwargs["collections"]
        assert "WHERE m.weaviate_collection IN $collections" in call.statement


class TestPurgeMemoryNodes:
    def test_deletes_the_given_ids_and_reports_the_count(self) -> None:
        driver = _OrphanQueryDriver()
        graph = _bare_graph(driver)

        purged = graph.purge_memory_nodes(["a", "b", "c"])

        assert purged == 3
        call = driver.calls[0]
        assert "DETACH DELETE" in call.statement
        assert call.kwargs["ids"] == ["a", "b", "c"]

    def test_empty_list_is_a_no_op_and_issues_no_query(self) -> None:
        driver = _OrphanQueryDriver()
        graph = _bare_graph(driver)

        assert graph.purge_memory_nodes([]) == 0
        assert driver.calls == []
