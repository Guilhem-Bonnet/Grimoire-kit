"""P15 / P15b — clé de projet dans le graphe Neo4j, identifiants préfixés, rattrapage.

Pilote factice qui enregistre les requêtes : aucun service. Les mêmes garanties
sont rejouées sur une vraie base par ``tests/integration/test_neo4j_project_key_service.py``
(job CI avec un service Neo4j 5.26).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.codegraph.schemas import CodeEdge, CodeNode, EdgeKind, NodeKind
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact
from grimoire.missions.schemas import (
    DependencyKind,
    Mission,
    MissionState,
    MissionTask,
    RiskProfile,
    TaskDependency,
    TaskState,
    TaskType,
)


class _Driver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalars: dict[str, int] = {}

    def execute_query(self, statement: str, **kwargs: Any) -> tuple[list[dict[str, int]], None, None]:
        self.calls.append((statement, kwargs))
        if "RETURN m.id AS id" in statement:
            return ([], None, None)
        return ([{"count": self.scalars.get(_first_clause(statement), 0)}], None, None)

    def verify_connectivity(self) -> None: ...

    def close(self) -> None: ...


def _first_clause(statement: str) -> str:
    return " ".join(statement.split())


class _GraphDatabase:
    driver_instance = _Driver()

    @classmethod
    def driver(cls, uri: str, *, auth: tuple[str, str], **_options: Any) -> _Driver:
        return cls.driver_instance


@pytest.fixture()
def driver(monkeypatch: pytest.MonkeyPatch) -> _Driver:
    _GraphDatabase.driver_instance = _Driver()
    monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
    return _GraphDatabase.driver_instance


def _graph(project: str = "projet-a") -> Any:
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

    return Neo4jMemoryGraph(uri="bolt://x:7687", user="neo4j", password="pw", project=project)


def _writes(driver: _Driver) -> list[tuple[str, dict[str, Any]]]:
    return [(s, k) for s, k in driver.calls if any(w in s for w in ("MERGE", "SET ", "DELETE", "CREATE"))]


class TestNoWriteAtConstruction:
    def test_building_the_graph_issues_no_query(self, driver: _Driver) -> None:
        _graph()
        assert driver.calls == []

    def test_building_a_memory_manager_issues_no_write_query(
        self, driver: _Driver, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        from grimoire.memory.manager import MemoryManager

        monkeypatch.setenv("GK_TEST_PW", "secret")
        cfg = GrimoireConfig.from_dict({
            "project": {"name": "projet-a"},
            "memory": {
                "backend": "local", "memory_graph": "neo4j",
                "neo4j_uri": "bolt://x:7687", "neo4j_password_env": "GK_TEST_PW",
            },
        })

        mgr = MemoryManager.from_config(cfg, project_root=tmp_path)

        assert mgr.memory_graph is not None
        assert mgr.memory_graph.project == "projet-a"
        assert _writes(driver) == []
        assert driver.calls == []

    def test_schema_is_created_by_ensure_schema_only(self, driver: _Driver) -> None:
        graph = _graph()
        graph.ensure_schema()
        assert sum("CREATE CONSTRAINT" in s for s, _ in driver.calls) == 15


class TestEveryUpsertCarriesTheProject:
    def test_all_node_writes_set_project(self, driver: _Driver) -> None:
        graph = _graph()
        graph.upsert_memory(MemoryEntry(id="m1", text="t", tags=("x",), metadata={"weaviate_id": "w1"}))
        graph.upsert_fact(KnowledgeFact(id="f1", subject="s", predicate="p", object="o"))
        graph.upsert_diary(DiaryRecord(id="d1", agent_name="atlas", topic="t", entry="e"))
        graph.upsert_code_nodes([CodeNode(id="a:f", kind=NodeKind.FUNCTION, name="f", file_path="src/a.py", line_start=1, line_end=2, module="a")])
        graph.upsert_code_edges([CodeEdge(from_node="a", to_node="a:f", kind=EdgeKind.DEFINES)])
        graph.upsert_mission(Mission(id="MIS-1", title="m", status=MissionState.OPEN, origin="user", created_at="2026-01-01T00:00:00"))
        graph.upsert_task(MissionTask(
            id="T-1", mission_id="MIS-1", title="t", status=TaskState.READY, type=TaskType.IMPLEMENTATION,
            risk_profile=RiskProfile.STANDARD, acceptance=("ok",), created_at="2026-01-01T00:00:00",
        ))

        node_writes = [(s, k) for s, k in driver.calls if "MERGE (" in s and "SET " in s]
        assert len(node_writes) >= 8, "aucune écriture de nœud observée"
        for statement, kwargs in node_writes:
            assert "project" in statement, statement
            assert kwargs["project"] == "projet-a"

    def test_without_a_project_key_nothing_changes(self, driver: _Driver) -> None:
        graph = _graph(project="")
        graph.upsert_code_nodes([CodeNode(id="a:f", kind=NodeKind.FUNCTION, name="f", file_path="x.py", line_start=1, line_end=1, module="a")])
        _, kwargs = driver.calls[-1]
        assert kwargs["nodes"][0]["id"] == "a:f"
        assert kwargs["prefix"] == ""


class TestPrefixedIdentifiers:
    def test_code_node_and_edge_ids_are_prefixed(self, driver: _Driver) -> None:
        graph = _graph()
        graph.upsert_code_nodes([CodeNode(id="a:f", kind=NodeKind.FUNCTION, name="f", file_path="src/a.py", line_start=1, line_end=2, module="a")])
        graph.upsert_code_edges([CodeEdge(from_node="a", to_node="a:f", kind=EdgeKind.DEFINES)])

        node_row = next(k for s, k in driver.calls if "MERGE (n:CodeNode" in s)["nodes"][0]
        edge_row = next(k for s, k in driver.calls if "CODE_EDGE" in s and "UNWIND" in s)["edges"][0]
        assert (node_row["id"], node_row["local_id"]) == ("projet-a::a:f", "a:f")
        assert (edge_row["from_node"], edge_row["to_node"]) == ("projet-a::a", "projet-a::a:f")

    def test_two_projects_never_share_a_tag_entity_or_agent(self, driver: _Driver) -> None:
        for project in ("projet-a", "projet-b"):
            graph = _graph(project)
            graph.upsert_memory(MemoryEntry(id=f"m-{project}", text="t", tags=("decision",)))
            graph.upsert_fact(KnowledgeFact(id=f"f-{project}", subject="équipe", predicate="p", object="o"))
            graph.upsert_diary(DiaryRecord(id=f"d-{project}", agent_name="atlas", topic="t", entry="e"))

        tag_calls = [k for s, k in driver.calls if "MERGE (t:GrimoireTag" in s]
        assert {k["prefix"] for k in tag_calls} == {"projet-a::", "projet-b::"}
        for statement in (s for s, _ in driver.calls if "GrimoireTag {name" in s or "GrimoireEntity {name" in s or "GrimoireAgent {name" in s):
            assert "$prefix +" in statement, statement

    def test_memory_to_code_links_and_references_use_the_prefixed_id(self, driver: _Driver) -> None:
        graph = _graph()
        graph.upsert_memory(MemoryEntry(
            id="m1", text="t", metadata={"projection_kind": "code_symbol", "code_node_id": "a:f"},
        ))
        link = next(k for s, k in driver.calls if "MEMORY_FOR {kind" in s and "CodeNode" in s)
        assert link["target_id"] == "projet-a::a:f"

        graph.replace_task_code_references(["T-1"], [{"task_id": "T-1", "code_node_id": "a:f", "source": "s"}])
        refs = next(k for s, k in driver.calls if "TOUCHES_CODE {source" in s)["refs"]
        assert refs[0]["code_node_id"] == "projet-a::a:f"

    def test_placeholder_ghosts_are_marked_external(self, driver: _Driver) -> None:
        _graph().upsert_code_edges([CodeEdge(from_node="a", to_node="os", kind=EdgeKind.IMPORTS)])
        statement = next(s for s, _ in driver.calls if "CODE_EDGE" in s)
        assert statement.count("external = true") == 2


class TestScopedReads:
    def test_counts_filter_on_the_project(self, driver: _Driver) -> None:
        _graph().stats()
        counting = [(s, k) for s, k in driver.calls if "count(" in s and "unattributed" not in s]
        assert counting
        for statement, kwargs in counting:
            if "labels(n)" in statement:
                continue
            assert "$project" in statement, statement
            assert kwargs["project"] == "projet-a"

    def test_legacy_nodes_without_a_project_stay_visible(self, driver: _Driver) -> None:
        """Pas de rattrapage livré : un nœud antérieur à la clé ne disparaît pas des lectures."""
        _graph().stats()
        _graph().find_orphan_memory_nodes(known_ids=frozenset(), collection="c")
        reads = [s for s, _ in driver.calls if "count(" in s or "NOT m.id IN" in s]
        assert reads and all("IS NULL" in s for s in reads)

    def test_orphans_are_looked_up_inside_the_project(self, driver: _Driver) -> None:
        _graph().find_orphan_memory_nodes(known_ids=frozenset(), collection="c")
        statement, kwargs = next((s, k) for s, k in driver.calls if "NOT m.id IN $known_ids" in s)
        assert "m.project = $project" in statement and kwargs["project"] == "projet-a"

    def test_projection_verify_reads_scoped_counts(self, driver: _Driver, tmp_path: Any) -> None:
        from grimoire.memory.projections import graph_projection_verify

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        report = graph_projection_verify(_graph(), project_root=tmp_path, code_paths=[tmp_path / "src"])
        assert report["ok"] is False  # le pilote factice répond 0 : rien n'est projeté
        assert all(k["project"] == "projet-a" for s, k in driver.calls if "count(" in s and "labels(n)" not in s)


class TestSchemaOnEveryWritePath:
    """Une seule fonction pose le schéma ; toute voie d'écriture la traverse avant son premier ordre."""

    @pytest.mark.parametrize("write", [
        lambda g: g.upsert_memory(MemoryEntry(id="m", text="t")),
        lambda g: g.upsert_fact(KnowledgeFact(id="f", subject="s", predicate="p", object="o")),
        lambda g: g.invalidate_fact("s", "p", "o", ended="2026-01-01"),
        lambda g: g.upsert_diary(DiaryRecord(id="d", agent_name="a", topic="t", entry="e")),
        lambda g: g.upsert_code_nodes([CodeNode(id="a:f", kind=NodeKind.FUNCTION, name="f", file_path="x.py", line_start=1, line_end=1, module="a")]),
        lambda g: g.delete_memory("m"),
    ])
    def test_first_write_of_any_path_creates_the_schema_first_and_once(self, driver: _Driver, write: Any) -> None:
        graph = _graph()
        write(graph)
        first_write = next(i for i, (s, _) in enumerate(driver.calls) if "CREATE CONSTRAINT" not in s)
        assert first_write == 15  # les quinze contraintes précèdent le premier ordre d'écriture
        constraints = sum("CREATE CONSTRAINT" in s for s, _ in driver.calls)
        write(graph)
        assert sum("CREATE CONSTRAINT" in s for s, _ in driver.calls) == constraints  # posé une seule fois


