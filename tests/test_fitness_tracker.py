"""Tests for fitness-tracker.py — System fitness scoring."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("fitness_tracker", TOOLS / "fitness-tracker.py")
ft = importlib.util.module_from_spec(_spec)
sys.modules["fitness_tracker"] = ft
_spec.loader.exec_module(ft)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Projet temp avec structure minimale."""
    (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)
    # Create some test files
    for i in range(5):
        (tmp_path / "tests" / f"test_{i}.py").write_text(f"# test {i}\n")
    return tmp_path


# ── DimensionResult ──────────────────────────────────────────────────────────


class TestDimensionResult:
    def test_creation(self):
        d = ft.DimensionResult(name="test", score=0.8, weight=0.3, weighted=0.24)
        assert d.name == "test"
        assert d.score == 0.8
        assert d.weighted == 0.24


# ── Test Health ──────────────────────────────────────────────────────────────


class TestTestHealth:
    def test_no_tests_dir(self, tmp_path):
        result = ft.measure_test_health(tmp_path)
        assert result.name == "test_health"
        assert result.score == 0.2

    def test_empty_tests_dir(self, tmp_path):
        (tmp_path / "tests").mkdir()
        result = ft.measure_test_health(tmp_path)
        assert result.name == "test_health"
        assert result.score == 0.3

    def test_few_tests(self, tmp_project):
        result = ft.measure_test_health(tmp_project)
        assert result.name == "test_health"
        # 5 test files → < 10 → 0.6
        assert result.score == 0.6

    def test_many_tests(self, tmp_path):
        (tmp_path / "tests").mkdir()
        for i in range(55):
            (tmp_path / "tests" / f"test_{i}.py").write_text(f"# test {i}\n")
        result = ft.measure_test_health(tmp_path)
        assert result.score == 1.0


# ── Memory Freshness ────────────────────────────────────────────────────────


