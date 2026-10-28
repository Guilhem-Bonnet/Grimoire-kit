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
            # La suppression d'orphelins passe par l'identifiant du nœud, et ne touche que lui.
            from grimoire.memory.projections import prune_orphan_memories

            known = [MemoryEntry(id="b-m1", text="mémoire")]
            preview = prune_orphan_memories(gb, known, collection="col-b")
            assert (preview["candidates"], preview["ids"], preview["purged"]) == (1, ["b-m2"], 0)
            assert prune_orphan_memories(gb, known, collection="col-b", apply=True)["purged"] == 1
            assert gb.stats()["memories"] == 1 and ga.stats()["memories"] == 1
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


class TestKeyedRelationshipsOnARealBase:
    """Les requêtes à clés préfixées que le faux pilote ne peut pas éprouver : arêtes, liens, suppressions."""

    def _count(self, raw: Any, rel: str, project: str) -> int:
        return _scalar(raw, f"MATCH (a)-[r:{rel}]->() WHERE a.project = $p RETURN count(r) AS c", p=project)

    def test_every_keyed_edge_is_created_and_scoped(self, raw: Any, projects: tuple[str, str]) -> None:
        from grimoire.evidence.schemas import EvidenceProfile, VerdictDecision, VerdictResult, VerificationVerdict
        from grimoire.missions.schemas import (
            DependencyKind,
            LedgerEvent,
            Mission,
            MissionState,
            MissionTask,
            RiskProfile,
            TaskDependency,
            TaskState,
            TaskType,
        )

        project = projects[0]
        graph = _graph(project)
        try:
            graph.upsert_code_nodes([CodeNode(id="pkg:run", kind=NodeKind.FUNCTION, name="run", file_path="src/p.py", line_start=1, line_end=2, module="pkg")])
            graph.upsert_memory(MemoryEntry(
                id="m1", text="lien code", tags=("t",),
                metadata={"weaviate_id": "w-1", "weaviate_collection": "col", "projection_kind": "code_symbol", "code_node_id": "pkg:run"},
            ))
            graph.upsert_fact(KnowledgeFact(id="f1", subject="s", predicate="p", object="o", source_memory_id="m1"))
            graph.upsert_diary(DiaryRecord(id="d1", agent_name="atlas", topic="t", entry="e", related_memory_id="m1"))
            graph.upsert_mission(Mission(id="MIS-1", title="m", status=MissionState.OPEN, origin="user", created_at="2026-01-01T00:00:00"))
            graph.upsert_task(MissionTask(
                id="T-1", mission_id="MIS-1", title="t", status=TaskState.READY, type=TaskType.IMPLEMENTATION,
                risk_profile=RiskProfile.STANDARD, acceptance=("ok",), created_at="2026-01-01T00:00:00",
                dependencies=(TaskDependency(kind=DependencyKind.RELATES, target="T-0"),),
            ))
            graph.upsert_ledger_event(LedgerEvent(id="ev1", event_type="task.created", entity_id="T-1", entity_kind="task", actor_id="a", created_at="2026-01-01T00:00:00"))
            graph.upsert_verdict(VerificationVerdict(
                id="ver-1", task_id="T-1", evidence_pack_id="EVD-1", verdict=VerdictResult.PASSED, profile=EvidenceProfile.LIGHT,
                checks=(), decision=VerdictDecision(close_task=True, reopen_task=False, create_incident=False),
                created_at="2026-01-01T00:00:00",
            ))
            graph.replace_task_code_references(["T-1"], [{"task_id": "T-1", "code_node_id": "pkg:run", "source": "ctx", "reason": "r"}])

            for rel in ("TAGGED_WITH", "VECTORIZED_AS", "MEMORY_FOR", "SOURCED_FROM", "DEPENDS_ON", "HAS_TASK", "HAS_EVENT", "HAS_VERDICT", "HAS_DECISION", "TOUCHES_CODE"):
                assert self._count(raw, rel, project) >= 1, f"arête {rel} absente"
            assert _scalar(raw, "MATCH (:GrimoireDiary {id: $id})-[r:RELATED_TO]->(:GrimoireMemory {id: $m}) RETURN count(r) AS c", id=f"{project}::d1", m=f"{project}::m1") == 1
            # Le stub de dépendance porte le projet.
            assert _scalar(raw, "MATCH (t:GrimoireTask {id: $id, project: $p}) RETURN count(t) AS c", id=f"{project}::T-0", p=project) == 1

            graph.delete_memory("m1")
            assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id, deleted: true}) RETURN count(m) AS c", id=f"{project}::m1") == 1
            graph.invalidate_fact("s", "p", "o", ended="2026-02-01")
            assert _scalar(raw, "MATCH (f:GrimoireFact {id: $id}) WHERE f.valid_to = '2026-02-01' RETURN count(f) AS c", id=f"{project}::f1") == 1
        finally:
            graph.close()