class _DeadDriver(_Driver):
    def execute_query(self, statement: str, **kwargs: Any) -> Any:
        self.calls.append((statement, kwargs))
        raise ConnectionError("injoignable")


class TestDeadGraphCircuitBreaker:
    def test_second_write_does_not_pay_the_failure_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dead = _DeadDriver()
        _GraphDatabase.driver_instance = dead
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        graph = _graph()

        with pytest.raises(ConnectionError):
            graph.upsert_memory(MemoryEntry(id="m1", text="t"))
        attempts = len(dead.calls)
        assert attempts == 1  # la pose du schéma échoue au premier ordre

        from grimoire.memory.neo4j_graph import GraphUnavailableError

        for write in (
            lambda: graph.upsert_memory(MemoryEntry(id="m2", text="t")),
            lambda: graph.upsert_fact(KnowledgeFact(id="f", subject="s", predicate="p", object="o")),
            lambda: graph.stats(),
        ):
            with pytest.raises(GraphUnavailableError):
                write()
        assert len(dead.calls) == attempts  # aucun appel au pilote pendant que le disjoncteur est ouvert

    def test_the_breaker_closes_after_the_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.memory import neo4j_graph as ng

        dead = _DeadDriver()
        _GraphDatabase.driver_instance = dead
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        graph = _graph()
        clock = {"t": 1000.0}
        monkeypatch.setattr(ng.time, "monotonic", lambda: clock["t"])

        with pytest.raises(ConnectionError):
            graph.upsert_memory(MemoryEntry(id="m1", text="t"))
        clock["t"] += ng.GRAPH_COOLDOWN_SECONDS + 1
        with pytest.raises(ConnectionError):  # le pilote est de nouveau interrogé
            graph.upsert_memory(MemoryEntry(id="m2", text="t"))
        assert len(dead.calls) == 2

    def test_the_driver_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        class _DB:
            @staticmethod
            def driver(uri: str, *, auth: tuple[str, str], **options: Any) -> _Driver:
                seen.update(options)
                return _Driver()

        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_DB))
        _graph()
        assert seen["max_transaction_retry_time"] <= 5 and seen["connection_timeout"] <= 5


