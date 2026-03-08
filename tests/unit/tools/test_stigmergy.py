"""Tests for grimoire.tools.stigmergy — Pheromone-based coordination board."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.tools.stigmergy import (
    DEFAULT_HALF_LIFE_HOURS,
    MAX_INTENSITY,
    REINFORCEMENT_BOOST,
    Pheromone,
    PheromoneBoard,
    Stigmergy,
    TrailPattern,
    amplify_pheromone,
    analyze_trails,
    compute_intensity,
    emit_pheromone,
    evaporate,
    load_board,
    resolve_pheromone,
    save_board,
    sense_pheromones,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire-output").mkdir()
    return tmp_path


@pytest.fixture()
def stig(root: Path) -> Stigmergy:
    return Stigmergy(root)


@pytest.fixture()
def board() -> PheromoneBoard:
    return PheromoneBoard()


# ── Pheromone Model ───────────────────────────────────────────────────────────


class TestPheromone:
    def test_from_dict_roundtrip(self) -> None:
        d = {
            "pheromone_id": "PH-abc12345",
            "pheromone_type": "ALERT",
            "location": "src/auth",
            "text": "Review needed",
            "emitter": "dev",
            "timestamp": "2025-01-01T00:00:00+00:00",
            "intensity": 0.8,
            "tags": ["sec"],
            "reinforcements": 1,
            "reinforced_by": ["qa"],
            "resolved": False,
        }
        p = Pheromone.from_dict(d)
        assert p.pheromone_type == "ALERT"
        assert p.intensity == 0.8
        assert p.tags == ["sec"]
        out = p.to_dict()
        assert out["pheromone_id"] == "PH-abc12345"

    def test_defaults(self) -> None:
        p = Pheromone(pheromone_id="PH-x", pheromone_type="NEED",
                      location="", text="", emitter="", timestamp="")
        assert p.reinforcements == 0
        assert p.resolved is False
        assert p.tags == []


# ── PheromoneBoard Model ─────────────────────────────────────────────────────


class TestPheromoneBoard:
    def test_empty_board(self) -> None:
        b = PheromoneBoard()
        assert b.pheromones == []
        assert b.total_emitted == 0

    def test_to_dict_from_dict(self) -> None:
        b = PheromoneBoard(total_emitted=3)
        b.pheromones.append(Pheromone(
            pheromone_id="PH-1", pheromone_type="NEED",
            location="src", text="help", emitter="dev",
            timestamp="2025-01-01T00:00:00+00:00",
        ))
        d = b.to_dict()
        restored = PheromoneBoard.from_dict(d)
        assert len(restored.pheromones) == 1
        assert restored.total_emitted == 3


# ── TrailPattern ──────────────────────────────────────────────────────────────


class TestTrailPattern:
    def test_to_dict(self) -> None:
        t = TrailPattern(
            pattern_type="hot-zone", location="src/api",
            description="3 signals", involved_agents=("dev", "qa"),
            pheromone_count=3, avg_intensity=0.65,
        )
        d = t.to_dict()
        assert d["pattern_type"] == "hot-zone"
        assert d["involved_agents"] == ["dev", "qa"]


# ── Persistence ───────────────────────────────────────────────────────────────


class TestPersistence:
    def test_load_empty(self, root: Path) -> None:
        b = load_board(root)
        assert isinstance(b, PheromoneBoard)
        assert len(b.pheromones) == 0

    def test_save_and_load(self, root: Path) -> None:
        b = PheromoneBoard()
        emit_pheromone(b, "ALERT", "src", "fire", "qa")
        save_board(root, b)
        loaded = load_board(root)
        assert len(loaded.pheromones) == 1
        assert loaded.total_emitted == 1

    def test_load_corrupted_json(self, root: Path) -> None:
        (root / "_grimoire-output" / "pheromone-board.json").write_text("{bad")
        b = load_board(root)
        assert len(b.pheromones) == 0


# ── Emit ──────────────────────────────────────────────────────────────────────


class TestEmitPheromone:
    def test_emit_adds_to_board(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "src/db", "migration needed", "dev")
        assert len(board.pheromones) == 1
        assert board.total_emitted == 1
        assert p.pheromone_type == "NEED"
        assert p.pheromone_id.startswith("PH-")

    def test_emit_clamps_intensity(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "", "", "qa", intensity=5.0)
        assert p.intensity <= MAX_INTENSITY

    def test_emit_with_tags(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "src", "vuln", "sec", tags=["cve", "high"])
        assert p.tags == ["cve", "high"]

    def test_emit_multiple(self, board: PheromoneBoard) -> None:
        for i in range(5):
            emit_pheromone(board, "PROGRESS", "src", f"step {i}", "dev")
        assert len(board.pheromones) == 5
        assert board.total_emitted == 5


# ── Amplify ───────────────────────────────────────────────────────────────────


class TestAmplify:
    def test_amplify_increases_intensity(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "src", "fire", "dev")
        original = p.intensity
        result = amplify_pheromone(board, p.pheromone_id, "qa")
        assert result is not None
        assert result.intensity == min(original + REINFORCEMENT_BOOST, MAX_INTENSITY)
        assert result.reinforcements == 1
        assert "qa" in result.reinforced_by

    def test_amplify_not_found(self, board: PheromoneBoard) -> None:
        result = amplify_pheromone(board, "PH-nonexistent", "qa")
        assert result is None

    def test_amplify_deduplicates_agent(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "", "", "dev")
        amplify_pheromone(board, p.pheromone_id, "qa")
        amplify_pheromone(board, p.pheromone_id, "qa")
        assert p.reinforced_by.count("qa") == 1
        assert p.reinforcements == 2


# ── Resolve ───────────────────────────────────────────────────────────────────


class TestResolve:
    def test_resolve_marks_resolved(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "BLOCK", "src", "stuck", "dev")
        result = resolve_pheromone(board, p.pheromone_id, "senior-dev")
        assert result is not None
        assert result.resolved is True
        assert result.resolved_by == "senior-dev"
        assert result.resolved_at != ""

    def test_resolve_not_found(self, board: PheromoneBoard) -> None:
        result = resolve_pheromone(board, "PH-nope", "agent")
        assert result is None


# ── Compute Intensity ─────────────────────────────────────────────────────────


class TestComputeIntensity:
    def test_fresh_pheromone(self) -> None:
        now = datetime.now(tz=UTC)
        p = Pheromone(
            pheromone_id="PH-x", pheromone_type="NEED",
            location="", text="", emitter="",
            timestamp=now.isoformat(), intensity=0.7,
        )
        assert compute_intensity(p, DEFAULT_HALF_LIFE_HOURS, now) == pytest.approx(0.7)

    def test_one_half_life(self) -> None:
        now = datetime.now(tz=UTC)
        then = now - timedelta(hours=DEFAULT_HALF_LIFE_HOURS)
        p = Pheromone(
            pheromone_id="PH-x", pheromone_type="NEED",
            location="", text="", emitter="",
            timestamp=then.isoformat(), intensity=0.7,
        )
        result = compute_intensity(p, DEFAULT_HALF_LIFE_HOURS, now)
        assert result == pytest.approx(0.35, rel=0.01)

    def test_bad_timestamp_returns_original(self) -> None:
        p = Pheromone(
            pheromone_id="PH-x", pheromone_type="NEED",
            location="", text="", emitter="",
            timestamp="not-a-date", intensity=0.5,
        )
        assert compute_intensity(p, 72.0) == 0.5


# ── Sense ─────────────────────────────────────────────────────────────────────


class TestSense:
    def test_sense_all(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "src/a", "a", "dev")
        emit_pheromone(board, "ALERT", "src/b", "b", "qa")
        results = sense_pheromones(board)
        assert len(results) == 2

    def test_sense_filter_type(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "src/a", "a", "dev")
        emit_pheromone(board, "ALERT", "src/b", "b", "qa")
        results = sense_pheromones(board, ptype="ALERT")
        assert len(results) == 1
        assert results[0][0].pheromone_type == "ALERT"

    def test_sense_filter_location(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "src/auth/login.py", "a", "dev")
        emit_pheromone(board, "NEED", "src/db/model.py", "b", "dev")
        results = sense_pheromones(board, location="auth")
        assert len(results) == 1

    def test_sense_filter_tag(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "ALERT", "src", "vuln", "sec", tags=["cve"])
        emit_pheromone(board, "ALERT", "src", "perf", "dev", tags=["slow"])
        results = sense_pheromones(board, tag="cve")
        assert len(results) == 1

    def test_sense_excludes_resolved(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "src", "done", "dev")
        resolve_pheromone(board, p.pheromone_id, "qa")
        results = sense_pheromones(board)
        assert len(results) == 0

    def test_sense_sorted_by_intensity(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "a", "low", "d", intensity=0.3)
        emit_pheromone(board, "ALERT", "a", "high", "d", intensity=0.9)
        results = sense_pheromones(board)
        assert results[0][1] > results[1][1]


# ── Evaporate ─────────────────────────────────────────────────────────────────


class TestEvaporate:
    def test_evaporate_empty(self) -> None:
        b = PheromoneBoard()
        _, removed = evaporate(b)
        assert removed == 0

    def test_evaporate_removes_old(self) -> None:
        b = PheromoneBoard()
        emit_pheromone(b, "NEED", "src", "ancient", "dev", intensity=0.1)
        far_past = (datetime.now(tz=UTC) - timedelta(days=60)).isoformat()
        b.pheromones[0].timestamp = far_past
        _, removed = evaporate(b)
        assert removed == 1
        assert len(b.pheromones) == 0

    def test_evaporate_keeps_recent(self) -> None:
        b = PheromoneBoard()
        emit_pheromone(b, "ALERT", "src", "fresh", "qa")
        _, removed = evaporate(b)
        assert removed == 0
        assert len(b.pheromones) == 1

    def test_evaporate_removes_resolved(self) -> None:
        b = PheromoneBoard()
        p = emit_pheromone(b, "NEED", "src", "done", "dev")
        resolve_pheromone(b, p.pheromone_id, "qa")
        _, removed = evaporate(b)
        assert removed == 1


# ── Trails ────────────────────────────────────────────────────────────────────


class TestAnalyzeTrails:
    def test_empty_board(self) -> None:
        patterns = analyze_trails(PheromoneBoard())
        assert patterns == []

    def test_hot_zone(self) -> None:
        b = PheromoneBoard()
        for i in range(4):
            emit_pheromone(b, "NEED", "src/auth", f"item {i}", "dev")
        patterns = analyze_trails(b)
        hot = [p for p in patterns if p.pattern_type == "hot-zone"]
        assert len(hot) == 1
        assert hot[0].location == "src/auth"
        assert hot[0].pheromone_count >= 3

    def test_convergence(self) -> None:
        b = PheromoneBoard()
        emit_pheromone(b, "NEED", "src/api", "review", "dev")
        emit_pheromone(b, "ALERT", "src/api", "sec issue", "sec")
        patterns = analyze_trails(b)
        conv = [p for p in patterns if p.pattern_type == "convergence"]
        assert len(conv) == 1
        assert "dev" in conv[0].involved_agents
        assert "sec" in conv[0].involved_agents

    def test_bottleneck(self) -> None:
        b = PheromoneBoard()
        emit_pheromone(b, "BLOCK", "src/db", "lock", "dev")
        emit_pheromone(b, "BLOCK", "src/db", "timeout", "qa")
        patterns = analyze_trails(b)
        bottleneck = [p for p in patterns if p.pattern_type == "bottleneck"]
        assert len(bottleneck) == 1


# ── Stigmergy Tool (integration) ─────────────────────────────────────────────


class TestStigmergyTool:
    def test_emit_action(self, stig: Stigmergy, root: Path) -> None:
        board = stig.run(action="emit", ptype="ALERT",
                         location="src/auth", text="review", emitter="dev")
        assert isinstance(board, PheromoneBoard)
        assert len(board.pheromones) == 1
        assert (root / "_grimoire-output" / "pheromone-board.json").exists()

    def test_sense_action(self, stig: Stigmergy) -> None:
        stig.run(action="emit", ptype="NEED", text="help", emitter="dev")
        board = stig.run(action="sense")
        assert len(board.pheromones) == 1

    def test_amplify_action(self, stig: Stigmergy) -> None:
        board = stig.run(action="emit", ptype="ALERT", text="x", emitter="dev")
        pid = board.pheromones[0].pheromone_id
        board2 = stig.run(action="amplify", pheromone_id=pid, agent="qa")
        assert board2.pheromones[0].reinforcements == 1

    def test_resolve_action(self, stig: Stigmergy) -> None:
        board = stig.run(action="emit", ptype="BLOCK", text="stuck", emitter="dev")
        pid = board.pheromones[0].pheromone_id
        board2 = stig.run(action="resolve", pheromone_id=pid, agent="lead")
        assert board2.pheromones[0].resolved is True

    def test_evaporate_action(self, stig: Stigmergy) -> None:
        stig.run(action="emit", ptype="NEED", text="old", emitter="dev")
        board = stig.run(action="evaporate")
        assert isinstance(board, PheromoneBoard)

    def test_default_action_is_sense(self, stig: Stigmergy) -> None:
        board = stig.run()
        assert isinstance(board, PheromoneBoard)
        assert len(board.pheromones) == 0
