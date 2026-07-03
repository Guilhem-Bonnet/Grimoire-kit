"""Tests for ``grimoire memory`` CLI sub-commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import BackendStatus, MemoryEntry

runner = CliRunner()

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_entry(
    id: str = "abc123",
    text: str = "Remember this fact",
    user_id: str = "global",
    tags: tuple[str, ...] = (),
    score: float = 0.0,
    created_at: str = "2026-03-15T10:00:00",
    metadata: dict[str, object] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=id, text=text, user_id=user_id, tags=tags,
        metadata=metadata or {}, score=score, created_at=created_at,
    )


@pytest.fixture
def mock_manager():
    """Patch _load_manager to return a MagicMock MemoryManager."""
    mgr = MagicMock()
    mgr.health_check.return_value = BackendStatus(
        backend="local", healthy=True, entries=42,
        detail={"path": "/tmp/test.json"},
    )
    mgr.count.return_value = 42
    mgr.facts_stats.return_value = {"facts": 3, "active_facts": 2, "expired_facts": 1}
    mgr.diary_stats.return_value = {"diary_entries": 4, "agents": 2}
    mgr.search_taxonomy.return_value = [
        _make_entry(
            id="e1",
            text="Hello world",
            score=0.95,
            tags=("greet",),
            metadata={"wing": "project-test", "hall": "hall_facts", "room": "greeting"},
        ),
        _make_entry(
            id="e2",
            text="Second memory",
            score=0.80,
            metadata={"wing": "project-test", "hall": "hall_events", "room": "follow-up"},
        ),
    ]
    mgr.get_all_filtered.return_value = [
        _make_entry(
            id="e1",
            text="Hello world",
            tags=("greet",),
            metadata={"wing": "project-test", "hall": "hall_facts", "room": "greeting"},
        ),
        _make_entry(
            id="e2",
            text="Second memory",
            metadata={"wing": "project-test", "hall": "hall_events", "room": "follow-up"},
        ),
    ]
    mgr.get_all.return_value = list(mgr.get_all_filtered.return_value)
    mgr.recall.return_value = _make_entry(id="abc123", text="Important")
    mgr.delete.return_value = True
    mgr.consolidate.return_value = 5
    mgr.store_many.return_value = [_make_entry(id="new1"), _make_entry(id="new2")]
    mgr.backend = object()

    cfg = GrimoireConfig.from_dict({"project": {"name": "test"}, "memory": {"backend": "local"}})
    with (
        patch("grimoire.cli.cmd_memory._load_manager", return_value=mgr),
        patch("grimoire.cli.cmd_memory._load_manager_context", return_value=(mgr, cfg, Path())),
        patch("grimoire.cli.cmd_memory._load_config_context", return_value=(cfg, Path())),
    ):
        yield mgr


# ── grimoire memory status ────────────────────────────────────────────────────


class TestMemoryStatus:
    def test_status_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "local" in result.output
        assert "42" in result.output

    def test_status_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["backend"] == "local"
        assert data["healthy"] is True
        assert data["entries"] == 42

    def test_status_unhealthy(self, mock_manager: MagicMock) -> None:
        mock_manager.health_check.return_value = BackendStatus(
            backend="qdrant", healthy=False, entries=0,
        )
        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "qdrant" in result.output


class TestMemoryMigrate:
    def test_migrate_plan_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "migrate", "plan"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["target_vector_backend"] == "weaviate-server"
        assert data["target_graph_backend"] == "neo4j"
        assert data["entries"] is None

    def test_export_bundle_allows_public_fallback(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        bundle = tmp_path / "bundle"

        result = runner.invoke(
            app,
            [
                "memory",
                "migrate",
                "export-bundle",
                "--bundle",
                str(bundle),
                "--allow-missing-vectors",
            ],
        )

        assert result.exit_code == 0
        assert (bundle / "manifest.json").is_file()
        assert (bundle / "memories.jsonl").is_file()
        manifest = json.loads((bundle / "manifest.json").read_text())
        assert manifest["record_count"] == 2
        assert manifest["vector_lossless"] is False

    def test_export_bundle_requires_vectors_by_default(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        result = runner.invoke(
            app,
            [
                "memory",
                "migrate",
                "export-bundle",
                "--bundle",
                str(tmp_path / "bundle"),
            ],
        )

        assert result.exit_code == 2
        assert "vector-lossless" in result.output

    def test_import_weaviate_dry_run(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        with patch(
            "grimoire.memory.migration.import_weaviate_bundle",
            return_value={
                "bundle": str(tmp_path / "bundle"),
                "collection": "GrimoireMemory",
                "objects": 2,
                "imported": 0,
                "dry_run": True,
            },
        ) as importer:
            result = runner.invoke(
                app,
                [
                    "memory",
                    "migrate",
                    "import-weaviate",
                    "--bundle",
                    str(tmp_path / "bundle"),
                    "--weaviate-url",
                    "http://localhost:8080",
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0
        assert "validated" in result.output
        importer.assert_called_once()

    def test_import_neo4j_dry_run(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps({
                "schema_version": "grimoire.memory_migration.v1",
                "files": {"neo4j_cypher": "neo4j-import.cypher"},
            }),
            encoding="utf-8",
        )
        (bundle / "neo4j-import.cypher").write_text("MERGE (m:GrimoireMemory {id: \"mem-1\"});\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "memory",
                "migrate",
                "import-neo4j",
                "--bundle",
                str(bundle),
                "--neo4j-uri",
                "neo4j://localhost:7687",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "validated" in result.output

    def test_verify_migration_success(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        with patch(
            "grimoire.memory.migration.verify_migration_bundle",
            return_value={
                "ok": True,
                "issues": [],
                "bundle": {"record_count": 2, "vector_count": 2},
                "weaviate": {"count": 2, "collection": "GrimoireMemory"},
                "neo4j": {"count": 2, "tag_edges": 1},
            },
        ) as verifier:
            result = runner.invoke(
                app,
                [
                    "memory",
                    "migrate",
                    "verify",
                    "--bundle",
                    str(tmp_path / "bundle"),
                    "--weaviate-url",
                    "http://localhost:8080",
                    "--skip-neo4j",
                ],
            )

        assert result.exit_code == 0
        assert "Migration verification" in result.output
        verifier.assert_called_once()

    def test_verify_migration_failure_exits_nonzero(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        with patch(
            "grimoire.memory.migration.verify_migration_bundle",
            return_value={
                "ok": False,
                "issues": ["Weaviate is missing 1 source ids"],
                "bundle": {"record_count": 2, "vector_count": 2},
                "weaviate": {"count": 1, "collection": "GrimoireMemory"},
                "neo4j": {"skipped": True},
            },
        ):
            result = runner.invoke(
                app,
                [
                    "memory",
                    "migrate",
                    "verify",
                    "--bundle",
                    str(tmp_path / "bundle"),
                    "--weaviate-url",
                    "http://localhost:8080",
                    "--skip-neo4j",
                ],
            )

        assert result.exit_code == 1
        assert "Weaviate is missing" in result.output


class TestMemoryGraph:
    def test_sync_code(self, mock_manager: MagicMock) -> None:
        graph = MagicMock()
        graph.close.return_value = None
        with (
            patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
            patch(
                "grimoire.memory.projections.sync_code_graph_projection",
                return_value={"files": 2, "code_nodes": 10, "code_edges": 4, "test_nodes": 1},
            ) as sync,
        ):
            result = runner.invoke(app, ["memory", "graph", "sync-code", "--paths", "src,tests"])

        assert result.exit_code == 0
        assert "Code graph synced" in result.output
        sync.assert_called_once()
        assert sync.call_args.kwargs["paths"] == [Path("src"), Path("tests")]
        graph.close.assert_called_once()

    def test_sync_tasks_json(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        graph = MagicMock()
        graph.close.return_value = None
        expected = {
            "missions": 1,
            "tasks": 2,
            "ledger_events": 3,
            "incidents": 0,
            "evidence_packs": 1,
            "verdicts": 1,
        }
        with (
            patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
            patch("grimoire.memory.projections.sync_task_memory_projection", return_value=expected),
        ):
            result = runner.invoke(
                app,
                [
                    "-o",
                    "json",
                    "memory",
                    "graph",
                    "sync-tasks",
                    "--ledger",
                    str(tmp_path / "ledger"),
                    "--evidence",
                    str(tmp_path / "evidence"),
                ],
            )

        assert result.exit_code == 0
        assert json.loads(result.output) == expected
        graph.close.assert_called_once()

    def test_verify_fails_nonzero(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        graph = MagicMock()
        graph.close.return_value = None
        stats = {
            "ok": False,
            "expected": {"tasks": 1},
            "actual": {"tasks": 0},
            "issues": ["Neo4j tasks=0 but local source expects at least 1"],
        }
        with (
            patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
            patch("grimoire.memory.projections.graph_projection_verify", return_value=stats),
        ):
            result = runner.invoke(
                app,
                [
                    "memory",
                    "graph",
                    "verify",
                    "--ledger",
                    str(tmp_path / "ledger"),
                    "--evidence",
                    str(tmp_path / "evidence"),
                ],
            )

        assert result.exit_code == 1
        assert "Neo4j tasks=0" in result.output
        graph.close.assert_called_once()


class TestMemoryVector:
    def test_sync_code_json(self, mock_manager: MagicMock) -> None:
        mock_manager.memory_graph = MagicMock()
        with (
            patch(
                "grimoire.memory.projections.sync_code_graph_projection",
                return_value={"files": 1, "code_nodes": 2, "code_edges": 1, "test_nodes": 0},
            ) as graph_sync,
            patch(
                "grimoire.memory.projections.sync_code_vector_projection",
                return_value={"vector_entries": 1, "code_files": 1},
            ) as vector_sync,
        ):
            result = runner.invoke(app, ["-o", "json", "memory", "vector", "sync-code", "--paths", "src"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["vector"]["vector_entries"] == 1
        graph_sync.assert_called_once()
        vector_sync.assert_called_once()

    def test_sync_tasks_without_graph(self, mock_manager: MagicMock) -> None:
        mock_manager.memory_graph = None
        with patch(
            "grimoire.memory.projections.sync_task_vector_projection",
            return_value={"vector_entries": 0},
        ) as vector_sync:
            result = runner.invoke(app, ["memory", "vector", "sync-tasks"])

        assert result.exit_code == 0
        assert "Task vector projection synced" in result.output
        vector_sync.assert_called_once()

    def test_verify_fails_nonzero(self, mock_manager: MagicMock) -> None:
        with (
            patch("grimoire.memory.projections.build_code_vector_entries", return_value=[]),
            patch("grimoire.memory.projections.build_task_vector_entries", return_value=[]),
            patch(
                "grimoire.memory.projections.vector_projection_verify",
                return_value={
                    "ok": False,
                    "expected": 1,
                    "actual": 0,
                    "missing": ["code:src/app.py"],
                    "stale": [],
                    "extra": [],
                    "issues": ["Missing vector projection entries: 1"],
                },
            ),
        ):
            result = runner.invoke(app, ["memory", "vector", "verify"])

        assert result.exit_code == 1
        assert "Missing vector projection" in result.output


class TestMemoryGate:
    def test_gate_success_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_manager: MagicMock) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        cfg = GrimoireConfig.from_dict({
            "project": {"name": "test"},
            "memory": {
                "backend": "weaviate-server",
                "weaviate_url": "http://localhost:8080",
                "weaviate_collection": "GrimoireKitMemory",
                "neo4j_uri": "bolt://localhost:7687",
                "neo4j_password_env": "GRIMOIRE_NEO4J_PASSWORD",
            },
        })
        monkeypatch.setenv("GRIMOIRE_NEO4J_PASSWORD", "secret")
        graph = MagicMock()
        graph.close.return_value = None

        with (
            patch("grimoire.cli.cmd_memory._load_config_context", return_value=(cfg, tmp_path)),
            patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
            patch(
                "grimoire.memory.migration.verify_migration_bundle",
                return_value={
                    "ok": True,
                    "issues": [],
                    "bundle": {"record_count": 2, "vector_count": 2},
                    "weaviate": {"count": 2, "collection": "GrimoireKitMemory"},
                    "neo4j": {"count": 2, "tag_edges": 1},
                },
            ),
            patch(
                "grimoire.memory.projections.sync_code_graph_projection",
                return_value={"files": 1, "code_nodes": 2, "code_edges": 1, "test_nodes": 0},
            ),
            patch(
                "grimoire.memory.projections.sync_task_memory_projection",
                return_value={
                    "missions": 0,
                    "tasks": 0,
                    "ledger_events": 0,
                    "incidents": 0,
                    "evidence_packs": 0,
                    "verdicts": 0,
                },
            ),
            patch(
                "grimoire.memory.projections.graph_projection_verify",
                return_value={
                    "ok": True,
                    "expected": {"code_nodes": 2, "code_edges": 1},
                    "actual": {"code_nodes": 2, "code_edges": 1},
                    "issues": [],
                },
            ),
        ):
            result = runner.invoke(app, ["-o", "json", "memory", "gate", "--bundle", str(bundle), "--skip-vectors"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["graph_sync"]["code"]["code_nodes"] == 2
        graph.close.assert_called_once()

    def test_gate_graph_failure_exits_nonzero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = GrimoireConfig.from_dict({
            "project": {"name": "test"},
            "memory": {
                "backend": "weaviate-server",
                "neo4j_uri": "bolt://localhost:7687",
                "neo4j_password_env": "GRIMOIRE_NEO4J_PASSWORD",
            },
        })
        monkeypatch.setenv("GRIMOIRE_NEO4J_PASSWORD", "secret")
        graph = MagicMock()
        graph.close.return_value = None
        with (
            patch("grimoire.cli.cmd_memory._load_config_context", return_value=(cfg, tmp_path)),
            patch("grimoire.cli.cmd_memory._load_neo4j_graph", return_value=graph),
            patch(
                "grimoire.memory.projections.graph_projection_verify",
                return_value={
                    "ok": False,
                    "expected": {"tasks": 1},
                    "actual": {"tasks": 0},
                    "issues": ["Neo4j tasks=0 but local source expects at least 1"],
                },
            ),
        ):
            result = runner.invoke(app, ["memory", "gate", "--skip-migration", "--no-sync", "--skip-vectors"])

        assert result.exit_code == 1
        assert "Neo4j tasks=0" in result.output

    def test_gate_soft_reports_missing_infra_without_failing(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "gate", "--soft", "--skip-migration", "--skip-vectors"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "memory.neo4j_uri" in data["issues"][0]

    def test_gate_vector_failure_exits_nonzero(self, tmp_path: Path, mock_manager: MagicMock) -> None:
        cfg = GrimoireConfig.from_dict({"project": {"name": "test"}, "memory": {"backend": "local"}})
        with (
            patch("grimoire.cli.cmd_memory._load_config_context", return_value=(cfg, tmp_path)),
            patch("grimoire.cli.cmd_memory._load_manager_context", return_value=(mock_manager, cfg, tmp_path)),
            patch("grimoire.memory.projections.build_code_vector_entries", return_value=[]),
            patch("grimoire.memory.projections.build_task_vector_entries", return_value=[]),
            patch(
                "grimoire.memory.projections.vector_projection_verify",
                return_value={
                    "ok": False,
                    "expected": 1,
                    "actual": 0,
                    "missing": ["code:src/app.py"],
                    "stale": [],
                    "extra": [],
                    "issues": ["Missing vector projection entries: 1"],
                },
            ),
        ):
            result = runner.invoke(app, ["memory", "gate", "--skip-migration", "--skip-graph"])

        assert result.exit_code == 1
        assert "Missing vector projection entries" in result.output


# ── grimoire memory search ────────────────────────────────────────────────────


class TestMemorySearch:
    def test_search_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello"])
        assert result.exit_code == 0
        assert "Hello world" in result.output
        mock_manager.search_taxonomy.assert_called_once_with(
            "hello", user_id="", limit=10, wing="", hall="", room=""
        )

    def test_search_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "search", "hello"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["text"] == "Hello world"

    def test_search_with_limit(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello", "-n", "3"])
        assert result.exit_code == 0
        mock_manager.search_taxonomy.assert_called_once_with(
            "hello", user_id="", limit=3, wing="", hall="", room=""
        )

    def test_search_with_user(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello", "-u", "bob"])
        assert result.exit_code == 0
        mock_manager.search_taxonomy.assert_called_once_with(
            "hello", user_id="bob", limit=10, wing="", hall="", room=""
        )

    def test_search_with_palace_filters(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello", "--wing", "project-test", "--room", "greeting"])
        assert result.exit_code == 0
        mock_manager.search_taxonomy.assert_called_once_with(
            "hello", user_id="", limit=10, wing="project-test", hall="", room="greeting"
        )

    def test_search_no_results(self, mock_manager: MagicMock) -> None:
        mock_manager.search_taxonomy.return_value = []
        result = runner.invoke(app, ["memory", "search", "nonexist"])
        assert result.exit_code == 0
        assert "No memories matching" in result.output


# ── grimoire memory list ──────────────────────────────────────────────────────


class TestMemoryList:
    def test_list_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "Hello world" in result.output

    def test_list_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 2
        assert data["offset"] == 0
        assert len(data["entries"]) == 2

    def test_list_with_pagination(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "list", "--offset", "10", "-n", "5"])
        assert result.exit_code == 0
        mock_manager.get_all_filtered.assert_any_call(
            user_id="", offset=10, limit=5, wing="", hall="", room=""
        )

    def test_list_empty(self, mock_manager: MagicMock) -> None:
        mock_manager.get_all_filtered.return_value = []
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "No memories stored" in result.output


# ── grimoire memory export ────────────────────────────────────────────────────


class TestMemoryExport:
    def test_export_stdout(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "export"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == 1
        assert data["count"] == 2
        assert len(data["entries"]) == 2

    def test_export_to_file(self, mock_manager: MagicMock, tmp_path: Path) -> None:
        out = tmp_path / "export.json"
        result = runner.invoke(app, ["memory", "export", "-f", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["count"] == 2

    def test_export_with_user_filter(self, mock_manager: MagicMock) -> None:
        runner.invoke(app, ["memory", "export", "-u", "alice"])
        mock_manager.get_all.assert_called_once_with(user_id="alice")


# ── grimoire memory import ────────────────────────────────────────────────────


class TestMemoryImport:
    def _write_export(self, path: Path, entries: list[dict]) -> None:
        data = {"version": 1, "count": len(entries), "entries": entries}
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_import_success(self, mock_manager: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "import.json"
        self._write_export(f, [{"text": "A"}, {"text": "B"}])
        result = runner.invoke(app, ["memory", "import", str(f)])
        assert result.exit_code == 0
        assert "Imported 2" in result.output
        mock_manager.store_many.assert_called_once()

    def test_import_json_output(self, mock_manager: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "import.json"
        self._write_export(f, [{"text": "A"}])
        result = runner.invoke(app, ["-o", "json", "memory", "import", str(f)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["imported"] == 2  # mock returns 2

    def test_import_dry_run(self, mock_manager: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "import.json"
        self._write_export(f, [{"text": "A"}, {"text": "B"}, {"text": "C"}])
        result = runner.invoke(app, ["memory", "import", str(f), "--dry-run"])
        assert result.exit_code == 0
        assert "3 entries" in result.output
        mock_manager.store_many.assert_not_called()

    def test_import_invalid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        with patch("grimoire.cli.cmd_memory._load_manager"):
            result = runner.invoke(app, ["memory", "import", str(f)])
        assert result.exit_code == 1

    def test_import_missing_text_field(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"entries": [{"no_text": True}]}), encoding="utf-8")
        with patch("grimoire.cli.cmd_memory._load_manager"):
            result = runner.invoke(app, ["memory", "import", str(f)])
        assert result.exit_code == 1

    def test_import_missing_entries_key(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"no_entries": []}), encoding="utf-8")
        with patch("grimoire.cli.cmd_memory._load_manager"):
            result = runner.invoke(app, ["memory", "import", str(f)])
        assert result.exit_code == 1


# ── grimoire memory gc ────────────────────────────────────────────────────────


class TestMemoryGc:
    def test_gc_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "gc"])
        assert result.exit_code == 0
        assert "Consolidated 5" in result.output

    def test_gc_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "gc"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["consolidated"] == 5

    def test_gc_nothing(self, mock_manager: MagicMock) -> None:
        mock_manager.consolidate.return_value = 0
        result = runner.invoke(app, ["memory", "gc"])
        assert result.exit_code == 0
        assert "Nothing to consolidate" in result.output


# ── grimoire memory delete ────────────────────────────────────────────────────


class TestMemoryDelete:
    def test_delete_with_yes(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "delete", "abc123", "--yes"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        mock_manager.delete.assert_called_once_with("abc123")

    def test_delete_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "delete", "abc123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["deleted"] is True

    def test_delete_not_found(self, mock_manager: MagicMock) -> None:
        mock_manager.recall.return_value = None
        result = runner.invoke(app, ["memory", "delete", "missing", "--yes"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_delete_confirm_abort(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "delete", "abc123"], input="n\n")
        assert result.exit_code == 0
        mock_manager.delete.assert_not_called()