class TestMemoryFreshness:
    def test_no_memory_dir(self, tmp_path):
        result = ft.measure_memory_freshness(tmp_path)
        assert result.score == 0.3

    def test_empty_memory(self, tmp_path):
        (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
        result = ft.measure_memory_freshness(tmp_path)
        assert result.score == 0.3

    def test_fresh_memory(self, tmp_project):
        # Write a file just now → should be very fresh
        (tmp_project / "_bmad" / "_memory" / "test.md").write_text("# Fresh\n")
        result = ft.measure_memory_freshness(tmp_project)
        assert result.score == 1.0
        assert "à jour" in result.detail


# ── Healing Measure ─────────────────────────────────────────────────────────


class TestHealingMeasure:
    def test_no_tool(self, tmp_path):
        result = ft.measure_healing(tmp_path)
        # Tool not available → neutral
        assert 0.4 <= result.score <= 0.7

    def test_no_history(self, tmp_project):
        # self-healing.py exists but no history
        result = ft.measure_healing(tmp_project)
        # Either tool unavailable (neutral) or no history (default)
        assert 0.3 <= result.score <= 0.8


# ── Compute Fitness ─────────────────────────────────────────────────────────


class TestComputeFitness:
    def test_computes_score(self, tmp_project):
        snapshot = ft.compute_fitness(tmp_project)
        assert 0 <= snapshot.fitness_score <= 100
        assert snapshot.level in ("HEALTHY", "WARNING", "CRITICAL")
        assert len(snapshot.dimensions) == 5

    def test_snapshot_to_dict(self, tmp_project):
        snapshot = ft.compute_fitness(tmp_project)
        d = snapshot.to_dict()
        assert "fitness_score" in d
        assert "level" in d
        assert "dimensions" in d
        assert isinstance(d["dimensions"], list)


# ── History JSONL ────────────────────────────────────────────────────────────


class TestHistory:
    def test_save_and_load(self, tmp_project):
        snapshot = ft.compute_fitness(tmp_project)
        path = ft.save_snapshot(snapshot, tmp_project)
        assert path.exists()

        history = ft.load_history(tmp_project)
        assert len(history) == 1
        assert "score" in history[0]

    def test_multiple_saves(self, tmp_project):
        for _ in range(3):
            snapshot = ft.compute_fitness(tmp_project)
            ft.save_snapshot(snapshot, tmp_project)

        history = ft.load_history(tmp_project)
        assert len(history) == 3

    def test_prune_history(self, tmp_path):
        (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
        path = tmp_path / ft.HISTORY_FILE

        # Write more than MAX entries
        with open(path, "w", encoding="utf-8") as f:
            for i in range(ft.MAX_HISTORY + 50):
                f.write(json.dumps({"ts": f"2025-01-{i:06d}", "score": i}) + "\n")

        ft._prune_history(path)
        lines = path.read_text().splitlines()
        assert len(lines) == ft.MAX_HISTORY

    def test_load_empty(self, tmp_project):
        history = ft.load_history(tmp_project)
        assert history == []

    def test_load_corrupt_lines(self, tmp_project):
        path = tmp_project / ft.HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"score": 42}\nnot json\n{"score": 84}\n')
        history = ft.load_history(tmp_project)
        assert len(history) == 2


# ── Trend ────────────────────────────────────────────────────────────────────


class TestTrend:
    def test_insufficient_data(self, tmp_project):
        trend = ft.compute_trend(tmp_project)
        assert trend["trend"] == "insufficient_data"

    def test_stable_trend(self, tmp_project):
        path = tmp_project / ft.HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps({"ts": f"t{i}", "score": 60}) + "\n")

        trend = ft.compute_trend(tmp_project)
        assert trend["trend"] == "stable"
        assert trend["delta"] == 0.0

    def test_improving_trend(self, tmp_project):
        path = tmp_project / ft.HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps({"ts": f"t{i}", "score": 30 + i * 5}) + "\n")

        trend = ft.compute_trend(tmp_project)
        assert trend["trend"] == "improving"
        assert trend["delta"] > 0

    def test_declining_trend(self, tmp_project):
        path = tmp_project / ft.HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps({"ts": f"t{i}", "score": 80 - i * 5}) + "\n")

        trend = ft.compute_trend(tmp_project)
        assert trend["trend"] == "declining"
        assert trend["delta"] < 0


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_fitness_check(self, tmp_project):
        result = ft.mcp_fitness_check(str(tmp_project))
        assert result["status"] == "ok"
        assert "fitness_score" in result
        assert "level" in result
        assert "trend" in result

    def test_fitness_trend(self, tmp_project):
        result = ft.mcp_fitness_trend(str(tmp_project))
        assert result["status"] == "ok"


# ── Render ───────────────────────────────────────────────────────────────────


class TestRender:
    def test_render_report(self, tmp_project):
        snapshot = ft.compute_fitness(tmp_project)
        text = ft.render_report(snapshot)
        assert "Fitness Tracker" in text
        assert "Score:" in text

    def test_render_trend_no_data(self, tmp_project):
        text = ft.render_trend(tmp_project)
        assert "Pas assez" in text or "insufficient" in text

    def test_render_trend_with_data(self, tmp_project):
        path = tmp_project / ft.HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"ts": f"t{i}", "score": 50 + i}) + "\n")
        text = ft.render_trend(tmp_project)
        assert "Tendance" in text


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_check_cli(self, capsys, tmp_project):
        ret = ft.main(["--project-root", str(tmp_project), "check"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Fitness" in out

    def test_check_json(self, capsys, tmp_project):
        ret = ft.main(["--project-root", str(tmp_project), "--json", "check"])
        assert ret == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "fitness_score" in data

    def test_trend_cli(self, capsys, tmp_project):
        ret = ft.main(["--project-root", str(tmp_project), "trend"])
        assert ret == 0

    def test_default_command(self, capsys, tmp_project):
        ret = ft.main(["--project-root", str(tmp_project)])
        assert ret == 0