class TestDeleteErasesContent:
    """P23 : supprimer un souvenir efface son contenu dans le graphe, puis ``purge-orphans`` retire la pierre tombale."""

    def test_deleted_node_has_no_text_and_the_purge_removes_it(self, raw: Any, projects: tuple[str, str]) -> None:
        a, _ = projects
        graph = _graph(a)
        try:
            graph.upsert_memory(MemoryEntry(
                id="a-del", text="contenu à effacer", tags=("decision",), source="task:T-1",
                metadata={"weaviate_collection": "col-a", "note": "métadonnée à effacer"},
            ))
            graph.delete_memory("a-del")

            records, _, _ = raw.execute_query(
                "MATCH (m:GrimoireMemory {id: $id}) RETURN m.text AS text, m.deleted AS deleted, "
                "m.metadata_json AS meta, m.tags AS tags, m.source AS source",
                id=f"{a}::a-del",
            )
            (row,) = records
            assert row["deleted"] is True
            assert row["text"] == ""
            assert row["meta"] == "{}"
            assert list(row["tags"]) == []
            assert row["source"] == ""
            assert _scalar(raw, "MATCH (:GrimoireMemory {id: $id})-[r:TAGGED_WITH]->() RETURN count(r) AS c", id=f"{a}::a-del") == 0

            # Enchaînement documenté : la suppression efface le contenu, `graph purge-orphans --apply` retire le nœud.
            orphans = graph.find_orphan_memory_nodes(known_ids=frozenset(), collection="col-a")
            assert "a-del" in {o["id"] for o in orphans}
            graph.purge_memory_nodes([o.get("node_id", o["id"]) for o in orphans])
            assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) RETURN count(m) AS c", id=f"{a}::a-del") == 0
        finally:
            graph.close()

    def test_a_non_weaviate_memory_leaves_no_edge_and_its_tombstone_is_purged(
        self, raw: Any, projects: tuple[str, str],
    ) -> None:
        """Un souvenir hors Weaviate n'a pas de ``weaviate_collection`` : sa pierre tombale doit quand même partir."""
        a, b = projects
        ga, gb = _graph(a), _graph(b)
        try:
            ga.upsert_memory(MemoryEntry(id="a-plain", text="contenu local", tags=("decision",), metadata={"projection_kind": "task", "projection_source_id": "t1"}))
            ga.upsert_memory(MemoryEntry(id="a-vec", text="contenu vectoriel", metadata={"weaviate_id": "w1", "weaviate_collection": "col-a"}))
            gb.upsert_memory(MemoryEntry(id="b-plain", text="autre projet"))
            gb.delete_memory("b-plain")
            ga.delete_memory("a-plain")
            ga.delete_memory("a-vec")

            for local in ("a-plain", "a-vec"):
                assert _scalar(
                    raw, "MATCH (m:GrimoireMemory {id: $id})-[r]-() RETURN count(r) AS c", id=f"{a}::{local}",
                ) == 0, "toutes les arêtes (étiquettes, MEMORY_FOR, VECTORIZED_AS, VECTOR_FOR) sont retirées"

            orphans = ga.find_orphan_memory_nodes(known_ids=frozenset(), collection="col-a")
            assert {o["id"] for o in orphans} == {"a-plain", "a-vec"}, "la pierre tombale sans collection est reconnue"
            ga.purge_memory_nodes([o["node_id"] for o in orphans])
            assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) RETURN count(m) AS c", id=f"{a}::a-plain") == 0
            # Jamais la pierre tombale d'un autre projet.
            assert _scalar(raw, "MATCH (m:GrimoireMemory {id: $id}) RETURN count(m) AS c", id=f"{b}::b-plain") == 1
        finally:
            ga.close()
            gb.close()


