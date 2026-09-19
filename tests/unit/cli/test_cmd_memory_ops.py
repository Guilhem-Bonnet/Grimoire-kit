"""Tests for ``grimoire memory up`` and ``grimoire memory graph purge-orphans`` (#527).

Split from ``test_cmd_memory.py``: these two commands own the vocabulary
alignment and the bounded Neo4j orphan purge that #527 asked for, and neither
had any CLI-level coverage before this pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import MemoryEntry

runner = CliRunner()


def _write_config(root: Path, body: str = '  backend: "auto"\n  collection_prefix: "grimoire"\n') -> Path:
    path = root / "project-context.yaml"
    path.write_text('project:\n  name: "Mon Super Projet"\n\nmemory:\n' + body, encoding="utf-8")
    return path


# ── grimoire memory up ────────────────────────────────────────────────────────


class TestMemoryUpVocabulary:
    def test_rejects_an_unknown_profile_listing_canonical_names(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["memory", "up", "--profile", "turbo"])
        assert result.exit_code == 1
        assert "lexical" in result.output
        assert "standard" in result.output
        assert "complet" in result.output

    def test_accepts_the_legacy_vector_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["-o", "json", "memory", "up", "--profile", "vector"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # Le nom canonique est ce que le plan rapporte, jamais l'alias saisi.
        assert data["profile"] == "standard"

    def test_accepts_the_legacy_full_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["-o", "json", "memory", "up", "--profile", "full"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["profile"] == "complet"

    def test_plan_reports_layer_profile_and_retrieval_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["-o", "json", "memory", "up", "--profile", "lexical"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["config"]["layer_profile"] == "lexical"
        assert data["config"]["retrieval_mode"] == "lexical"


# ── grimoire memory up --start (issue Grimoire-kit#616, PR2) ──────────────────


class TestMemoryUpStart:
    """``--start`` is the only path that may launch a container — and only on
    this explicit flag. Real Docker is never invoked in this suite."""

    def test_without_the_flag_the_stack_helper_is_never_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        with patch("grimoire.tools.memory_setup.start_memory_stack") as mock_start:
            result = runner.invoke(app, ["-o", "json", "memory", "up", "--profile", "complet"])
        assert result.exit_code == 0, result.output
        mock_start.assert_not_called()

    def test_the_flag_calls_the_stack_helper_before_planning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        with patch(
            "grimoire.tools.memory_setup.start_memory_stack",
            return_value=["Weaviate : démarré"],
        ) as mock_start:
            result = runner.invoke(
                app, ["-o", "json", "memory", "up", "--profile", "complet", "--start"],
            )
        assert result.exit_code == 0, result.output
        mock_start.assert_called_once()
        data = json.loads(result.output)
        assert data["started"] == ["Weaviate : démarré"]

    def test_start_is_a_no_op_for_standard_and_never_touches_docker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)

        def _fail_if_called(*a: object, **k: object) -> None:
            raise AssertionError("docker must not be invoked for `standard`")

        monkeypatch.setattr("grimoire.tools.memory_setup.subprocess.run", _fail_if_called)
        result = runner.invoke(
            app, ["-o", "json", "memory", "up", "--profile", "standard", "--start"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["started"] == []


# ── grimoire memory graph purge-orphans ───────────────────────────────────────


def _entry(entry_id: str) -> MemoryEntry:
    return MemoryEntry(id=entry_id, text="x", user_id="guilhem", tags=(), metadata={})


@pytest.fixture
def purge_context():
    """Patch the manager/config/graph trio ``purge-orphans`` reads."""
    mgr = MagicMock()
    mgr.get_all.return_value = [_entry("kept-1")]

    cfg = GrimoireConfig.from_dict({
        "project": {"name": "test", "type": "generic", "stack": []},
        "memory": {
            "backend": "weaviate-server",
            "weaviate_url": "http://localhost:8080",
            "weaviate_collection": "ProjectMemory",
            "neo4j_uri": "bolt://localhost:7687",
        },
        "agents": {"archetype": "minimal"},
    })

    graph = MagicMock()
    graph.find_orphan_memory_nodes.return_value = [{"id": "orphan-1", "collection": "GrimoireMemory"}]
    graph.purge_memory_nodes.return_value = 1

    with (
        patch("grimoire.cli.cmd_memory_ops._load_manager_context", return_value=(mgr, cfg, Path.cwd())),
        patch("grimoire.cli.cmd_memory_ops._load_neo4j_graph", return_value=graph),
    ):
        yield mgr, graph


class TestPurgeOrphans:
    def test_dry_run_by_default_never_purges(self, purge_context) -> None:
        _mgr, graph = purge_context
        result = runner.invoke(app, ["-o", "json", "memory", "graph", "purge-orphans"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["applied"] is False
        assert data["candidates"] == 1
        assert data["purged"] == 0
        graph.purge_memory_nodes.assert_not_called()

    def test_apply_actually_purges(self, purge_context) -> None:
        _mgr, graph = purge_context
        result = runner.invoke(app, ["-o", "json", "memory", "graph", "purge-orphans", "--apply"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["applied"] is True
        assert data["purged"] == 1
        graph.purge_memory_nodes.assert_called_once_with(["orphan-1"])

    def test_scope_is_the_configured_collection(self, purge_context) -> None:
        _mgr, graph = purge_context
        runner.invoke(app, ["memory", "graph", "purge-orphans"])
        _, kwargs = graph.find_orphan_memory_nodes.call_args
        assert kwargs["collection"] == "ProjectMemory"

    def test_closes_the_graph_connection(self, purge_context) -> None:
        _mgr, graph = purge_context
        runner.invoke(app, ["memory", "graph", "purge-orphans"])
        graph.close.assert_called_once()


# ── grimoire memory doctor (issue Grimoire-kit#616/#619) ─────────────────────


@pytest.fixture
def doctor_context(tmp_path: Path):
    """Patch config/manager/graph the way ``memory doctor`` reads them."""
    from grimoire.memory.backends.base import BackendStatus

    mgr = MagicMock()
    mgr.get_all.return_value = [_entry("kept-1")]
    mgr.health_check.return_value = BackendStatus(backend="weaviate-server", healthy=True, entries=1)

    cfg = GrimoireConfig.from_dict({
        "project": {"name": "test", "type": "generic", "stack": []},
        "memory": {
            "backend": "weaviate-server",
            "weaviate_url": "http://localhost:8080",
            "weaviate_collection": "ProjectMemory",
            "neo4j_uri": "bolt://localhost:7687",
            "knowledge_graph": "neo4j",
            "layer_profile": "graphe",
        },
        "agents": {"archetype": "minimal"},
    })

    graph = MagicMock()
    graph.find_orphan_memory_nodes.return_value = [{"id": "orphan-1", "collection": "GrimoireMemory"}]
    graph.purge_memory_nodes.return_value = 1

    with (
        patch("grimoire.cli.cmd_memory_ops._load_config_context", return_value=(cfg, tmp_path)),
        patch("grimoire.cli.cmd_memory_ops.MemoryManager.from_config", return_value=mgr),
        patch("grimoire.cli.cmd_memory_ops._load_neo4j_graph", return_value=graph),
        patch("grimoire.tools.memory_setup._tcp_reachable", return_value=True),
    ):
        yield mgr, graph


class TestMemoryDoctor:
    def test_dry_run_reports_orphans_without_purging(self, doctor_context) -> None:
        _mgr, graph = doctor_context
        result = runner.invoke(app, ["-o", "json", "memory", "doctor"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["applied"] is False
        assert data["graph_drift"]["candidates"] == 1
        assert data["graph_drift"]["purged"] == 0
        graph.purge_memory_nodes.assert_not_called()

    def test_apply_purges_reusing_the_same_probe_as_purge_orphans(self, doctor_context) -> None:
        _mgr, graph = doctor_context
        result = runner.invoke(app, ["-o", "json", "memory", "doctor", "--apply"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["applied"] is True
        assert data["graph_drift"]["purged"] == 1
        graph.purge_memory_nodes.assert_called_once_with(["orphan-1"])

    def test_report_names_the_started_stack(self, doctor_context) -> None:
        """Every configured service is reachable (mocked) — nothing to flag."""
        result = runner.invoke(app, ["-o", "json", "memory", "doctor"])
        data = json.loads(result.output)
        assert data["not_started"] == []

    def test_not_started_stack_is_reported_with_the_start_command(
        self, doctor_context, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("grimoire.tools.memory_setup._tcp_reachable", lambda *a, **k: False)
        result = runner.invoke(app, ["memory", "doctor"])
        assert result.exit_code == 0, result.output
        assert "Pile non démarrée" in result.output
        assert "grimoire memory up --profile graphe --start --apply" in result.output

    def test_never_fails_when_the_backend_is_unavailable(self, tmp_path: Path) -> None:
        from grimoire.core.exceptions import GrimoireMemoryError

        cfg = GrimoireConfig.from_dict({
            "project": {"name": "test", "type": "generic", "stack": []},
            "memory": {"backend": "weaviate-server", "weaviate_url": "http://localhost:8080"},
            "agents": {"archetype": "minimal"},
        })
        with (
            patch("grimoire.cli.cmd_memory_ops._load_config_context", return_value=(cfg, tmp_path)),
            patch(
                "grimoire.cli.cmd_memory_ops.MemoryManager.from_config",
                side_effect=GrimoireMemoryError("backend down"),
            ),
        ):
            result = runner.invoke(app, ["-o", "json", "memory", "doctor"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["healthy"] is False
        assert "backend down" in data["backend_error"]

    def test_never_fails_when_no_graph_is_configured(self, tmp_path: Path) -> None:
        mgr = MagicMock()
        mgr.get_all.return_value = []
        from grimoire.memory.backends.base import BackendStatus

        mgr.health_check.return_value = BackendStatus(backend="lexical", healthy=True, entries=0)
        cfg = GrimoireConfig.from_dict({
            "project": {"name": "test", "type": "generic", "stack": []},
            "memory": {"backend": "lexical"},
            "agents": {"archetype": "minimal"},
        })
        with (
            patch("grimoire.cli.cmd_memory_ops._load_config_context", return_value=(cfg, tmp_path)),
            patch("grimoire.cli.cmd_memory_ops.MemoryManager.from_config", return_value=mgr),
        ):
            result = runner.invoke(app, ["-o", "json", "memory", "doctor"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["graph_drift"] is None
