"""Tests de la logique des hooks stigmergiques (auto-émission / captation).

Charge le script autonome ``framework/tools/stigmergy_hooks/scripts/
stigmergy_hook.py`` par chemin et vérifie les décisions (émission, renfort,
COMPLETE, purge), le format de contexte, l'extraction défensive d'event, le
fail-open, et la parité de format du board avec le module SDK.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from grimoire.tools import stigmergy as sdk


def _load_hook_module() -> ModuleType:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "framework" / "tools" / "stigmergy_hooks" / "scripts" / "stigmergy_hook.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("stigmergy_hook", candidate)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    pytest.skip("stigmergy_hook.py introuvable")


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    return _load_hook_module()


NOW = datetime(2026, 1, 2, tzinfo=UTC)


class TestDecisions:
    def test_edit_emits_progress(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        event = {"tool_name": "Edit", "tool_input": {"file_path": "src/auth/login.py"}, "agent": "dev"}
        assert hook.decide_post_edit(board, event, NOW) == "emit"
        assert len(board["pheromones"]) == 1
        assert board["pheromones"][0]["pheromone_type"] == "PROGRESS"
        assert board["pheromones"][0]["location"] == "src/auth"

    def test_second_edit_same_zone_reinforces(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        e1 = {"tool_name": "Edit", "tool_input": {"file_path": "src/auth/login.py"}, "agent": "dev"}
        e2 = {"tool_name": "Write", "tool_input": {"file_path": "src/auth/token.py"}, "agent": "qa"}
        hook.decide_post_edit(board, e1, NOW)
        assert hook.decide_post_edit(board, e2, NOW) == "reinforce"
        # anti-bruit : une seule piste pour la zone, renforcée
        assert len(board["pheromones"]) == 1
        assert board["pheromones"][0]["reinforcements"] == 1
        assert "qa" in board["pheromones"][0]["reinforced_by"]

    def test_non_write_tool_no_emit(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        event = {"tool_name": "Read", "tool_input": {"file_path": "src/auth/login.py"}}
        assert hook.decide_post_edit(board, event, NOW) is None
        assert board["pheromones"] == []

    def test_non_source_file_no_emit(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        event = {"tool_name": "Write", "tool_input": {"file_path": "build/output.bin"}}
        assert hook.decide_post_edit(board, event, NOW) is None

    def test_stop_marks_most_active_complete_and_purges(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        hook.emit(board, "PROGRESS", "src/auth", "t", "dev", 0.7)
        hook.emit(board, "PROGRESS", "src/auth", "t2", "dev", 0.7)
        hook.emit(board, "PROGRESS", "docs", "t", "dev", 0.3)
        # un signal déjà mort → doit être purgé au stop
        dead = hook.emit(board, "NEED", "old", "x", "dev", 0.7)
        dead["timestamp"] = (NOW - timedelta(hours=1000)).isoformat()
        zone = hook.decide_stop(board, {"agent": "dev"}, NOW)
        assert zone == "src/auth"
        types = [(p["pheromone_type"], p["location"]) for p in board["pheromones"]]
        assert ("COMPLETE", "src/auth") in types
        assert ("NEED", "old") not in types  # purgé
        assert board["total_evaporated"] >= 1


class TestSenseAndEvents:
    def test_format_sense_lists_active(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        hook.emit(board, "ALERT", "src/db", "faille", "qa", 0.7)
        text = hook.format_sense(board, NOW)
        assert "ALERT @ src/db" in text
        assert "faille" in text

    def test_format_sense_empty(self, hook: ModuleType) -> None:
        board = {"pheromones": [], "half_life_hours": 72.0, "total_emitted": 0, "total_evaporated": 0}
        assert hook.format_sense(board, NOW) == ""

    def test_event_extraction_variants(self, hook: ModuleType) -> None:
        assert hook.event_tool_name({"toolName": "Edit"}) == "Edit"
        assert hook.event_file_path({"toolInput": {"filePath": "a/b.py"}}) == "a/b.py"
        assert hook.zone_of("a/b/c.py") == "a/b"
        assert hook.event_emitter({}) == "session"

    def test_main_fail_open_on_garbage(self, hook: ModuleType, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin", _FakeStdin("not json at all"))
        rc = hook.main(["emit-post-edit"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"


class TestParity:
    def test_hook_board_read_by_sdk(self, hook: ModuleType, tmp_path: Path) -> None:
        board = hook.load_board(tmp_path)
        hook.emit(board, "PROGRESS", "src/api", "wip", "dev", 0.5)
        hook.save_board(tmp_path, board)
        sdk_board = sdk.load_board(tmp_path)
        active = sdk.sense_pheromones(sdk_board)
        assert len(active) == 1
        assert active[0][0].pheromone_type == "PROGRESS"

    def test_decay_matches_sdk(self, hook: ModuleType) -> None:
        ph = {"timestamp": (NOW - timedelta(hours=72)).isoformat(), "intensity": 1.0}
        assert hook.compute_intensity(ph, 72.0, NOW) == pytest.approx(0.5, abs=1e-9)


class _FakeStdin:
    def __init__(self, data: str) -> None:
        self._data = data

    def read(self) -> str:
        return self._data
