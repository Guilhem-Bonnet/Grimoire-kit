"""Tests for grimoire.tools.stigmergy — Pheromone-based coordination board."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.tools.stigmergy import (
    DEFAULT_HALF_LIFE_HOURS,
    MAX_INTENSITY,
    REINFORCEMENT_BOOST,
    TYPE_HALF_LIFE,
    TYPE_ICONS,
    Pheromone,
    PheromoneBoard,
    Stigmergy,
    TrailPattern,
    amplify_pheromone,
    analyze_trails,
    bulk_deposit,
    compute_intensity,
    compute_urgency_score,
    deposit_pheromone,
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


# ── Deposit Pheromone (atomic helper) ────────────────────────────────────────


class TestDepositPheromone:
    def test_creates_new_signal(self, root: Path) -> None:
        p = deposit_pheromone(root, "NEED", "test/loc", "test signal", "tester")
        assert p is not None
        board = load_board(root)
        assert len(board.pheromones) == 1
        assert board.pheromones[0].pheromone_type == "NEED"

    def test_deduplicates_identical_signal(self, root: Path) -> None:
        p1 = deposit_pheromone(root, "ALERT", "test/loc", "same signal", "tool-a", intensity=0.5)
        p2 = deposit_pheromone(root, "ALERT", "test/loc", "same signal", "tool-b")
        assert p1 is not None
        assert p2 is not None
        assert p1.pheromone_id == p2.pheromone_id
        board = load_board(root)
        assert len(board.pheromones) == 1
        assert board.pheromones[0].reinforcements == 1

    def test_different_location_creates_new(self, root: Path) -> None:
        deposit_pheromone(root, "ALERT", "test/a", "signal", "tool")
        deposit_pheromone(root, "ALERT", "test/b", "signal", "tool")
        board = load_board(root)
        assert len(board.pheromones) == 2

    def test_resolved_signal_creates_new(self, root: Path) -> None:
        p1 = deposit_pheromone(root, "NEED", "test/loc", "done", "tool")
        assert p1 is not None
        board = load_board(root)
        resolve_pheromone(board, p1.pheromone_id, "agent")
        save_board(root, board)
        p2 = deposit_pheromone(root, "NEED", "test/loc", "done", "tool")
        assert p2 is not None
        assert p2.pheromone_id != p1.pheromone_id
        board2 = load_board(root)
        # 2 pheromones: 1 resolved + 1 new
        assert len(board2.pheromones) == 2

    def test_returns_none_on_missing_output_dir(self, tmp_path: Path) -> None:
        # No _grimoire-output dir — load_board falls back to empty, save creates it
        result = deposit_pheromone(tmp_path, "NEED", "loc", "text", "tool")
        # Either succeeds (creates dir) or returns None — never raises
        assert result is None or result.pheromone_type == "NEED"


# ── Constants ──────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_type_icons_complete(self) -> None:
        from grimoire.tools.stigmergy import VALID_TYPES
        assert set(TYPE_ICONS.keys()) == VALID_TYPES

    def test_type_half_life_complete(self) -> None:
        from grimoire.tools.stigmergy import VALID_TYPES
        assert set(TYPE_HALF_LIFE.keys()) == VALID_TYPES

    def test_complete_evaporates_fastest(self) -> None:
        assert TYPE_HALF_LIFE["COMPLETE"] < TYPE_HALF_LIFE["ALERT"]
        assert TYPE_HALF_LIFE["COMPLETE"] < TYPE_HALF_LIFE["BLOCK"]

    def test_opportunity_persists_longest(self) -> None:
        assert TYPE_HALF_LIFE["OPPORTUNITY"] == max(TYPE_HALF_LIFE.values())

    def test_block_outlasts_progress(self) -> None:
        assert TYPE_HALF_LIFE["BLOCK"] > TYPE_HALF_LIFE["PROGRESS"]


# ── Per-type TTL ─────────────────────────────────────────────────────────────────


class TestPerTypeTTL:
    """COMPLETE and OPPORTUNITY have different evaporation rates."""

    def _make(self, ptype: str, age_hours: float, intensity: float = 0.7) -> Pheromone:
        ts = (datetime.now(tz=UTC) - timedelta(hours=age_hours)).isoformat()
        return Pheromone(
            pheromone_id="PH-x", pheromone_type=ptype,
            location="", text="", emitter="",
            timestamp=ts, intensity=intensity,
        )

    def test_complete_at_24h_is_half(self) -> None:
        p = self._make("COMPLETE", age_hours=24.0)
        result = compute_intensity(p, DEFAULT_HALF_LIFE_HOURS)
        assert result == pytest.approx(0.35, rel=0.02)

    def test_opportunity_at_24h_barely_decays(self) -> None:
        p = self._make("OPPORTUNITY", age_hours=24.0)
        result = compute_intensity(p, DEFAULT_HALF_LIFE_HOURS)
        # half-life=168h, age=24h => 0.7 * 0.5^(24/168) ≈ 0.632
        assert result > 0.60

    def test_complete_decays_faster_than_block(self) -> None:
        now = datetime.now(tz=UTC)
        age = timedelta(hours=48)
        ts = (now - age).isoformat()
        p_complete = Pheromone(
            pheromone_id="PH-1", pheromone_type="COMPLETE",
            location="", text="", emitter="", timestamp=ts, intensity=0.7,
        )
        p_block = Pheromone(
            pheromone_id="PH-2", pheromone_type="BLOCK",
            location="", text="", emitter="", timestamp=ts, intensity=0.7,
        )
        i_complete = compute_intensity(p_complete, DEFAULT_HALF_LIFE_HOURS, now)
        i_block = compute_intensity(p_block, DEFAULT_HALF_LIFE_HOURS, now)
        assert i_complete < i_block

    def test_evaporate_removes_old_complete_before_block(self, board: PheromoneBoard) -> None:
        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(hours=60)).isoformat()
        # COMPLETE at 60h age (2.5x its 24h half-life) → ~0.7 * 0.5^2.5 ≈ 0.124 > threshold
        # But at 100h → 0.7 * 0.5^(100/24) ≈ 0.024 < threshold → evaporates
        very_old_ts = (now - timedelta(hours=100)).isoformat()
        board.pheromones.append(Pheromone(
            pheromone_id="PH-c", pheromone_type="COMPLETE",
            location="", text="", emitter="", timestamp=very_old_ts, intensity=0.7,
        ))
        board.pheromones.append(Pheromone(
            pheromone_id="PH-b", pheromone_type="BLOCK",
            location="", text="", emitter="", timestamp=old_ts, intensity=0.7,
        ))
        _, removed = evaporate(board, now=now)
        assert removed == 1  # only COMPLETE evaporated
        remaining_types = [p.pheromone_type for p in board.pheromones]
        assert "BLOCK" in remaining_types
        assert "COMPLETE" not in remaining_types


# ── Urgency Score ─────────────────────────────────────────────────────────────────


class TestUrgencyScore:
    def test_no_reinforcements_equals_intensity(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "src", "issue", "dev", intensity=0.6)
        score = compute_urgency_score(p, DEFAULT_HALF_LIFE_HOURS)
        assert score == pytest.approx(0.6, rel=0.01)

    def test_reinforcements_raise_score(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "src", "issue", "dev", intensity=0.6)
        p.reinforcements = 3
        score = compute_urgency_score(p, DEFAULT_HALF_LIFE_HOURS)
        # 0.6 * (1 + 3*0.3) = 0.6 * 1.9 = 1.14
        assert score == pytest.approx(1.14, rel=0.01)

    def test_capped_at_2(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "BLOCK", "src", "stuck", "dev", intensity=1.0)
        p.reinforcements = 10
        score = compute_urgency_score(p, DEFAULT_HALF_LIFE_HOURS)
        assert score == pytest.approx(2.0)

    def test_high_reinforce_beats_fresh_unreinforced(self, board: PheromoneBoard) -> None:
        now = datetime.now(tz=UTC)
        # Older signal but heavily reinforced
        old_ts = (now - timedelta(hours=24)).isoformat()
        p_old = Pheromone(
            pheromone_id="PH-old", pheromone_type="ALERT",
            location="", text="", emitter="dev",
            timestamp=old_ts, intensity=0.7, reinforcements=5,
        )
        # Fresh signal, no reinforcements
        p_fresh = emit_pheromone(board, "ALERT", "src", "fresh", "qa", intensity=0.7)
        score_old = compute_urgency_score(p_old, DEFAULT_HALF_LIFE_HOURS, now)
        score_fresh = compute_urgency_score(p_fresh, DEFAULT_HALF_LIFE_HOURS, now)
        assert score_old > score_fresh


# ── Sense (extended) ──────────────────────────────────────────────────────────────────


class TestSenseExtended:
    def test_sense_filter_emitter(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "src", "a", "dev")
        emit_pheromone(board, "NEED", "src", "b", "qa")
        results = sense_pheromones(board, emitter="dev")
        assert len(results) == 1
        assert results[0][0].emitter == "dev"

    def test_sense_filter_emitter_case_insensitive(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "NEED", "src", "a", "DEV")
        results = sense_pheromones(board, emitter="dev")
        assert len(results) == 1

    def test_sense_include_resolved(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "src", "done", "dev")
        resolve_pheromone(board, p.pheromone_id, "qa")
        assert len(sense_pheromones(board)) == 0
        results = sense_pheromones(board, include_resolved=True)
        assert len(results) == 1
        assert results[0][0].resolved is True

    def test_sense_include_resolved_false_by_default(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "ALERT", "src", "warn", "dev")
        resolve_pheromone(board, p.pheromone_id, "qa")
        results = sense_pheromones(board)
        assert len(results) == 0


# ── Trails (extended) ───────────────────────────────────────────────────────────────


class TestTrailsExtended:
    def test_cold_zone_detected(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "src/old", "done", "dev")
        resolve_pheromone(board, p.pheromone_id, "qa")
        patterns = analyze_trails(board)
        cold = [pt for pt in patterns if pt.pattern_type == "cold-zone"]
        assert len(cold) == 1
        assert cold[0].location == "src/old"

    def test_cold_zone_not_detected_if_still_active(self, board: PheromoneBoard) -> None:
        p = emit_pheromone(board, "NEED", "src/active", "still going", "dev")
        resolve_pheromone(board, p.pheromone_id, "qa")
        emit_pheromone(board, "PROGRESS", "src/active", "continuing", "dev")
        patterns = analyze_trails(board)
        cold = [pt for pt in patterns if pt.pattern_type == "cold-zone"]
        assert len(cold) == 0

    def test_relay_detected(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "COMPLETE", "src/api", "v1 done", "dev")
        emit_pheromone(board, "NEED", "src/api", "review v1", "qa")
        patterns = analyze_trails(board)
        relays = [pt for pt in patterns if pt.pattern_type == "relay"]
        assert len(relays) >= 1
        assert "dev" in relays[0].involved_agents
        assert "qa" in relays[0].involved_agents

    def test_relay_requires_different_agents(self, board: PheromoneBoard) -> None:
        emit_pheromone(board, "COMPLETE", "src/api", "done", "dev")
        emit_pheromone(board, "NEED", "src/api", "more work", "dev")  # same agent
        patterns = analyze_trails(board)
        relays = [pt for pt in patterns if pt.pattern_type == "relay"]
        assert len(relays) == 0

    def test_deduplication(self, board: PheromoneBoard) -> None:
        """Same pattern_type + location should appear only once."""
        for i in range(5):
            emit_pheromone(board, "ALERT", "src/shared", f"msg {i}", "dev")
        patterns = analyze_trails(board)
        hot = [pt for pt in patterns if pt.pattern_type == "hot-zone"
               and pt.location == "src/shared"]
        assert len(hot) == 1


# ── Bulk Deposit ───────────────────────────────────────────────────────────────────


class TestBulkDeposit:
    def test_deposits_multiple_signals(self, root: Path) -> None:
        signals = [
            {"ptype": "ALERT", "location": "src/a", "text": "issue a", "emitter": "dev"},
            {"ptype": "NEED",  "location": "src/b", "text": "help b",  "emitter": "qa"},
            {"ptype": "BLOCK", "location": "src/c", "text": "stuck c", "emitter": "dev"},
        ]
        count = bulk_deposit(root, signals)
        assert count == 3
        board = load_board(root)
        assert len(board.pheromones) == 3

    def test_is_atomic_single_write(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """save_board should be called exactly once regardless of signal count."""
        save_calls: list[int] = []
        original_save = save_board

        def counting_save(pr: Path, b: PheromoneBoard) -> None:
            save_calls.append(1)
            original_save(pr, b)

        monkeypatch.setattr(
            "grimoire.tools.stigmergy.save_board", counting_save)
        bulk_deposit(root, [
            {"ptype": "NEED", "location": "x", "text": "a", "emitter": "dev"},
            {"ptype": "NEED", "location": "y", "text": "b", "emitter": "dev"},
        ])
        assert len(save_calls) == 1

    def test_deduplicates_within_batch(self, root: Path) -> None:
        signals = [
            {"ptype": "ALERT", "location": "src", "text": "fire", "emitter": "tool-a"},
            {"ptype": "ALERT", "location": "src", "text": "fire", "emitter": "tool-b"},
        ]
        count = bulk_deposit(root, signals)
        assert count == 2
        board = load_board(root)
        assert len(board.pheromones) == 1  # deduplicated
        assert board.pheromones[0].reinforcements == 1

    def test_empty_signals_returns_zero(self, root: Path) -> None:
        assert bulk_deposit(root, []) == 0

    def test_returns_zero_on_bad_signal(self, root: Path) -> None:
        # Missing required key 'ptype'
        result = bulk_deposit(root, [{"location": "x", "text": "y", "emitter": "z"}])
        assert result == 0
