#!/usr/bin/env python3
"""
Tests pour cognitive-flywheel.py — Boucle d'auto-amélioration continue BMAD.

Fonctions testées :
  - TraceEntry, Pattern, FlywheelScore, Correction, FlywheelReport (dataclasses)
  - load_report(), save_report(), load_history(), append_history()
  - parse_trace(), extract_patterns(), compute_score()
  - generate_corrections(), apply_gates()
  - render_scoreboard()
  - cmd_analyze(), cmd_report(), cmd_apply(), cmd_history(), cmd_score()
  - build_parser(), main()
"""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "cognitive-flywheel.py"


def _import_mod():
    mod_name = "cognitive_flywheel"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "cognitive-flywheel.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    (root / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
    (root / "_bmad-output").mkdir(parents=True, exist_ok=True)
    return root


def _write_trace(root: Path, lines: list[str]) -> None:
    """Write a fake BMAD_TRACE.md for testing."""
    trace_path = root / "_bmad-output" / "BMAD_TRACE.md"
    trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


SAMPLE_TRACE_LINES = [
    "[2026-03-01T10:00:00Z] [agent:dev] [GIT-COMMIT] story:US-01 — initial commit",
    "[2026-03-01T10:01:00Z] [agent:dev] [AC-PASS] story:US-01 — AC-1 unit tests pass",
    "[2026-03-01T10:02:00Z] [agent:dev] [FAILURE] story:US-01 — import crash on dataclass",
    "[2026-03-01T10:03:00Z] [agent:dev] [FAILURE] story:US-01 — import crash on dataclass",
    "[2026-03-01T10:04:00Z] [agent:dev] [FAILURE] story:US-01 — import crash on dataclass",
    "[2026-03-01T10:05:00Z] [agent:architect] [DECISION] story:US-02 — chose PostgreSQL",
    "[2026-03-01T10:06:00Z] [agent:dev] [GIT-COMMIT] story:US-02 — database migration",
    "[2026-03-01T10:07:00Z] [agent:qa] [AC-FAIL] story:US-02 — perf test timeout",
    "[2026-03-01T10:08:00Z] [agent:qa] [AC-FAIL] story:US-02 — perf test timeout",
    "[2026-03-01T10:09:00Z] [agent:qa] [AC-FAIL] story:US-02 — perf test timeout",
    "[2026-03-01T10:10:00Z] [agent:dev] [CHECKPOINT] story:US-02 — checkpoint before refactor",
    "[2026-03-01T10:11:00Z] [agent:dev] [REMEMBER] — learned about flywheel pattern",
]


# ── Dataclass tests ──────────────────────────────────────────────────────────


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_trace_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "TraceEntry"))

    def test_pattern_exists(self):
        self.assertTrue(hasattr(self.mod, "Pattern"))

    def test_pattern_defaults(self):
        p = self.mod.Pattern()
        self.assertEqual(p.status, "noise")
        self.assertEqual(p.severity, "low")

    def test_flywheel_score_exists(self):
        self.assertTrue(hasattr(self.mod, "FlywheelScore"))

    def test_flywheel_score_defaults(self):
        s = self.mod.FlywheelScore()
        self.assertEqual(s.trend, "stable")
        self.assertEqual(s.health_grade, "A")

    def test_correction_exists(self):
        self.assertTrue(hasattr(self.mod, "Correction"))

    def test_correction_defaults(self):
        c = self.mod.Correction()
        self.assertEqual(c.status, "pending")

    def test_flywheel_report_exists(self):
        self.assertTrue(hasattr(self.mod, "FlywheelReport"))


# ── Parse trace tests ────────────────────────────────────────────────────────


