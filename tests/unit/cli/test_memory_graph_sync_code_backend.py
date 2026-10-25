"""R10 — ``memory graph sync-code --backend`` : le sélecteur instancie le backend demandé (doubles, sans Neo4j)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig

runner = CliRunner()


def _cfg() -> GrimoireConfig:
    return GrimoireConfig.from_dict({
        "project": {"name": "projet-a", "type": "generic", "stack": []},
        "memory": {
            "backend": "local", "neo4j_uri": "bolt://x:7687", "neo4j_password_env": "GK_R10_PW",
            "neo4j_user": "neo4j", "neo4j_database": "bd",
        },
        "agents": {"archetype": "minimal"},
    })


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("GK_R10_PW", "secret")
    return tmp_path


class _FakeCodegraphBackend:
    instances: list[_FakeCodegraphBackend] = []  # noqa: RUF012 - double de test

    def __init__(self, uri: str, *, auth: tuple[str, str] | None = None, database: str = "neo4j") -> None:
        self.args = (uri, auth, database)
        self.pushed: list[Any] = []
        self.closed = False
        _FakeCodegraphBackend.instances.append(self)

    def push_graph(self, graph: Any) -> dict[str, int]:
        self.pushed.append(graph)
        return {"nodes": len(list(graph.nodes)), "edges": 7}

    def close(self) -> None:
        self.closed = True


def _run(project: Path, *args: str) -> Any:
    with patch("grimoire.cli.cmd_memory_projections._load_config_context", return_value=(_cfg(), project)):
        return runner.invoke(app, ["-o", "json", "memory", "graph", "sync-code", "--paths", "src", *args])


def test_backend_flag_instantiates_the_codegraph_backend(project: Path) -> None:
    _FakeCodegraphBackend.instances = []
    with (
        patch("grimoire.codegraph.backends.neo4j.Neo4jCodeGraphBackend", _FakeCodegraphBackend),
        patch("grimoire.cli.cmd_memory_projections._load_neo4j_graph", side_effect=AssertionError("graphe mémoire non voulu")),
    ):
        result = _run(project, "--backend", "codegraph-neo4j")

    assert result.exit_code == 0, result.output
    (backend,) = _FakeCodegraphBackend.instances
    assert backend.args == ("bolt://x:7687", ("neo4j", "secret"), "bd")
    assert len(backend.pushed) == 1 and backend.closed
    stats = json.loads(result.output)
    assert stats["code_edges"] == 7 and stats["files"] == 1


def test_default_backend_is_still_the_memory_graph(project: Path) -> None:
    graph = MagicMock()
    with (
        patch("grimoire.cli.cmd_memory_projections._load_neo4j_graph", return_value=graph) as loader,
        patch(
            "grimoire.memory.projections.sync_code_graph_projection",
            return_value={"files": 1, "code_nodes": 2, "code_edges": 3, "test_nodes": 0},
        ) as sync,
    ):
        result = _run(project)

    assert result.exit_code == 0, result.output
    loader.assert_called_once()
    assert loader.call_args.kwargs == {"ensure_schema": True}
    sync.assert_called_once()
    graph.close.assert_called_once()


def test_unknown_backend_is_refused(project: Path) -> None:
    result = _run(project, "--backend", "memgraph")
    assert result.exit_code == 2
    assert "memgraph" in result.output


def test_codegraph_backend_without_password_is_a_clear_error(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GK_R10_PW")
    result = _run(project, "--backend", "codegraph-neo4j")
    assert result.exit_code == 2
    assert "GK_R10_PW" in result.output


class TestBackfillProjectCli:
    """P15b — le rattrapage est un aperçu tant que ``--apply`` n'est pas passé."""

    def _invoke(self, project: Path, *args: str) -> tuple[Any, MagicMock]:
        graph = MagicMock()
        graph.backfill_project.return_value = {"memories_by_collection": 2, "code_nodes": 5}
        with (
            patch("grimoire.cli.cmd_memory_projections._load_config_context", return_value=(_cfg(), project)),
            patch("grimoire.cli.cmd_memory_projections._load_neo4j_graph", return_value=graph) as loader,
        ):
            result = runner.invoke(app, ["-o", "json", "memory", "graph", "backfill-project", *args])
        self.loader = loader
        return result, graph

    def test_dry_run_by_default_and_schema_untouched(self, project: Path) -> None:
        result, graph = self._invoke(project, "--paths", "src,tests")
        assert result.exit_code == 0, result.output
        graph.backfill_project.assert_called_once_with(
            collections=["grimoire"], code_prefixes=["src/", "tests/"], apply=False,
        )
        assert self.loader.call_args.kwargs == {"ensure_schema": False}
        assert json.loads(result.output) == {
            "project": "projet-a", "apply": False, "memories_by_collection": 2, "code_nodes": 5,
        }

    def test_apply_is_explicit(self, project: Path) -> None:
        result, graph = self._invoke(project, "--apply", "--collection", "col-x")
        assert result.exit_code == 0, result.output
        assert graph.backfill_project.call_args.kwargs["apply"] is True
        assert graph.backfill_project.call_args.kwargs["collections"] == ["col-x"]
        assert self.loader.call_args.kwargs == {"ensure_schema": True}
