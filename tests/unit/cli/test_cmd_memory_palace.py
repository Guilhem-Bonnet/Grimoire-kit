"""Tests for palace taxonomy, MemPalace bridge, and sidecar CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact

runner = CliRunner()


def _make_entry(
    id: str = "e1",
    text: str = "Hello palace",
    *,
    metadata: dict[str, object] | None = None,
) -> MemoryEntry:
    return MemoryEntry(id=id, text=text, metadata=metadata or {})


def _make_fact(
    id: str = "fact-1",
    *,
    subject: str = "team",
    predicate: str = "decided",
    object_: str = "qdrant-local",
    valid_from: str = "2026-04-10",
) -> KnowledgeFact:
    return KnowledgeFact(
        id=id,
        subject=subject,
        predicate=predicate,
        object=object_,
        valid_from=valid_from,
        confidence=0.9,
        wing="project-test",
        hall="hall_facts",
        room="decisions",
    )


def _make_diary(id: str = "diary-1") -> DiaryRecord:
    return DiaryRecord(
        id=id,
        agent_name="atlas",
        topic="memory",
        entry="Tracked the storage decision.",
        created_at="2026-04-10T12:00:00",
    )


@pytest.fixture()
def mock_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.taxonomy.return_value = {
        "project-test": {
            "total": 1,
            "halls": {
                "hall_facts": {
                    "total": 1,
                    "rooms": {"decisions": 1},
                }
            },
        }
    }
    mgr.get_all_filtered.return_value = [
        _make_entry(
            metadata={
                "wing": "project-test",
                "hall": "hall_facts",
                "room": "decisions",
            }
        )
    ]
    mgr.store_many.return_value = [_make_entry(id="imported-1")]
    mgr.add_fact.return_value = _make_fact()
    mgr.invalidate_fact.return_value = 2
    mgr.query_facts.return_value = [_make_fact()]
    mgr.facts_timeline.return_value = [_make_fact(id="fact-timeline")]
    mgr.facts_stats.return_value = {"facts": 3, "active_facts": 2, "expired_facts": 1}
    mgr.write_diary.return_value = _make_diary()
    mgr.read_diary.return_value = [_make_diary()]
    mgr.diary_stats.return_value = {"diary_entries": 4, "agents": 2}

    with patch("grimoire.cli.cmd_memory._load_manager", return_value=mgr):
        yield mgr


@pytest.fixture()
def mock_palace_backend() -> MagicMock:
    backend = MagicMock()
    backend.store_many.return_value = [_make_entry(id="palace-1")]
    backend.get_all_filtered.return_value = [
        _make_entry(
            id="palace-entry",
            metadata={
                "wing": "project-test",
                "hall": "hall_facts",
                "room": "decisions",
            },
        )
    ]
    with patch("grimoire.cli.cmd_memory._load_mempalace_backend", return_value=backend):
        yield backend


class TestMemoryTaxonomy:
    def test_taxonomy_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "taxonomy"])
        assert result.exit_code == 0
        assert "project-test" in result.output
        assert "hall_facts" in result.output
        assert "decisions" in result.output
        mock_manager.taxonomy.assert_called_once_with(user_id="", wing="", hall="", room="")

    def test_taxonomy_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "taxonomy"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["project-test"]["halls"]["hall_facts"]["rooms"]["decisions"] == 1


class TestMemPalaceBridge:
    def test_mempalace_export(self, mock_manager: MagicMock, mock_palace_backend: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "mempalace-export", "--palace", "/tmp/palace"])
        assert result.exit_code == 0
        assert "Exported 1 memories" in result.output
        mock_manager.get_all_filtered.assert_called_once_with(user_id="", wing="", hall="", room="")
        mock_palace_backend.store_many.assert_called_once()

    def test_mempalace_import_json(self, mock_manager: MagicMock, mock_palace_backend: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "mempalace-import", "--palace", "/tmp/palace"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["imported"] == 1
        mock_palace_backend.get_all_filtered.assert_called_once_with(
            user_id="", filters={"wing": "", "hall": "", "room": ""}
        )
        mock_manager.store_many.assert_called_once()


class TestFactsCommands:
    def test_facts_add_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "facts", "add", "team", "decided", "qdrant-local"])
        assert result.exit_code == 0
        assert "Fact stored" in result.output
        mock_manager.add_fact.assert_called_once_with(
            "team",
            "decided",
            "qdrant-local",
            valid_from="",
            confidence=1.0,
            source_memory_id="",
        )

    def test_facts_invalidate_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "facts", "invalidate", "team", "decided", "qdrant-local"])
        assert result.exit_code == 0
        assert "Invalidated 2 fact" in result.output

    def test_facts_query_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "facts", "query", "team"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["predicate"] == "decided"

    def test_facts_timeline_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "facts", "timeline", "team"])
        assert result.exit_code == 0
        assert "qdrant-local" in result.output

    def test_facts_stats_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "facts", "stats"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["facts"] == 3
        assert data["active_facts"] == 2


class TestDiaryCommands:
    def test_diary_write_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "diary", "write", "atlas", "Tracked the storage decision."])
        assert result.exit_code == 0
        assert "Diary entry stored for" in result.output
        mock_manager.write_diary.assert_called_once_with(
            "atlas",
            "Tracked the storage decision.",
            topic="general",
            entry_format="markdown",
            related_memory_id="",
        )

    def test_diary_read_json(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["-o", "json", "memory", "diary", "read", "atlas", "--last", "1"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["agent_name"] == "atlas"
        assert data[0]["topic"] == "memory"

    def test_diary_stats_text(self, mock_manager: MagicMock) -> None:
        result = runner.invoke(app, ["memory", "diary", "stats"])
        assert result.exit_code == 0
        assert "Diary entries: 4" in result.output
        assert "Agents: 2" in result.output