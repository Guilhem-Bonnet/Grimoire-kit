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
) -> MemoryEntry:
    return MemoryEntry(
        id=id, text=text, user_id=user_id, tags=tags,
        score=score, created_at=created_at,
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
    mgr.search.return_value = [
        _make_entry(id="e1", text="Hello world", score=0.95, tags=("greet",)),
        _make_entry(id="e2", text="Second memory", score=0.80),
    ]
    mgr.search_taxonomy.return_value = list(mgr.search.return_value)
    mgr.get_all.return_value = [
        _make_entry(id="e1", text="Hello world", tags=("greet",)),
        _make_entry(id="e2", text="Second memory"),
    ]
    mgr.get_all_filtered.return_value = list(mgr.get_all.return_value)
    mgr.recall.return_value = _make_entry(id="abc123", text="Important")
    mgr.delete.return_value = True
    mgr.consolidate.return_value = 5
    mgr.store_many.return_value = [_make_entry(id="new1"), _make_entry(id="new2")]

    cfg = GrimoireConfig.from_dict({
        "project": {"name": "test", "type": "generic", "stack": []},
        "memory": {"backend": "local"},
        "agents": {"archetype": "minimal"},
    })

    with (
        patch("grimoire.cli.cmd_memory._load_manager", return_value=mgr),
        patch("grimoire.cli.cmd_memory._load_manager_context", return_value=(mgr, cfg, Path.cwd())),
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


# ── grimoire memory search ────────────────────────────────────────────────────


class TestMemorySearch:
    def test_search_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello"])
        assert result.exit_code == 0
        assert "Hello world" in result.output
        mock_manager.search_taxonomy.assert_called_once_with("hello", user_id="", limit=10, wing="", hall="", room="")

    def test_search_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "search", "hello"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["text"] == "Hello world"

    def test_search_with_limit(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello", "-n", "3"])
        assert result.exit_code == 0
        mock_manager.search_taxonomy.assert_called_once_with("hello", user_id="", limit=3, wing="", hall="", room="")

    def test_search_with_user(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "search", "hello", "-u", "bob"])
        assert result.exit_code == 0
        mock_manager.search_taxonomy.assert_called_once_with("hello", user_id="bob", limit=10, wing="", hall="", room="")

    def test_search_no_results(self, mock_manager: MagicMock) -> None:
        mock_manager.search_taxonomy.return_value = []
        result = runner.invoke(app, ["memory", "search", "nonexist"])
        assert result.exit_code == 0
        assert "No memories matching" in result.output


# ── grimoire memory remember / recall ─────────────────────────────────────────


class TestMemoryRemember:
    def test_remember_text(self, mock_manager: MagicMock) -> None:
        mock_manager.remember.return_value = _make_entry(id="typed1", text="fait important")
        result = runner.invoke(app, ["memory", "remember", "fait important", "-t", "decisions", "-a", "dev"])
        assert result.exit_code == 0
        assert "remembered" in result.output
        mock_manager.remember.assert_called_once_with("decisions", "dev", "fait important", tags=())

    def test_remember_json(self, mock_manager: MagicMock) -> None:
        mock_manager.remember.return_value = _make_entry(id="typed1", text="fait")
        result = runner.invoke(app, ["-o", "json", "memory", "remember", "fait", "-t", "failures", "-a", "qa"])
        assert result.exit_code == 0
        assert json.loads(result.output)["id"] == "typed1"

    def test_remember_tags(self, mock_manager: MagicMock) -> None:
        mock_manager.remember.return_value = _make_entry(id="typed1")
        runner.invoke(app, ["memory", "remember", "x", "-t", "decisions", "-a", "dev", "--tags", "infra, db"])
        mock_manager.remember.assert_called_once_with("decisions", "dev", "x", tags=("infra", "db"))

    def test_remember_invalid_type_exits(self, mock_manager: MagicMock) -> None:
        from grimoire.core.exceptions import GrimoireMemoryError

        mock_manager.remember.side_effect = GrimoireMemoryError("Invalid memory type 'gossip'")
        result = runner.invoke(app, ["memory", "remember", "x", "-t", "gossip", "-a", "dev"])
        assert result.exit_code == 1


class TestMemoryRecall:
    def test_recall_text(self, mock_manager: MagicMock) -> None:
        mock_manager.recall_typed.return_value = [_make_entry(id="e1", text="Hello world")]
        result = runner.invoke(app, ["memory", "recall", "hello"])
        assert result.exit_code == 0
        assert "Hello world" in result.output

    def test_recall_filters_forwarded(self, mock_manager: MagicMock) -> None:
        mock_manager.recall_typed.return_value = []
        runner.invoke(app, ["memory", "recall", "q", "-t", "failures", "-a", "dev", "-n", "3"])
        mock_manager.recall_typed.assert_called_once_with("q", type_="failures", agent="dev", limit=3)

    def test_recall_json(self, mock_manager: MagicMock) -> None:
        mock_manager.recall_typed.return_value = [_make_entry(id="e1")]
        result = runner.invoke(app, ["-o", "json", "memory", "recall", "q"])
        assert result.exit_code == 0
        assert json.loads(result.output)[0]["id"] == "e1"

    def test_recall_empty(self, mock_manager: MagicMock) -> None:
        mock_manager.recall_typed.return_value = []
        result = runner.invoke(app, ["memory", "recall", "nonexist"])
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
        mock_manager.get_all_filtered.assert_any_call(user_id="", offset=10, limit=5, wing="", hall="", room="")

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
        assert "Imported" in result.output
        assert "2" in result.output
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
        assert "3" in result.output
        assert "entries" in result.output
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
        assert "Consolidated" in result.output
        assert "5" in result.output

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