class TestProjectsSharingLedgerNodes:
    """Deux projets qui réutilisent un identifiant de mission, de tâche ou de fait ne s'écrasent pas."""

    def test_ledger_and_fact_ids_are_keyed_per_project(self, driver: _Driver) -> None:
        for project in ("projet-a", "projet-b"):
            graph = _graph(project)
            graph.upsert_mission(Mission(id="MIS-1", title="m", status=MissionState.OPEN, origin="user", created_at="2026-01-01T00:00:00"))
            graph.upsert_task(MissionTask(
                id="T-1", mission_id="MIS-1", title="t", status=TaskState.READY, type=TaskType.IMPLEMENTATION,
                risk_profile=RiskProfile.STANDARD, acceptance=("ok",), created_at="2026-01-01T00:00:00",
            ))
            graph.upsert_fact(KnowledgeFact(id="fact-hash", subject="s", predicate="p", object="o"))

        ids = {(k["project"], s.split("MERGE (")[1].split(":")[1].split(" ")[0]): k["id"]
               for s, k in driver.calls
               if any(f"MERGE ({v}:Grimoire{label}" in s for v, label in (("m", "Mission"), ("t", "Task"), ("f", "Fact")))}
        assert ids[("projet-a", "GrimoireMission")] == "projet-a::MIS-1"
        assert ids[("projet-b", "GrimoireTask")] == "projet-b::T-1"
        assert ids[("projet-a", "GrimoireFact")] == "projet-a::fact-hash"
        task_call = next(k for s, k in driver.calls if "MERGE (t:GrimoireTask" in s)
        assert task_call["mission_id"] == "projet-a::MIS-1"

    def test_without_a_project_ids_are_untouched(self, driver: _Driver) -> None:
        _graph("").upsert_mission(Mission(id="MIS-1", title="m", status=MissionState.OPEN, origin="user", created_at="2026-01-01T00:00:00"))
        assert next(k for s, k in driver.calls if "MERGE (m:GrimoireMission" in s)["id"] == "MIS-1"


