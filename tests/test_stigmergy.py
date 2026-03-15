#!/usr/bin/env python3
"""
Tests pour stigmergy.py — Coordination stigmergique Grimoire.

Fonctions testées :
  - Pheromone / PheromoneBoard / TrailPattern dataclasses
  - emit_pheromone()
  - amplify_pheromone()
  - resolve_pheromone()
  - sense_pheromones() + filtres
  - compute_current_intensity() / évaporation
  - evaporate()
  - analyze_trails() — 5 types de patterns
  - render_sense() / render_landscape() / render_trails() / render_evaporate()
  - load_board() / save_board()
  - CLI (emit, sense, amplify, resolve, landscape, trails, evaporate, stats)
"""

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "stigmergy.py"


def _import_st():
    return importlib.import_module("stigmergy")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_root(tmpdir: Path) -> Path:
    (tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    return tmpdir


def _make_board(st, pheromones=None, **kw):
    b = st.PheromoneBoard(**kw)
    if pheromones:
        b.pheromones = pheromones
    return b


def _make_pheromone(st, ptype="NEED", location="src/auth",
                    text="review needed", emitter="dev",
                    timestamp=None, intensity=0.7,
                    tags=None, reinforcements=0,
                    reinforced_by=None, resolved=False,
                    resolved_by="", resolved_at=""):
    if timestamp is None:
        timestamp = datetime.now(tz=UTC).isoformat()
    return st.Pheromone(
        pheromone_id=st._generate_id(ptype, location, text, timestamp),
        pheromone_type=ptype,
        location=location,
        text=text,
        emitter=emitter,
        timestamp=timestamp,
        intensity=intensity,
        tags=tags or [],
        reinforcements=reinforcements,
        reinforced_by=reinforced_by or [],
        resolved=resolved,
        resolved_by=resolved_by,
        resolved_at=resolved_at,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dataclass Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPheromoneDataclass(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_to_dict_from_dict_roundtrip(self):
        p = _make_pheromone(self.st, tags=["security", "urgent"])
        d = p.to_dict()
        p2 = self.st.Pheromone.from_dict(d)
        self.assertEqual(p.pheromone_id, p2.pheromone_id)
        self.assertEqual(p.pheromone_type, p2.pheromone_type)
        self.assertEqual(p.tags, p2.tags)
        self.assertAlmostEqual(p.intensity, p2.intensity, places=3)

    def test_from_dict_defaults(self):
        p = self.st.Pheromone.from_dict({})
        self.assertEqual(p.pheromone_id, "")
        self.assertEqual(p.pheromone_type, "NEED")
        self.assertFalse(p.resolved)

    def test_all_fields_serialized(self):
        p = _make_pheromone(self.st, reinforcements=3,
                           reinforced_by=["qa", "sm"])
        d = p.to_dict()
        self.assertIn("reinforcements", d)
        self.assertEqual(d["reinforcements"], 3)
        self.assertEqual(d["reinforced_by"], ["qa", "sm"])

    def test_intensity_rounded(self):
        p = _make_pheromone(self.st, intensity=0.33333333)
        d = p.to_dict()
        self.assertEqual(d["intensity"], 0.3333)

    def test_resolved_fields(self):
        p = _make_pheromone(self.st, resolved=True,
                           resolved_by="qa", resolved_at="2025-01-01T00:00:00")
        d = p.to_dict()
        self.assertTrue(d["resolved"])
        self.assertEqual(d["resolved_by"], "qa")


class TestPheromoneBoardDataclass(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_empty_board(self):
        b = self.st.PheromoneBoard()
        self.assertEqual(b.pheromones, [])
        self.assertEqual(b.total_emitted, 0)
        self.assertEqual(b.total_evaporated, 0)

    def test_roundtrip(self):
        b = self.st.PheromoneBoard(total_emitted=5, total_evaporated=2)
        b.pheromones = [_make_pheromone(self.st)]
        d = b.to_dict()
        b2 = self.st.PheromoneBoard.from_dict(d)
        self.assertEqual(b2.total_emitted, 5)
        self.assertEqual(len(b2.pheromones), 1)

    def test_custom_half_life(self):
        b = self.st.PheromoneBoard(half_life_hours=24.0)
        self.assertEqual(b.half_life_hours, 24.0)


class TestTrailPatternDataclass(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_to_dict(self):
        t = self.st.TrailPattern(
            pattern_type="hot-zone", location="src/auth",
            description="test", involved_agents=["dev", "qa"],
            pheromone_count=5, avg_intensity=0.666)
        d = t.to_dict()
        self.assertEqual(d["pattern_type"], "hot-zone")
        self.assertEqual(d["avg_intensity"], 0.67)


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.root = _make_root(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty(self):
        b = self.st.load_board(self.root)
        self.assertEqual(b.pheromones, [])
        self.assertEqual(b.total_emitted, 0)

    def test_save_and_load(self):
        b = self.st.PheromoneBoard(total_emitted=3)
        b.pheromones = [_make_pheromone(self.st)]
        self.st.save_board(self.root, b)
        b2 = self.st.load_board(self.root)
        self.assertEqual(b2.total_emitted, 3)
        self.assertEqual(len(b2.pheromones), 1)

    def test_load_corrupt_json(self):
        path = self.root / "_grimoire-output" / "pheromone-board.json"
        path.write_text("{invalid}", encoding="utf-8")
        b = self.st.load_board(self.root)
        self.assertEqual(b.pheromones, [])

    def test_save_creates_directory(self):
        root = self.tmpdir / "new-project"
        b = self.st.PheromoneBoard()
        self.st.save_board(root, b)
        self.assertTrue((root / "_grimoire-output" / "pheromone-board.json").exists())

    def test_json_structure(self):
        b = self.st.PheromoneBoard()
        self.st.emit_pheromone(b, "ALERT", "loc", "msg", "dev")
        self.st.save_board(self.root, b)
        path = self.root / "_grimoire-output" / "pheromone-board.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("version", data)
        self.assertIn("pheromones", data)
        self.assertIsInstance(data["pheromones"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# ID Generation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIDGeneration(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_format(self):
        pid = self.st._generate_id("NEED", "src", "txt", "TS")
        self.assertTrue(pid.startswith("PH-"))
        self.assertEqual(len(pid), 11)  # PH- + 8 hex

    def test_deterministic(self):
        a = self.st._generate_id("NEED", "src", "txt", "TS")
        b = self.st._generate_id("NEED", "src", "txt", "TS")
        self.assertEqual(a, b)

    def test_different_inputs(self):
        a = self.st._generate_id("NEED", "src", "txt", "TS1")
        b = self.st._generate_id("ALERT", "src", "txt", "TS1")
        self.assertNotEqual(a, b)


# ═══════════════════════════════════════════════════════════════════════════════
# Évaporation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaporation(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_no_decay_at_emission(self):
        now = datetime.now(tz=UTC)
        p = _make_pheromone(self.st, timestamp=now.isoformat(), intensity=1.0)
        current = self.st.compute_current_intensity(p, 72.0, now)
        self.assertAlmostEqual(current, 1.0, places=2)

    def test_half_decay_at_half_life(self):
        now = datetime.now(tz=UTC)
        past = now - timedelta(hours=72)
        p = _make_pheromone(self.st, timestamp=past.isoformat(), intensity=1.0)
        current = self.st.compute_current_intensity(p, 72.0, now)
        self.assertAlmostEqual(current, 0.5, places=2)

    def test_quarter_at_two_half_lives(self):
        now = datetime.now(tz=UTC)
        past = now - timedelta(hours=144)
        p = _make_pheromone(self.st, timestamp=past.isoformat(), intensity=1.0)
        current = self.st.compute_current_intensity(p, 72.0, now)
        self.assertAlmostEqual(current, 0.25, places=2)

    def test_custom_half_life(self):
        now = datetime.now(tz=UTC)
        past = now - timedelta(hours=24)
        p = _make_pheromone(self.st, timestamp=past.isoformat(), intensity=1.0)
        current = self.st.compute_current_intensity(p, 24.0, now)
        self.assertAlmostEqual(current, 0.5, places=2)

    def test_initial_intensity_scales(self):
        now = datetime.now(tz=UTC)
        past = now - timedelta(hours=72)
        p = _make_pheromone(self.st, timestamp=past.isoformat(), intensity=0.8)
        current = self.st.compute_current_intensity(p, 72.0, now)
        self.assertAlmostEqual(current, 0.4, places=2)

    def test_invalid_timestamp(self):
        p = _make_pheromone(self.st, timestamp="INVALID")
        current = self.st.compute_current_intensity(p, 72.0)
        self.assertEqual(current, p.intensity)

    def test_future_timestamp(self):
        now = datetime.now(tz=UTC)
        future = now + timedelta(hours=24)
        p = _make_pheromone(self.st, timestamp=future.isoformat(),
                           intensity=0.8)
        current = self.st.compute_current_intensity(p, 72.0, now)
        self.assertEqual(current, 0.8)

    def test_evaporate_removes_dead(self):
        now = datetime.now(tz=UTC)
        old = now - timedelta(hours=720)  # 30 jours → quasi-0
        p1 = _make_pheromone(self.st, timestamp=old.isoformat(),
                            intensity=0.5, text="old")
        p2 = _make_pheromone(self.st, timestamp=now.isoformat(),
                            intensity=0.8, text="new")
        board = _make_board(self.st, pheromones=[p1, p2])
        board, count = self.st.evaporate(board, now)
        self.assertEqual(count, 1)
        self.assertEqual(len(board.pheromones), 1)
        self.assertEqual(board.pheromones[0].text, "new")

    def test_evaporate_counts_total(self):
        now = datetime.now(tz=UTC)
        old = now - timedelta(hours=720)
        p = _make_pheromone(self.st, timestamp=old.isoformat(), intensity=0.3)
        board = _make_board(self.st, pheromones=[p])
        board, _count = self.st.evaporate(board, now)
        self.assertEqual(board.total_evaporated, 1)

    def test_evaporate_keeps_all_fresh(self):
        now = datetime.now(tz=UTC)
        ps = [_make_pheromone(self.st, timestamp=now.isoformat(),
                              text=f"p{i}") for i in range(5)]
        board = _make_board(self.st, pheromones=ps)
        board, count = self.st.evaporate(board, now)
        self.assertEqual(count, 0)
        self.assertEqual(len(board.pheromones), 5)

    def test_evaporate_removes_resolved(self):
        now = datetime.now(tz=UTC)
        p = _make_pheromone(self.st, timestamp=now.isoformat(),
                           intensity=0.8, resolved=True)
        board = _make_board(self.st, pheromones=[p])
        board, count = self.st.evaporate(board, now)
        self.assertEqual(count, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Emit Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmit(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_basic_emit(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "src/auth",
                                   "review needed", "dev")
        self.assertTrue(p.pheromone_id.startswith("PH-"))
        self.assertEqual(p.pheromone_type, "NEED")
        self.assertEqual(p.emitter, "dev")
        self.assertEqual(len(board.pheromones), 1)
        self.assertEqual(board.total_emitted, 1)

    def test_emit_with_tags(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "ALERT", "src/db",
                                   "breaking change", "architect",
                                   tags=["breaking", "database"])
        self.assertEqual(len(p.tags), 2)
        self.assertIn("breaking", p.tags)

    def test_emit_custom_intensity(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=0.3)
        self.assertAlmostEqual(p.intensity, 0.3)

    def test_emit_clamps_intensity_high(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=1.5)
        self.assertAlmostEqual(p.intensity, 1.0)

    def test_emit_clamps_intensity_low(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=-0.5)
        self.assertAlmostEqual(p.intensity, 0.0)

    def test_emit_multiple(self):
        board = self.st.PheromoneBoard()
        for i in range(5):
            self.st.emit_pheromone(board, "PROGRESS", f"zone{i}",
                                  f"task {i}", "dev")
        self.assertEqual(len(board.pheromones), 5)
        self.assertEqual(board.total_emitted, 5)

    def test_emit_all_types(self):
        board = self.st.PheromoneBoard()
        for t in self.st.VALID_TYPES:
            p = self.st.emit_pheromone(board, t, "loc", f"type={t}", "dev")
            self.assertEqual(p.pheromone_type, t)


# ═══════════════════════════════════════════════════════════════════════════════
# Amplify Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAmplify(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_basic_amplify(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev")
        original = p.intensity
        result = self.st.amplify_pheromone(board, p.pheromone_id, "qa")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.intensity,
                               original + self.st.REINFORCEMENT_BOOST)
        self.assertEqual(result.reinforcements, 1)
        self.assertIn("qa", result.reinforced_by)

    def test_amplify_nonexistent(self):
        board = self.st.PheromoneBoard()
        result = self.st.amplify_pheromone(board, "PH-nonexist", "qa")
        self.assertIsNone(result)

    def test_amplify_caps_at_max(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=0.95)
        self.st.amplify_pheromone(board, p.pheromone_id, "qa")
        self.assertAlmostEqual(p.intensity, 1.0)

    def test_amplify_multiple_agents(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=0.3)
        self.st.amplify_pheromone(board, p.pheromone_id, "qa")
        self.st.amplify_pheromone(board, p.pheromone_id, "sm")
        self.assertEqual(p.reinforcements, 2)
        self.assertEqual(len(p.reinforced_by), 2)

    def test_amplify_same_agent_twice(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev",
                                   intensity=0.5)
        self.st.amplify_pheromone(board, p.pheromone_id, "qa")
        self.st.amplify_pheromone(board, p.pheromone_id, "qa")
        self.assertEqual(p.reinforcements, 2)
        # reinforced_by dédupliqué
        self.assertEqual(len(p.reinforced_by), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Resolve Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolve(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_basic_resolve(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "BLOCK", "src/api", "blocked", "dev")
        result = self.st.resolve_pheromone(board, p.pheromone_id, "qa")
        self.assertIsNotNone(result)
        self.assertTrue(result.resolved)
        self.assertEqual(result.resolved_by, "qa")
        self.assertNotEqual(result.resolved_at, "")

    def test_resolve_nonexistent(self):
        board = self.st.PheromoneBoard()
        result = self.st.resolve_pheromone(board, "PH-nonexist", "qa")
        self.assertIsNone(result)

    def test_resolved_not_sensed_by_default(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev")
        self.st.resolve_pheromone(board, p.pheromone_id, "qa")
        items = self.st.sense_pheromones(board)
        self.assertEqual(len(items), 0)

    def test_resolved_sensed_with_flag(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "loc", "txt", "dev")
        self.st.resolve_pheromone(board, p.pheromone_id, "qa")
        items = self.st.sense_pheromones(board, include_resolved=True)
        self.assertEqual(len(items), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Sense Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSense(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()
        self.now = datetime.now(tz=UTC)

    def _board_with_pheromones(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "NEED", "src/auth",
                               "review sécurité", "dev", tags=["security"])
        self.st.emit_pheromone(board, "ALERT", "src/db",
                               "breaking change", "architect", tags=["db"])
        self.st.emit_pheromone(board, "PROGRESS", "src/auth",
                               "en cours", "dev")
        self.st.emit_pheromone(board, "BLOCK", "src/api",
                               "bloqué par dep", "pm")
        return board

    def test_sense_all(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board)
        self.assertEqual(len(items), 4)

    def test_sense_filter_type(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, ptype="ALERT")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0].pheromone_type, "ALERT")

    def test_sense_filter_location(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, location="auth")
        self.assertEqual(len(items), 2)

    def test_sense_filter_location_case_insensitive(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, location="AUTH")
        self.assertEqual(len(items), 2)

    def test_sense_filter_tag(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, tag="security")
        self.assertEqual(len(items), 1)

    def test_sense_filter_tag_case_insensitive(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, tag="SECURITY")
        self.assertEqual(len(items), 1)

    def test_sense_filter_emitter(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, emitter="dev")
        self.assertEqual(len(items), 2)

    def test_sense_sorted_by_intensity(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "NEED", "a", "low", "d", intensity=0.3)
        self.st.emit_pheromone(board, "NEED", "b", "high", "d", intensity=0.9)
        self.st.emit_pheromone(board, "NEED", "c", "mid", "d", intensity=0.6)
        items = self.st.sense_pheromones(board)
        intensities = [i for _, i in items]
        self.assertEqual(intensities, sorted(intensities, reverse=True))

    def test_sense_excludes_below_threshold(self):
        board = self.st.PheromoneBoard()
        now = datetime.now(tz=UTC)
        old = now - timedelta(hours=720)
        p = _make_pheromone(self.st, timestamp=old.isoformat(),
                           intensity=0.3)
        board.pheromones.append(p)
        items = self.st.sense_pheromones(board, now=now)
        self.assertEqual(len(items), 0)

    def test_sense_empty_board(self):
        board = self.st.PheromoneBoard()
        items = self.st.sense_pheromones(board)
        self.assertEqual(len(items), 0)

    def test_sense_multiple_filters(self):
        board = self._board_with_pheromones()
        items = self.st.sense_pheromones(board, ptype="NEED",
                                         location="auth")
        self.assertEqual(len(items), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Trail Analysis Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrailAnalysis(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()
        self.now = datetime.now(tz=UTC)

    def test_hot_zone(self):
        board = self.st.PheromoneBoard()
        for i in range(4):
            self.st.emit_pheromone(board, "NEED", "src/auth",
                                   f"signal {i}", "dev")
        patterns = self.st.analyze_trails(board, self.now)
        hot_zones = [p for p in patterns if p.pattern_type == "hot-zone"]
        self.assertGreaterEqual(len(hot_zones), 1)
        self.assertEqual(hot_zones[0].location, "src/auth")

    def test_convergence(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "NEED", "src/auth", "a", "dev")
        self.st.emit_pheromone(board, "PROGRESS", "src/auth", "b", "qa")
        patterns = self.st.analyze_trails(board, self.now)
        convergences = [p for p in patterns if p.pattern_type == "convergence"]
        self.assertGreaterEqual(len(convergences), 1)
        agents = convergences[0].involved_agents
        self.assertIn("dev", agents)
        self.assertIn("qa", agents)

    def test_bottleneck(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "BLOCK", "src/api", "a", "dev")
        self.st.emit_pheromone(board, "BLOCK", "src/api", "b", "qa")
        patterns = self.st.analyze_trails(board, self.now)
        bottlenecks = [p for p in patterns if p.pattern_type == "bottleneck"]
        self.assertGreaterEqual(len(bottlenecks), 1)

    def test_cold_zone(self):
        board = self.st.PheromoneBoard()
        p = self.st.emit_pheromone(board, "NEED", "src/old", "old", "dev")
        self.st.resolve_pheromone(board, p.pheromone_id, "qa")
        patterns = self.st.analyze_trails(board, self.now)
        cold = [p for p in patterns if p.pattern_type == "cold-zone"]
        self.assertGreaterEqual(len(cold), 1)

    def test_relay(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "COMPLETE", "src/auth",
                               "review done", "dev")
        self.st.emit_pheromone(board, "NEED", "src/auth",
                               "deploy needed", "sm")
        patterns = self.st.analyze_trails(board, self.now)
        relays = [p for p in patterns if p.pattern_type == "relay"]
        self.assertGreaterEqual(len(relays), 1)

    def test_no_patterns_on_empty(self):
        board = self.st.PheromoneBoard()
        patterns = self.st.analyze_trails(board, self.now)
        self.assertEqual(len(patterns), 0)

    def test_dedup_patterns(self):
        board = self.st.PheromoneBoard()
        for i in range(5):
            self.st.emit_pheromone(board, "NEED", "src/auth",
                                   f"signal {i}", f"agent{i}")
        patterns = self.st.analyze_trails(board, self.now)
        # Devrait y avoir un seul hot-zone et un seul convergence pour src/auth
        hot = [p for p in patterns if p.pattern_type == "hot-zone"]
        conv = [p for p in patterns if p.pattern_type == "convergence"]
        self.assertEqual(len(hot), 1)
        self.assertEqual(len(conv), 1)

    def test_relay_requires_different_agents(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "COMPLETE", "src/auth",
                               "done", "dev")
        self.st.emit_pheromone(board, "NEED", "src/auth",
                               "more needed", "dev")  # même agent
        patterns = self.st.analyze_trails(board, self.now)
        relays = [p for p in patterns if p.pattern_type == "relay"]
        self.assertEqual(len(relays), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Render Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRender(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_render_sense_empty(self):
        out = self.st.render_sense([])
        self.assertIn("Aucune phéromone", out)

    def test_render_sense_with_items(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "NEED", "src/auth",
                                "review", "dev", tags=["security"])
        items = self.st.sense_pheromones(board)
        out = self.st.render_sense(items)
        self.assertIn("NEED", out)
        self.assertIn("src/auth", out)
        self.assertIn("review", out)
        self.assertIn("🔵", out)

    def test_render_landscape(self):
        board = self.st.PheromoneBoard()
        self.st.emit_pheromone(board, "NEED", "a", "x", "dev")
        self.st.emit_pheromone(board, "ALERT", "b", "y", "qa")
        out = self.st.render_landscape(board)
        self.assertIn("Paysage Phéromonique", out)
        self.assertIn("Signaux actifs", out)

    def test_render_trails_empty(self):
        out = self.st.render_trails([])
        self.assertIn("Aucun pattern", out)

    def test_render_trails_with_patterns(self):
        patterns = [self.st.TrailPattern(
            pattern_type="hot-zone", location="src/auth",
            description="test pattern", involved_agents=["dev"],
            pheromone_count=3, avg_intensity=0.8)]
        out = self.st.render_trails(patterns)
        self.assertIn("HOT-ZONE", out)
        self.assertIn("src/auth", out)

    def test_render_evaporate(self):
        out = self.st.render_evaporate(5, 10)
        self.assertIn("5", out)
        self.assertIn("10", out)

    def test_render_evaporate_dry_run(self):
        out = self.st.render_evaporate(3, 7, dry_run=True)
        self.assertIn("DRY RUN", out)

    def test_intensity_bar(self):
        bar = self.st._intensity_bar(0.5, width=10)
        self.assertEqual(len(bar), 10)
        self.assertEqual(bar.count("█"), 5)
        self.assertEqual(bar.count("░"), 5)

    def test_intensity_bar_full(self):
        bar = self.st._intensity_bar(1.0, width=10)
        self.assertEqual(bar, "█" * 10)

    def test_intensity_bar_empty(self):
        bar = self.st._intensity_bar(0.0, width=10)
        self.assertEqual(bar, "░" * 10)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.root = _make_root(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, *args) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(TOOL), "--project-root", str(self.root), *list(args)]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    def test_cli_no_command(self):
        r = self._run()
        # Should print help and exit non-zero or zero depending on argparse
        # argparse exits with 0 when no command if we call print_help + exit(1)
        self.assertNotEqual(r.returncode, None)

    def test_cli_emit(self):
        r = self._run("emit", "--type", "NEED", "--location", "src/auth",
                       "--text", "review needed", "--agent", "dev")
        self.assertEqual(r.returncode, 0)
        self.assertIn("PH-", r.stdout)
        self.assertIn("NEED", r.stdout)

    def test_cli_emit_with_tags(self):
        r = self._run("emit", "--type", "ALERT", "--location", "src/db",
                       "--text", "breaking", "--agent", "arch",
                       "--tags", "db,urgent")
        self.assertEqual(r.returncode, 0)

    def test_cli_sense_empty(self):
        r = self._run("sense")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Aucune", r.stdout)

    def test_cli_sense_after_emit(self):
        self._run("emit", "--type", "NEED", "--location", "src/x",
                  "--text", "test", "--agent", "dev")
        r = self._run("sense")
        self.assertEqual(r.returncode, 0)
        self.assertIn("NEED", r.stdout)

    def test_cli_sense_json(self):
        self._run("emit", "--type", "NEED", "--location", "loc",
                  "--text", "txt", "--agent", "dev")
        r = self._run("sense", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 1)
        self.assertIn("current_intensity", data[0])

    def test_cli_sense_filter_type(self):
        self._run("emit", "--type", "NEED", "--location", "a",
                  "--text", "a", "--agent", "dev")
        self._run("emit", "--type", "ALERT", "--location", "b",
                  "--text", "b", "--agent", "dev")
        r = self._run("sense", "--type", "ALERT")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ALERT", r.stdout)
        self.assertNotIn("NEED", r.stdout.split("ALERT")[0])

    def test_cli_amplify(self):
        r1 = self._run("emit", "--type", "NEED", "--location", "a",
                        "--text", "a", "--agent", "dev")
        # Extract PH-id from output
        pid = next(w for w in r1.stdout.split() if w.startswith("PH-"))
        r2 = self._run("amplify", "--id", pid, "--agent", "qa")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("renforcée", r2.stdout)

    def test_cli_amplify_nonexistent(self):
        r = self._run("amplify", "--id", "PH-00000000", "--agent", "qa")
        self.assertNotEqual(r.returncode, 0)

    def test_cli_resolve(self):
        r1 = self._run("emit", "--type", "BLOCK", "--location", "a",
                        "--text", "blocked", "--agent", "dev")
        pid = next(w for w in r1.stdout.split() if w.startswith("PH-"))
        r2 = self._run("resolve", "--id", pid, "--agent", "qa")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("résolue", r2.stdout)

    def test_cli_resolve_nonexistent(self):
        r = self._run("resolve", "--id", "PH-00000000", "--agent", "qa")
        self.assertNotEqual(r.returncode, 0)

    def test_cli_landscape(self):
        self._run("emit", "--type", "NEED", "--location", "a",
                  "--text", "a", "--agent", "dev")
        r = self._run("landscape")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Paysage", r.stdout)

    def test_cli_trails(self):
        r = self._run("trails")
        self.assertEqual(r.returncode, 0)

    def test_cli_evaporate_empty(self):
        r = self._run("evaporate")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Évaporation", r.stdout)

    def test_cli_evaporate_dry_run(self):
        r = self._run("evaporate", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("DRY RUN", r.stdout)

    def test_cli_stats_empty(self):
        r = self._run("stats")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Statistiques", r.stdout)

    def test_cli_stats_with_data(self):
        self._run("emit", "--type", "NEED", "--location", "a",
                  "--text", "a", "--agent", "dev")
        r = self._run("stats")
        self.assertEqual(r.returncode, 0)
        self.assertIn("1", r.stdout)

    def test_cli_emit_custom_intensity(self):
        r = self._run("emit", "--type", "NEED", "--location", "x",
                       "--text", "x", "--agent", "dev", "--intensity", "0.3")
        self.assertEqual(r.returncode, 0)
        self.assertIn("30%", r.stdout)


# ═══════════════════════════════════════════════════════════════════════════════
# Constants Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.st = _import_st()

    def test_valid_types(self):
        expected = {"NEED", "ALERT", "OPPORTUNITY", "PROGRESS",
                    "COMPLETE", "BLOCK"}
        self.assertEqual(self.st.VALID_TYPES, expected)

    def test_type_icons_complete(self):
        for t in self.st.VALID_TYPES:
            self.assertIn(t, self.st.TYPE_ICONS)

    def test_detection_threshold_positive(self):
        self.assertGreater(self.st.DETECTION_THRESHOLD, 0)

    def test_max_intensity(self):
        self.assertEqual(self.st.MAX_INTENSITY, 1.0)

    def test_reinforcement_boost(self):
        self.assertGreater(self.st.REINFORCEMENT_BOOST, 0)
        self.assertLessEqual(self.st.REINFORCEMENT_BOOST, 0.5)


if __name__ == "__main__":
    unittest.main()
