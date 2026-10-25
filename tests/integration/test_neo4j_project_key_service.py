"""P15 / P15b sur une vraie base Neo4j.

Exécutés par le job CI ``neo4j-service`` (service Neo4j 5.26, ``services:`` de
GitHub Actions) ; ignorés partout ailleurs, faute de ``GRIMOIRE_TEST_NEO4J_URI``.
Chaque test travaille sous des noms de projet uniques et nettoie derrière lui :
la base peut être partagée avec d'autres tests du même job.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from grimoire.codegraph.schemas import CodeEdge, CodeNode, EdgeKind, NodeKind
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact

# Le job CI pose GRIMOIRE_REQUIRE_NEO4J=1 : il n'a alors pas le droit de sauter ces tests
# (une variable renommée ou un extra absent doit échouer, pas passer au vert).
_REQUIRED = bool(os.environ.get("GRIMOIRE_REQUIRE_NEO4J"))
if _REQUIRED:
    import neo4j

    pytestmark = [pytest.mark.integration]
else:
    neo4j = pytest.importorskip("neo4j")
    pytestmark = [
        pytest.mark.integration,
        pytest.mark.skipif(not os.environ.get("GRIMOIRE_TEST_NEO4J_URI"), reason="GRIMOIRE_TEST_NEO4J_URI absent (job CI neo4j-service)"),
    ]


def _conn() -> tuple[str, str, str]:
    return (
        os.environ["GRIMOIRE_TEST_NEO4J_URI"],
        os.environ.get("GRIMOIRE_TEST_NEO4J_USER", "neo4j"),
        os.environ.get("GRIMOIRE_TEST_NEO4J_PASSWORD", "ci-password"),
    )


@pytest.fixture(scope="module")
def raw() -> Iterator[Any]:
    uri, user, password = _conn()
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    deadline = time.monotonic() + 90
    while True:
        try:
            driver.verify_connectivity()
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(3)
    yield driver
    driver.close()


def _scalar(raw: Any, statement: str, **params: Any) -> int:
    records, _, _ = raw.execute_query(statement, **params)
    return int(records[0]["c"])


def _graph(project: str) -> Any:
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    uri, user, password = _conn()
    graph = Neo4jMemoryGraph(uri=uri, user=user, password=password, project=project)
    graph.ensure_schema()
    return graph


@pytest.fixture
def projects(raw: Any) -> Iterator[tuple[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    names = (f"proj-a-{suffix}", f"proj-b-{suffix}")
    yield names
    raw.execute_query("MATCH (n) WHERE n.project IN $p DETACH DELETE n", p=list(names))
    raw.execute_query("MATCH (n) WHERE n.name STARTS WITH $a OR n.name STARTS WITH $b DETACH DELETE n", a=f"{names[0]}::", b=f"{names[1]}::")


def _populate(graph: Any, tag: str) -> None:
    graph.upsert_memory(MemoryEntry(id=f"{tag}-m1", text="mémoire", tags=("decision",), metadata={"weaviate_collection": f"col-{tag}"}))
    graph.upsert_fact(KnowledgeFact(id=f"{tag}-f1", subject="équipe", predicate="choisit", object="neo4j"))
    graph.upsert_diary(DiaryRecord(id=f"{tag}-d1", agent_name="atlas", topic="t", entry="e"))
    graph.upsert_code_nodes([CodeNode(id="pkg.mod:run", kind=NodeKind.FUNCTION, name="run", file_path="src/pkg/mod.py", line_start=1, line_end=2, module="pkg.mod")])
    graph.upsert_code_edges([CodeEdge(from_node="pkg.mod", to_node="pkg.mod:run", kind=EdgeKind.DEFINES)])


class TestTwoProjectsOneDatabase:
    def test_same_tag_entity_agent_and_code_id_stay_separate(self, raw: Any, projects: tuple[str, str]) -> None:
        a, b = projects
        ga, gb = _graph(a), _graph(b)
        try:
            _populate(ga, "a")
            _populate(gb, "b")

            for label, prop in (("GrimoireTag", "decision"), ("GrimoireEntity", "équipe"), ("GrimoireAgent", "atlas")):
                assert _scalar(raw, f"MATCH (n:{label}) WHERE n.label = $v AND n.project IN $p RETURN count(n) AS c", v=prop, p=[a, b]) == 2
            assert _scalar(raw, "MATCH (n:CodeNode) WHERE n.local_id = 'pkg.mod:run' AND n.project IN $p RETURN count(n) AS c", p=[a, b]) == 2
            assert _scalar(raw, "MATCH (n:CodeNode {id: $id}) RETURN count(n) AS c", id=f"{a}::pkg.mod:run") == 1
        finally:
            ga.close()
            gb.close()

    def test_stats_orphans_and_verify_only_see_their_own_project(self, projects: tuple[str, str]) -> None:
        from grimoire.memory.projections import graph_projection_verify

        a, b = projects
        ga, gb = _graph(a), _graph(b)
        try:
            _populate(ga, "a")
            _populate(gb, "b")
            gb.upsert_memory(MemoryEntry(id="b-m2", text="autre", metadata={"weaviate_collection": "col-b"}))

            sa, sb = ga.stats(), gb.stats()
            assert (sa["memories"], sb["memories"]) == (1, 2)
            assert (sa["code_nodes"], sb["code_nodes"]) == (2, 2)  # module + fonction (le module vient de l'arête)
            assert (sa["tag_edges"], sb["tag_edges"]) == (1, 1)

            # B ne connaît que b-m1 : b-m2 est orphelin pour B, jamais pour A, et A ne voit rien de B.
            assert [o["id"] for o in gb.find_orphan_memory_nodes(known_ids=frozenset({"b-m1"}), collection="col-b")] == ["b-m2"]
            assert ga.find_orphan_memory_nodes(known_ids=frozenset(), collection="col-b") == []
            report = graph_projection_verify(ga)
            assert report["ok"] is True
        finally:
            ga.close()
            gb.close()

    def test_construction_does_not_write(self, raw: Any, projects: tuple[str, str]) -> None:
        from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

        before = _scalar(raw, "MATCH (n) RETURN count(n) AS c")
        uri, user, password = _conn()
        Neo4jMemoryGraph(uri=uri, user=user, password=password, project=projects[0]).close()
        assert _scalar(raw, "MATCH (n) RETURN count(n) AS c") == before


class TestLedgerNodesDoNotLeakAcrossProjects:
    def test_same_mission_task_and_fact_ids_stay_separate(self, raw: Any, projects: tuple[str, str]) -> None:
        from grimoire.missions.schemas import Mission, MissionState, MissionTask, RiskProfile, TaskState, TaskType

        a, b = projects
        for project, title in ((a, "mission de A"), (b, "mission de B")):
            graph = _graph(project)
            try:
                graph.upsert_mission(Mission(id="MIS-1", title=title, status=MissionState.OPEN, origin="user", created_at="2026-01-01T00:00:00"))
                graph.upsert_task(MissionTask(
                    id="T-1", mission_id="MIS-1", title=f"tâche de {project}", status=TaskState.READY,
                    type=TaskType.IMPLEMENTATION, risk_profile=RiskProfile.STANDARD, acceptance=("ok",),
                    created_at="2026-01-01T00:00:00",
                ))
                graph.upsert_fact(KnowledgeFact(id="fact-hash", subject="s", predicate="p", object="o"))
            finally:
                graph.close()

        for project, title in ((a, "mission de A"), (b, "mission de B")):
            assert _scalar(raw, "MATCH (m:GrimoireMission {id: $id, title: $t, project: $p}) RETURN count(m) AS c", id=f"{project}::MIS-1", t=title, p=project) == 1
            assert _scalar(raw, "MATCH (t:GrimoireTask {id: $id, project: $p})<-[:HAS_TASK]-(:GrimoireMission {id: $m}) RETURN count(t) AS c", id=f"{project}::T-1", p=project, m=f"{project}::MIS-1") == 1
            assert _scalar(raw, "MATCH (f:GrimoireFact {id: $id, project: $p}) RETURN count(f) AS c", id=f"{project}::fact-hash", p=project) == 1


class TestDeadServer:
    def test_unreachable_server_fails_fast_then_instantly(self) -> None:
        from grimoire.memory.neo4j_graph import GraphUnavailableError, Neo4jMemoryGraph

        graph = Neo4jMemoryGraph(uri="bolt://127.0.0.1:1", user="neo4j", password="x", project="p")
        started = time.monotonic()
        with pytest.raises(Exception):  # noqa: B017 - ServiceUnavailable du pilote
            graph.upsert_memory(MemoryEntry(id="m1", text="t"))
        first = time.monotonic() - started
        started = time.monotonic()
        with pytest.raises(GraphUnavailableError):
            graph.upsert_memory(MemoryEntry(id="m2", text="t"))
        assert first < 15 and time.monotonic() - started < 0.5
        graph.close()