class TestMemoryNodesAreKeyedToo:
    """Un second projet qui écrit le même identifiant ne vole ni l'attribution ni le texte."""

    def test_memory_and_vector_object_ids_are_prefixed(self, driver: _Driver) -> None:
        _graph().upsert_memory(MemoryEntry(
            id="m1", text="t", tags=("x",),
            metadata={"weaviate_id": "w1", "projection_kind": "code_symbol", "code_node_id": "a:f"},
        ))
        node = next(k for s, k in driver.calls if "MERGE (m:GrimoireMemory" in s)
        assert node["id"] == "projet-a::m1"
        assert next(k for s, k in driver.calls if "UNWIND $tags" in s)["id"] == "projet-a::m1"
        vector = next((s, k) for s, k in driver.calls if "WeaviateObject" in s and "MERGE (w" in s)
        assert "{id: $prefix + $weaviate_id}" in vector[0] and vector[1]["id"] == "projet-a::m1"
        link = next(k for s, k in driver.calls if "MEMORY_FOR {kind" in s and "CodeNode" in s)
        assert (link["memory_id"], link["target_id"]) == ("projet-a::m1", "projet-a::a:f")

    def test_two_projects_writing_the_same_memory_id_get_two_nodes(self, driver: _Driver) -> None:
        for project in ("projet-a", "projet-b"):
            _graph(project).upsert_memory(MemoryEntry(id="same", text=f"texte de {project}"))
        written = [(k["id"], k["project"], k["text"]) for s, k in driver.calls if "MERGE (m:GrimoireMemory" in s]
        assert written == [("projet-a::same", "projet-a", "texte de projet-a"), ("projet-b::same", "projet-b", "texte de projet-b")]

    def test_fact_diary_delete_and_orphans_use_the_keyed_memory_id(self, driver: _Driver) -> None:
        graph = _graph()
        graph.upsert_fact(KnowledgeFact(id="f", subject="s", predicate="p", object="o", source_memory_id="m1"))
        graph.upsert_diary(DiaryRecord(id="d", agent_name="a", topic="t", entry="e", related_memory_id="m1"))
        graph.delete_memory("m1")
        assert next(k for s, k in driver.calls if "SOURCED_FROM" in s)["source_memory_id"] == "projet-a::m1"
        assert next(k for s, k in driver.calls if "RELATED_TO" in s)["related_memory_id"] == "projet-a::m1"
        assert next(k for s, k in driver.calls if "SET m.deleted = true" in s)["id"] == "projet-a::m1"
        graph.find_orphan_memory_nodes(known_ids=frozenset({"m1"}), collection="c")
        known = next(k for s, k in driver.calls if "NOT m.id IN $known_ids" in s)["known_ids"]
        assert set(known) == {"m1", "projet-a::m1"}  # un nœud antérieur à la clé n'est pas pris pour un orphelin

    def test_dependency_stubs_get_the_project(self, driver: _Driver) -> None:
        graph = _graph()
        graph.upsert_task(MissionTask(
            id="T-1", mission_id="MIS-1", title="t", status=TaskState.READY, type=TaskType.IMPLEMENTATION,
            risk_profile=RiskProfile.STANDARD, acceptance=("ok",), created_at="2026-01-01T00:00:00",
            dependencies=(TaskDependency(kind=DependencyKind.RELATES, target="T-0"),),
        ))
        stub = next((s, k) for s, k in driver.calls if "MERGE (target:GrimoireTask" in s)
        assert "ON CREATE SET target.project = $project" in stub[0] and stub[1]["target"] == "projet-a::T-0"


