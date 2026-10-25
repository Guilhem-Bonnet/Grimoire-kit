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
from grimoire.missions.schemas import Mission, MissionState, MissionTask, RiskProfile, TaskState, TaskType


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
    def driver(cls, uri: str, *, auth: tuple[str, str]) -> _Driver:
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

    def test_stats_report_unattributed_nodes_only_with_a_project(self, driver: _Driver) -> None:
        assert "unattributed_nodes" in _graph().stats()
        assert "unattributed_nodes" not in _graph(project="").stats()

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


class TestBackfill:
    def test_preview_runs_counts_only(self, driver: _Driver) -> None:
        report = _graph().backfill_project(collections=["col"], code_prefixes=["src/"], apply=False)
        assert set(report) == {
            "memories_by_collection", "memories_by_metadata", "code_nodes", "code_id_conflicts", "ghosts_external",
        }
        for statement, _ in driver.calls:
            assert "SET " not in statement, statement

    def test_apply_attributes_in_the_documented_order(self, driver: _Driver) -> None:
        head = "MATCH (n:GrimoireMemory) WHERE n.project IS NULL AND "
        driver.scalars = {
            _first_clause(head + "n.weaviate_collection IN $collections RETURN count(n) AS count"): 3,
            _first_clause(
                head + "n.metadata_json CONTAINS $needle AND NOT coalesce(n.weaviate_collection, '') IN $collections "
                "RETURN count(n) AS count"
            ): 2,
        }
        report = _graph().backfill_project(collections=["col"], code_prefixes=["src/"], apply=True)
        assert report["memories_by_collection"] == 3
        statements = [" ".join(s.split()) for s, _ in driver.calls if "SET " in s]
        assert statements[0].endswith("SET n.project = $project")
        assert "weaviate_collection IN $collections" in statements[0]
        assert "metadata_json CONTAINS $needle" in statements[1]
        assert any("n.id = $prefix + n.id" in s for s in statements)
        assert any("n.external = true" in s for s in statements)
        assert all("n.project IS NULL" in s for s in statements)  # jamais un nœud déjà attribué

    def test_needle_matches_the_json_written_by_upsert_memory(self, driver: _Driver) -> None:
        _graph("Mon Projet").backfill_project(collections=[], code_prefixes=[], apply=False)
        needle = next(k["needle"] for _, k in driver.calls if "needle" in k)
        assert needle == '"project_name": "Mon Projet"'

    def test_requires_a_project_key(self, driver: _Driver) -> None:
        with pytest.raises(ValueError, match="project"):
            _graph(project="").backfill_project(collections=[], code_prefixes=[])