class TestBackfillProject:
    """Rattrapage d'une base écrite avant la clé de projet (deux projets, mêmes étiquettes et chemins)."""

    @pytest.fixture
    def legacy(self, raw: Any, projects: tuple[str, str], tmp_path: Any) -> Iterator[dict[str, Any]]:
        a, b = projects
        root = tmp_path / "proj-a"
        (root / "src" / "pkg").mkdir(parents=True)
        (root / "src" / "pkg" / "mod.py").write_text("x = 1\n")
        sfx = a.rsplit("-", 1)[1]
        ids = {k: f"{k}-{sfx}" for k in ("m-a", "m-b", "m-foreign", "m-dup", "f-a", "d-a", "n-a", "n-b", "n-gone")}
        raw.execute_query(
            """
            CREATE (ma:GrimoireMemory {id: $ma, weaviate_collection: $ca, metadata_json: '{}', deleted: false})
            CREATE (mb:GrimoireMemory {id: $mb, weaviate_collection: $cb, metadata_json: '{}', deleted: false})
            CREATE (mf:GrimoireMemory {id: $mf, weaviate_collection: $ca, deleted: false,
                    metadata_json: '{"project_name": "ailleurs"}'})
            CREATE (mm:GrimoireMemory {id: $mm, weaviate_collection: 'autre', deleted: false,
                    metadata_json: '{"project_name": "' + $a + '"}'})
            CREATE (ta:GrimoireTag {name: 'solo-' + $sfx}), (ts:GrimoireTag {name: 'commun-' + $sfx})
            CREATE (ma)-[:TAGGED_WITH]->(ta), (ma)-[:TAGGED_WITH]->(ts), (mb)-[:TAGGED_WITH]->(ts)
            CREATE (f:GrimoireFact {id: $fa, subject: 's', predicate: 'p', object: 'o'})-[:SOURCED_FROM]->(ma)
            CREATE (d:GrimoireDiary {id: $da, agent_name: 'atlas'})-[:RELATED_TO]->(ma)
            CREATE (w:WeaviateObject {id: 'w-' + $sfx})<-[:VECTORIZED_AS]-(ma)
            CREATE (:CodeNode {id: 'pkg.mod:run', file_path: 'src/pkg/mod.py', placeholder: false})
            CREATE (:CodeNode {id: 'pkg.gone:run', file_path: 'src/pkg/gone.py', placeholder: false})
            CREATE (:CodeNode {id: 'ghost-' + $sfx, file_path: '', placeholder: true})
            """,
            ma=ids["m-a"], mb=ids["m-b"], mf=ids["m-foreign"], mm=ids["m-dup"], fa=ids["f-a"], da=ids["d-a"],
            ca=f"col-{a}", cb=f"col-{b}", a=a, sfx=sfx,
        )
        yield {"a": a, "root": root, "ids": ids, "sfx": sfx}
        raw.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.id IN ['pkg.mod:run', 'pkg.gone:run', $g, $w] "
            "OR n.name ENDS WITH $sfx OR n.id STARTS WITH $pre DETACH DELETE n",
            ids=list(ids.values()), g=f"ghost-{sfx}", w=f"w-{sfx}", sfx=sfx, pre=f"{a}::",
        )

    def _run(self, legacy: dict[str, Any], *, apply: bool) -> dict[str, Any]:
        from grimoire.memory.neo4j_backfill import backfill_project, resolve_code_prefixes

        a = legacy["a"]
        graph = _graph(a)
        try:
            return backfill_project(
                graph,
                collections=[f"col-{a}"],
                project_root=legacy["root"],
                code_prefixes=resolve_code_prefixes(legacy["root"], ["src"]),
                apply=apply,
            )
        finally:
            graph.close()

    def test_preview_counts_and_writes_nothing(self, raw: Any, legacy: dict[str, Any]) -> None:
        report = self._run(legacy, apply=False)
        assert report["memories_by_collection"] == 1  # m-a ; m-foreign porte un autre project_name
        assert report["memories_by_metadata"] == 1  # m-dup
        assert report["memories_other_project"] == 1
        assert (report["facts"], report["diaries"], report["weaviate_objects"]) == (1, 1, 1)
        assert report["tags"] == 1 and report["tags_shared"] == 1  # solo ; commun avec le projet B
        assert (report["code_nodes"], report["code_nodes_not_in_project"]) == (1, 1)
        assert report["ghosts_external"] >= 1
        assert _scalar(raw, "MATCH (n) WHERE n.project = $p RETURN count(n) AS c", p=legacy["a"]) == 0

    def test_apply_attributes_prefixes_and_is_replayable(self, raw: Any, legacy: dict[str, Any]) -> None:
        a, sfx, ids = legacy["a"], legacy["sfx"], legacy["ids"]
        self._run(legacy, apply=True)

        for kind in ("m-a", "m-dup"):
            assert _scalar(raw, "MATCH (n:GrimoireMemory {id: $id, project: $p}) RETURN count(n) AS c", id=f"{a}::{ids[kind]}", p=a) == 1
        # Le souvenir d'un autre projet et celui de la collection de B restent intacts.
        assert _scalar(raw, "MATCH (n:GrimoireMemory {id: $id}) WHERE n.project IS NULL RETURN count(n) AS c", id=ids["m-foreign"]) == 1
        assert _scalar(raw, "MATCH (n:GrimoireMemory {id: $id}) WHERE n.project IS NULL RETURN count(n) AS c", id=ids["m-b"]) == 1
        assert _scalar(raw, "MATCH (n:GrimoireFact {id: $id, project: $p}) RETURN count(n) AS c", id=f"{a}::{ids['f-a']}", p=a) == 1
        assert _scalar(raw, "MATCH (n:GrimoireDiary {id: $id, project: $p}) RETURN count(n) AS c", id=f"{a}::{ids['d-a']}", p=a) == 1
        assert _scalar(raw, "MATCH (n:WeaviateObject {id: $id, project: $p}) RETURN count(n) AS c", id=f"{a}::w-{sfx}", p=a) == 1
        assert _scalar(raw, "MATCH (n:GrimoireTag {name: $n, project: $p}) RETURN count(n) AS c", n=f"{a}::solo-{sfx}", p=a) == 1
        # Le nom partagé entre deux projets n'est pas attribué, ni fusionné.
        assert _scalar(raw, "MATCH (n:GrimoireTag {name: $n}) WHERE n.project IS NULL RETURN count(n) AS c", n=f"commun-{sfx}") == 1
        assert _scalar(raw, "MATCH (n:CodeNode {id: $id, project: $p, local_id: 'pkg.mod:run'}) RETURN count(n) AS c", id=f"{a}::pkg.mod:run", p=a) == 1
        assert _scalar(raw, "MATCH (n:CodeNode {id: 'pkg.gone:run'}) WHERE n.project IS NULL RETURN count(n) AS c") == 1
        assert _scalar(raw, "MATCH (n:CodeNode {id: $id, external: true}) RETURN count(n) AS c", id=f"ghost-{sfx}") == 1
        # Les relations suivent le nœud renommé.
        assert _scalar(raw, "MATCH (:GrimoireFact {id: $f})-[:SOURCED_FROM]->(:GrimoireMemory {id: $m}) RETURN count(*) AS c", f=f"{a}::{ids['f-a']}", m=f"{a}::{ids['m-a']}") == 1

        second = self._run(legacy, apply=True)
        assert (second["memories_by_collection"], second["memories_by_metadata"], second["facts"], second["code_nodes"]) == (0, 0, 0, 0)

    def test_id_conflict_is_listed_and_left_untouched(self, raw: Any, legacy: dict[str, Any]) -> None:
        a, ids = legacy["a"], legacy["ids"]
        raw.execute_query("CREATE (:GrimoireMemory {id: $id, project: $p, deleted: false})", id=f"{a}::{ids['m-a']}", p=a)
        report = self._run(legacy, apply=True)
        assert {"label": "GrimoireMemory", "id": ids["m-a"]} in report["conflicts"]
        assert _scalar(raw, "MATCH (n:GrimoireMemory {id: $id}) WHERE n.project IS NULL RETURN count(n) AS c", id=ids["m-a"]) == 1