class _QueryErrorDriver(_Driver):
    def execute_query(self, statement: str, **kwargs: Any) -> Any:
        self.calls.append((statement, kwargs))
        if "MERGE (m:GrimoireMemory" not in statement:
            return ([], None, None)
        raise CypherSyntaxError("Invalid input")


class CypherSyntaxError(Exception):
    """Erreur de requête : pas une panne de connectivité."""


class TestBreakerOnlyOnConnectivity:
    def test_a_query_error_does_not_suspend_valid_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = _QueryErrorDriver()
        _GraphDatabase.driver_instance = bad
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        graph = _graph()
        with pytest.raises(CypherSyntaxError):
            graph.upsert_memory(MemoryEntry(id="m1", text="t"))
        with pytest.raises(CypherSyntaxError):  # le disjoncteur est resté fermé : le pilote est réinterrogé
            graph.upsert_memory(MemoryEntry(id="m2", text="t"))
        assert sum("MERGE (m:GrimoireMemory" in s for s, _ in bad.calls) == 2

    def test_service_unavailable_opens_it_and_the_message_names_the_cause_and_the_remedy(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ServiceUnavailable(Exception):  # noqa: N818 - même nom que l'erreur du pilote
            pass

        class _Down(_Driver):
            def execute_query(self, statement: str, **kwargs: Any) -> Any:
                self.calls.append((statement, kwargs))
                raise ServiceUnavailable("Couldn't connect to x:7687")

        _GraphDatabase.driver_instance = _Down()
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        graph = _graph()
        with pytest.raises(ServiceUnavailable):
            graph.upsert_memory(MemoryEntry(id="m1", text="t"))
        from grimoire.memory.neo4j_graph import GraphUnavailableError

        with pytest.raises(GraphUnavailableError) as err:
            graph.upsert_memory(MemoryEntry(id="m2", text="t"))
        assert "ServiceUnavailable" in str(err.value) and "memory graph sync-memories" in str(err.value)


class _OrphanRows(_Driver):
    def execute_query(self, statement: str, **kwargs: Any) -> Any:
        self.calls.append((statement, kwargs))
        if "NOT m.id IN $known_ids" in statement:
            rows = [
                {"id": "projet-a::gone", "collection": "c"},   # nœud préfixé d'un souvenir disparu du store
                {"id": "legacy-gone", "collection": "c"},      # nœud antérieur à la clé
            ]
            return (rows, None, None)
        return ([{"count": 0}], None, None)


class TestOrphanApiSpeaksStoreIds:
    def test_find_returns_the_store_id_and_keeps_the_node_id_for_the_purge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        driver = _OrphanRows()
        _GraphDatabase.driver_instance = driver
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        found = _graph().find_orphan_memory_nodes(known_ids=frozenset({"kept"}), collection="c")
        assert [(o["id"], o["node_id"]) for o in found] == [("gone", "projet-a::gone"), ("legacy-gone", "legacy-gone")]

    def test_prune_reports_store_ids_and_purges_node_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.memory.projections import prune_orphan_memories

        driver = _OrphanRows()
        _GraphDatabase.driver_instance = driver
        monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=_GraphDatabase))
        report = prune_orphan_memories(_graph(), [MemoryEntry(id="kept", text="t")], collection="c", apply=True)
        assert report["ids"] == ["gone", "legacy-gone"] and report["purged"] == 2
        purge = next(k for s, k in driver.calls if "DETACH DELETE" in s)
        assert purge["ids"] == ["projet-a::gone", "legacy-gone"]
