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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("GRIMOIRE_TEST_NEO4J_URI"), reason="GRIMOIRE_TEST_NEO4J_URI absent (job CI neo4j-service)"),
]

neo4j = pytest.importorskip("neo4j")


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


class TestBackfill:
    def _seed_legacy(self, raw: Any, project: str, col: str) -> dict[str, str]:
        ids = {k: f"{k}-{uuid.uuid4().hex[:8]}" for k in ("by_col", "by_meta", "other_col", "other_meta", "code", "outside", "ghost")}
        mem = "CREATE (:GrimoireMemory {id: $id, text: 't', weaviate_collection: $col, metadata_json: $meta, deleted: false})"
        raw.execute_query(mem, id=ids["by_col"], col=col, meta="{}")
        raw.execute_query(mem, id=ids["by_meta"], col="", meta=f'{{"project_name": "{project}"}}')
        raw.execute_query(mem, id=ids["other_col"], col="autre-collection", meta="{}")
        raw.execute_query(mem, id=ids["other_meta"], col="", meta='{"project_name": "un-autre-projet"}')
        code = "CREATE (:CodeNode {id: $id, file_path: $fp, placeholder: $ph, name: $id})"
        raw.execute_query(code, id=f"{project}-src:f", fp="src/a.py", ph=False)
        raw.execute_query(code, id=f"{project}-docs:g", fp="docs/b.py", ph=False)
        raw.execute_query(code, id=f"{project}-ghost", fp="", ph=True)
        return ids

    def test_dry_run_counts_then_apply_attributes_then_replay_is_empty(self, raw: Any, projects: tuple[str, str]) -> None:
        project = projects[0]
        col = f"col-{project}"
        ids = self._seed_legacy(raw, project, col)
        graph = _graph(project)
        try:
            preview = graph.backfill_project(collections=[col], code_prefixes=["src/"], apply=False)
            assert (preview["memories_by_collection"], preview["memories_by_metadata"], preview["code_nodes"]) == (1, 1, 1)
            assert preview["code_id_conflicts"] == 0 and preview["ghosts_external"] >= 1
            assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) WHERE m.project IS NULL RETURN count(m) AS c", id=ids["by_col"]) == 1

            applied = graph.backfill_project(collections=[col], code_prefixes=["src/"], apply=True)
            assert applied["memories_by_collection"] == 1 and applied["memories_by_metadata"] == 1

            for key in ("by_col", "by_meta"):
                assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) WHERE m.project = $p RETURN count(m) AS c", id=ids[key], p=project) == 1
            for untouched in ("other_col", "other_meta"):
                assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) WHERE m.project IS NULL RETURN count(m) AS c", id=ids[untouched]) == 1
            assert _scalar(raw, "MATCH (n:CodeNode {id: $id, project: $p}) RETURN count(n) AS c", id=f"{project}::{project}-src:f", p=project) == 1
            assert _scalar(raw, "MATCH (n:CodeNode {id: $id}) WHERE n.project IS NULL RETURN count(n) AS c", id=f"{project}-docs:g") == 1
            assert _scalar(raw, "MATCH (n:CodeNode {id: $id, external: true}) WHERE n.project IS NULL RETURN count(n) AS c", id=f"{project}-ghost") == 1

            replay = graph.backfill_project(collections=[col], code_prefixes=["src/"], apply=True)
            assert (replay["memories_by_collection"], replay["memories_by_metadata"], replay["code_nodes"]) == (0, 0, 0)
        finally:
            graph.close()
            raw.execute_query("MATCH (n) WHERE n.id IN $ids OR n.id STARTS WITH $p OR n.id CONTAINS $p DETACH DELETE n", ids=list(ids.values()), p=project)
            raw.execute_query("MATCH (n:GrimoireMemory) WHERE n.weaviate_collection = 'autre-collection' AND n.text = 't' DETACH DELETE n")