class TestParseTrace(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parse_empty(self):
        entries = self.mod.parse_trace(self.tmpdir)
        self.assertEqual(entries, [])

    def test_parse_sample(self):
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        entries = self.mod.parse_trace(self.tmpdir)
        self.assertEqual(len(entries), 12)

    def test_parse_since_filter(self):
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        entries = self.mod.parse_trace(self.tmpdir, since="2026-03-01T10:05")
        self.assertGreater(len(entries), 0)
        self.assertLess(len(entries), 12)

    def test_parse_entry_types(self):
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        entries = self.mod.parse_trace(self.tmpdir)
        types = {e.entry_type for e in entries}
        self.assertIn("GIT-COMMIT", types)
        self.assertIn("FAILURE", types)
        self.assertIn("AC-FAIL", types)


# ── Pattern extraction tests ─────────────────────────────────────────────────


class TestPatternExtraction(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.entries = self.mod.parse_trace(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extract_returns_list(self):
        patterns = self.mod.extract_patterns(self.entries)
        self.assertIsInstance(patterns, list)

    def test_extract_finds_failure_pattern(self):
        patterns = self.mod.extract_patterns(self.entries)
        failure_pats = [p for p in patterns if "Failure:" in p.description]
        self.assertGreater(len(failure_pats), 0)

    def test_extract_finds_ac_fail_pattern(self):
        patterns = self.mod.extract_patterns(self.entries)
        ac_pats = [p for p in patterns if "AC-FAIL:" in p.description]
        self.assertGreater(len(ac_pats), 0)

    def test_confirmed_has_3_occurrences(self):
        patterns = self.mod.extract_patterns(self.entries)
        confirmed = [p for p in patterns if p.status == "confirmed"]
        for p in confirmed:
            self.assertGreaterEqual(p.occurrences, 3)


# ── Scoring tests ────────────────────────────────────────────────────────────


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.entries = self.mod.parse_trace(self.tmpdir)
        self.patterns = self.mod.extract_patterns(self.entries)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_compute_score_returns_score(self):
        score = self.mod.compute_score(self.entries, self.patterns, [])
        self.assertIsInstance(score, self.mod.FlywheelScore)

    def test_score_has_grade(self):
        score = self.mod.compute_score(self.entries, self.patterns, [])
        self.assertIn(score.health_grade, ("A+", "A", "B", "C", "D"))

    def test_score_has_trend(self):
        score = self.mod.compute_score(self.entries, self.patterns, [])
        self.assertIn(score.trend, ("improving", "stable", "degrading"))

    def test_score_failure_rate(self):
        score = self.mod.compute_score(self.entries, self.patterns, [])
        self.assertGreater(score.failure_rate, 0)
        self.assertLessEqual(score.failure_rate, 1.0)

    def test_score_with_previous_improving(self):
        prev = [{"failure_rate": 0.8, "patterns_confirmed": 10}]
        score = self.mod.compute_score(self.entries, self.patterns, prev)
        self.assertEqual(score.trend, "improving")

    def test_score_empty_entries(self):
        score = self.mod.compute_score([], [], [])
        self.assertEqual(score.health_grade, "A+")
        self.assertEqual(score.failure_rate, 0.0)


# ── Correction & Gate tests ──────────────────────────────────────────────────


class TestCorrections(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        entries = self.mod.parse_trace(self.tmpdir)
        self.patterns = self.mod.extract_patterns(entries)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_corrections(self):
        corrections = self.mod.generate_corrections(self.patterns)
        self.assertIsInstance(corrections, list)

    def test_corrections_from_confirmed_only(self):
        corrections = self.mod.generate_corrections(self.patterns)
        for c in corrections:
            pat = next((p for p in self.patterns if p.pattern_id == c.pattern_id), None)
            self.assertIsNotNone(pat)
            self.assertEqual(pat.status, "confirmed")

    def test_apply_gates_max_limit(self):
        # Create many corrections
        corrections = []
        for i in range(10):
            corrections.append(self.mod.Correction(
                correction_id=f"C-{i}", severity="low",
                status="pending", target_file=f"file-{i}.py"
            ))
        result = self.mod.apply_gates(corrections, 3)
        pending = [c for c in result if c.status == "pending"]
        self.assertLessEqual(len(pending), 3)

    def test_apply_gates_medium_collision(self):
        # 2 medium corrections on same file → elevate to high
        corrections = [
            self.mod.Correction(correction_id="C-1", severity="medium",
                                status="pending", target_file="same.py"),
            self.mod.Correction(correction_id="C-2", severity="medium",
                                status="pending", target_file="same.py"),
        ]
        result = self.mod.apply_gates(corrections, 5)
        high = [c for c in result if c.status == "high-escalated"]
        self.assertEqual(len(high), 2)


# ── Report I/O tests ─────────────────────────────────────────────────────────


class TestReportIO(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_report_none(self):
        result = self.mod.load_report(self.tmpdir)
        self.assertIsNone(result)

    def test_save_and_load_report(self):
        report = self.mod.FlywheelReport(
            cycle_id="FW-test123",
            timestamp="2026-03-01T10:00:00Z",
            score=self.mod.FlywheelScore(health_grade="A"),
        )
        self.mod.save_report(self.tmpdir, report)
        loaded = self.mod.load_report(self.tmpdir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.cycle_id, "FW-test123")
        self.assertEqual(loaded.score.health_grade, "A")

    def test_history_empty(self):
        entries = self.mod.load_history(self.tmpdir)
        self.assertEqual(entries, [])

    def test_append_and_load_history(self):
        self.mod.append_history(self.tmpdir, {"cycle_id": "FW-1", "grade": "A"})
        self.mod.append_history(self.tmpdir, {"cycle_id": "FW-2", "grade": "B"})
        entries = self.mod.load_history(self.tmpdir)
        self.assertEqual(len(entries), 2)


# ── Scoreboard rendering tests ───────────────────────────────────────────────


class TestScoreboard(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_render_scoreboard(self):
        score = self.mod.FlywheelScore(
            cycle_id="FW-abc", health_grade="B", failure_rate=0.1,
            patterns_confirmed=2, patterns_watch=1, trend="improving",
        )
        patterns = [
            self.mod.Pattern(pattern_id="P-1", status="confirmed", severity="medium",
                             occurrences=4, description="test pattern"),
        ]
        md = self.mod.render_scoreboard(score, patterns)
        self.assertIn("BMAD Cognitive Flywheel", md)
        self.assertIn("B", md)
        self.assertIn("improving", md)
        self.assertIn("P-1", md)


# ── Command tests ─────────────────────────────────────────────────────────────


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cmd_analyze_empty(self):
        import argparse
        args = argparse.Namespace(since=None)
        rc = self.mod.cmd_analyze(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_analyze_with_data(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        args = argparse.Namespace(since=None)
        rc = self.mod.cmd_analyze(self.tmpdir, args)
        self.assertEqual(rc, 0)
        # Report should now exist
        report = self.mod.load_report(self.tmpdir)
        self.assertIsNotNone(report)

    def test_cmd_report_no_data(self):
        import argparse
        rc = self.mod.cmd_report(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 1)

    def test_cmd_report_with_data(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.mod.cmd_analyze(self.tmpdir, argparse.Namespace(since=None))
        rc = self.mod.cmd_report(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_apply_dry_run(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.mod.cmd_analyze(self.tmpdir, argparse.Namespace(since=None))
        args = argparse.Namespace(max=5, dry_run=True)
        rc = self.mod.cmd_apply(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_apply_real(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.mod.cmd_analyze(self.tmpdir, argparse.Namespace(since=None))
        args = argparse.Namespace(max=5, dry_run=False)
        rc = self.mod.cmd_apply(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_history_empty(self):
        import argparse
        rc = self.mod.cmd_history(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_score_no_data(self):
        import argparse
        rc = self.mod.cmd_score(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 1)

    def test_cmd_score_with_data(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.mod.cmd_analyze(self.tmpdir, argparse.Namespace(since=None))
        rc = self.mod.cmd_score(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_dashboard_json(self):
        import argparse
        _write_trace(self.tmpdir, SAMPLE_TRACE_LINES)
        self.mod.cmd_analyze(self.tmpdir, argparse.Namespace(since=None))
        args = argparse.Namespace(json=True)
        rc = self.mod.cmd_dashboard(self.tmpdir, args)
        self.assertEqual(rc, 0)


# ── CLI parser tests ─────────────────────────────────────────────────────────


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        p = self.mod.build_parser()
        self.assertIsNotNone(p)

    def test_parse_analyze(self):
        p = self.mod.build_parser()
        args = p.parse_args(["analyze"])
        self.assertEqual(args.command, "analyze")

    def test_parse_analyze_since(self):
        p = self.mod.build_parser()
        args = p.parse_args(["analyze", "--since", "2026-01-01"])
        self.assertEqual(args.since, "2026-01-01")

    def test_parse_apply_dry_run(self):
        p = self.mod.build_parser()
        args = p.parse_args(["apply", "--dry-run", "--max", "3"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.max, 3)

    def test_parse_dashboard_json(self):
        p = self.mod.build_parser()
        args = p.parse_args(["dashboard", "--json"])
        self.assertTrue(args.json)


# ── CLI integration test ─────────────────────────────────────────────────────


class TestCLIIntegration(unittest.TestCase):
    def test_version(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("cognitive-flywheel", r.stdout.lower() + r.stderr.lower())

    def test_analyze_on_empty_project(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            _make_project(tmpdir)
            r = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmpdir), "analyze"],
                capture_output=True, text=True, timeout=30
            )
            self.assertEqual(r.returncode, 0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Constants tests ──────────────────────────────────────────────────────────


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version(self):
        self.assertIsInstance(self.mod.VERSION, str)

    def test_thresholds(self):
        self.assertEqual(self.mod.THRESHOLD_NOISE, 1)
        self.assertEqual(self.mod.THRESHOLD_WATCH, 2)
        self.assertEqual(self.mod.THRESHOLD_CONFIRMED, 3)

    def test_max_corrections(self):
        self.assertEqual(self.mod.MAX_AUTO_CORRECTIONS, 5)

    def test_severities(self):
        self.assertIn("low", self.mod.SEVERITIES)
        self.assertIn("high", self.mod.SEVERITIES)

    def test_trace_regex(self):
        self.assertIsNotNone(self.mod.TRACE_RE)


if __name__ == "__main__":
    unittest.main()
