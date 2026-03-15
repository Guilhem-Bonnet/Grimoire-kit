#!/usr/bin/env python3
"""
Tests pour antifragile-score.py — Score d'Anti-Fragilité Grimoire.

Fonctions testées :
  - _count_entries()
  - _count_failure_sections()
  - _count_contradictions()
  - _count_sil_signals()
  - _count_learnings()
  - _count_decisions()
  - score_recovery()
  - score_learning_velocity()
  - score_contradiction_resolution()
  - score_signal_trend()
  - score_decision_quality()
  - score_pattern_recurrence()
  - compute_antifragile_score()
  - save_score() / load_history()
  - render_report()
  - render_trend()
  - DimensionScore, AntifragileResult dataclasses
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_af():
    return importlib.import_module("antifragile-score".replace("-", "_"))


# Workaround: le module porte un tiret — import via importlib
def _import_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "antifragile_score",
        KIT_DIR / "framework" / "tools" / "antifragile-score.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_memory_tree(root, failures=None, contradictions=None,
                        decisions=None, learnings=None):
    """Créer un arbre mémoire minimal."""
    mem = root / "_grimoire" / "_memory"
    mem.mkdir(parents=True, exist_ok=True)

    if failures:
        (mem / "failure-museum.md").write_text(failures, encoding="utf-8")
    if contradictions:
        (mem / "contradiction-log.md").write_text(contradictions, encoding="utf-8")
    if decisions:
        (mem / "decisions-log.md").write_text(decisions, encoding="utf-8")

    if learnings:
        ld = mem / "agent-learnings"
        ld.mkdir(exist_ok=True)
        for name, content in learnings.items():
            (ld / name).write_text(content, encoding="utf-8")

    # Output dir
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    return mem


# ── Test DimensionScore ───────────────────────────────────────────────────────

class TestDimensionScore(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_defaults(self):
        ds = self.mod.DimensionScore(
            name="test", score=0.5, weight=0.2, weighted=0.1,
            evidence_count=3, details="desc",
        )
        self.assertEqual(ds.name, "test")
        self.assertEqual(ds.score, 0.5)
        self.assertEqual(ds.recommendations, [])

    def test_with_recommendations(self):
        ds = self.mod.DimensionScore(
            name="test", score=0.1, weight=0.15, weighted=0.015,
            evidence_count=1, details="d",
            recommendations=["Fix ceci", "Fix cela"],
        )
        self.assertEqual(len(ds.recommendations), 2)


class TestAntifragileResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_defaults(self):
        r = self.mod.AntifragileResult(
            timestamp="2026-01-01T00:00:00",
            global_score=42.5,
            level="ROBUST",
            dimensions=[],
            total_evidence=0,
            summary="test",
        )
        self.assertEqual(r.global_score, 42.5)
        self.assertEqual(r.level, "ROBUST")
        self.assertIsNone(r.since)


# ── Test _count_entries ───────────────────────────────────────────────────────

class TestCountEntries(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_file(self):
        f = self.tmpdir / "test.md"
        f.write_text("", encoding="utf-8")
        result = self.mod._count_entries(f)
        self.assertEqual(len(result), 0)

    def test_missing_file(self):
        result = self.mod._count_entries(self.tmpdir / "nope.md")
        self.assertEqual(len(result), 0)

    def test_parses_entries(self):
        f = self.tmpdir / "test.md"
        f.write_text(
            "# Header\n"
            "- [2026-01-15] Entry one\n"
            "- [2026-02-01] Entry two\n"
            "* [2026-02-10] Entry three\n",
            encoding="utf-8",
        )
        result = self.mod._count_entries(f)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0][0], "2026-01-15")
        self.assertEqual(result[1][0], "2026-02-01")

    def test_filters_by_since(self):
        f = self.tmpdir / "test.md"
        f.write_text(
            "- [2025-12-01] Old\n"
            "- [2026-02-01] New\n",
            encoding="utf-8",
        )
        result = self.mod._count_entries(f, since="2026-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "2026-02-01")

    def test_handles_entries_without_date(self):
        f = self.tmpdir / "test.md"
        f.write_text("- No date here\n", encoding="utf-8")
        result = self.mod._count_entries(f)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "")

    def test_skips_headers(self):
        f = self.tmpdir / "test.md"
        f.write_text("# Header line\n## Sub\n\n", encoding="utf-8")
        result = self.mod._count_entries(f)
        self.assertEqual(len(result), 0)


# ── Test _count_failure_sections ──────────────────────────────────────────────

class TestCountFailureSections(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_museum(self):
        f = self.tmpdir / "fm.md"
        f.write_text("# Failure Museum\n", encoding="utf-8")
        r = self.mod._count_failure_sections(f)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["with_rule"], 0)
        self.assertEqual(r["with_lesson"], 0)

    def test_missing_file(self):
        r = self.mod._count_failure_sections(self.tmpdir / "nope.md")
        self.assertEqual(r["total"], 0)

    def test_counts_entries_with_rules(self):
        f = self.tmpdir / "fm.md"
        f.write_text(
            "## Top Erreurs Critiques 🔴\n"
            "### [2026-01-01] CC-FAIL — Oubli de test\n"
            "- Leçon : toujours tester\n"
            "- Règle instaurée : test obligatoire\n"
            "\n"
            "### [2026-01-10] WRONG-ASSUMPTION — Mauvaise hyp\n"
            "- Leçon : vérifier\n",
            encoding="utf-8",
        )
        r = self.mod._count_failure_sections(f)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["with_rule"], 1)
        self.assertEqual(r["with_lesson"], 2)
        self.assertIn("CC-FAIL", r["categories"])
        self.assertIn("WRONG-ASSUMPTION", r["categories"])

    def test_severity_tracking(self):
        f = self.tmpdir / "fm.md"
        f.write_text(
            "## Top Erreurs Critiques 🔴\n"
            "### [2026-01-01] CC-FAIL — err1\n"
            "\n"
            "## Erreurs Importantes 🟡\n"
            "### [2026-01-02] HALLUCINATION — err2\n",
            encoding="utf-8",
        )
        r = self.mod._count_failure_sections(f)
        self.assertEqual(r["total"], 2)
        # Severity counts depend on section parsing
        self.assertGreaterEqual(r["critical"] + r["important"], 2)

    def test_since_filter(self):
        f = self.tmpdir / "fm.md"
        f.write_text(
            "### [2025-06-01] CC-FAIL — old\n"
            "### [2026-03-01] CC-FAIL — new\n",
            encoding="utf-8",
        )
        r = self.mod._count_failure_sections(f, since="2026-01-01")
        self.assertEqual(r["total"], 1)


# ── Test _count_contradictions ────────────────────────────────────────────────

class TestCountContradictions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty(self):
        f = self.tmpdir / "c.md"
        f.write_text("# Contradictions\n", encoding="utf-8")
        r = self.mod._count_contradictions(f)
        self.assertEqual(r["total"], 0)

    def test_missing_file(self):
        r = self.mod._count_contradictions(self.tmpdir / "nope.md")
        self.assertEqual(r["total"], 0)

    def test_counts_statuses(self):
        f = self.tmpdir / "c.md"
        f.write_text(
            "| 2026-01-01 | A vs B | ⏳ |\n"
            "| 2026-01-02 | C vs D | ✅ |\n"
            "| 2026-01-03 | E vs F | ⚠️ |\n"
            "| 2026-01-04 | G vs H | ✅ resolved |\n",
            encoding="utf-8",
        )
        r = self.mod._count_contradictions(f)
        self.assertEqual(r["total"], 4)
        self.assertEqual(r["resolved"], 2)
        self.assertEqual(r["active"], 2)


# ── Test _count_sil_signals ──────────────────────────────────────────────────

class TestCountSilSignals(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_memory(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        r = self.mod._count_sil_signals(mem)
        self.assertTrue(all(v == 0 for v in r.values()))

    def test_detects_cc_fail(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        (mem / "decisions-log.md").write_text(
            "- [2026-01-01] Terminé sans vérif\n"
            "- [2026-01-02] CC_FAIL détecté\n",
            encoding="utf-8",
        )
        r = self.mod._count_sil_signals(mem)
        self.assertGreater(r["cc_fail"], 0)

    def test_detects_guardrail(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        (mem / "decisions-log.md").write_text(
            "- [2026-01-01] Fichier écrasé overwrite config\n",
            encoding="utf-8",
        )
        r = self.mod._count_sil_signals(mem)
        self.assertGreater(r["guardrail_miss"], 0)

    def test_scans_learnings(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        ld = mem / "agent-learnings"
        ld.mkdir(parents=True)
        (ld / "dev.md").write_text(
            "- [2026-01-01] En fait, c'était incorrect\n",
            encoding="utf-8",
        )
        r = self.mod._count_sil_signals(mem)
        self.assertGreater(r["expertise_gap"], 0)


# ── Test _count_learnings ────────────────────────────────────────────────────

class TestCountLearnings(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        r = self.mod._count_learnings(mem)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["agents"], {})

    def test_counts_by_agent(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        ld = mem / "agent-learnings"
        ld.mkdir(parents=True)
        (ld / "dev.md").write_text(
            "- [2026-01-01] Learning 1\n"
            "- [2026-01-02] Learning 2\n",
            encoding="utf-8",
        )
        (ld / "qa.md").write_text("- [2026-01-01] Learning A\n", encoding="utf-8")
        r = self.mod._count_learnings(mem)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["agents"]["dev"], 2)
        self.assertEqual(r["agents"]["qa"], 1)

    def test_since_filter(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        ld = mem / "agent-learnings"
        ld.mkdir(parents=True)
        (ld / "dev.md").write_text(
            "- [2025-01-01] Old\n"
            "- [2026-06-01] New\n",
            encoding="utf-8",
        )
        r = self.mod._count_learnings(mem, since="2026-01-01")
        self.assertEqual(r["total"], 1)


# ── Test _count_decisions ────────────────────────────────────────────────────

class TestCountDecisions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        r = self.mod._count_decisions(mem)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["reversals"], 0)

    def test_counts_decisions_and_reversals(self):
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        (mem / "decisions-log.md").write_text(
            "- [2026-01-01] Chose React\n"
            "- [2026-01-02] En fait non, annulé React, pris Vue\n"
            "- [2026-01-03] Setup CI/CD\n"
            "- [2026-01-04] Revert de la config\n",
            encoding="utf-8",
        )
        r = self.mod._count_decisions(mem)
        self.assertEqual(r["total"], 4)
        self.assertEqual(r["reversals"], 2)  # "annulé" + "revert"


# ── Test score_recovery ──────────────────────────────────────────────────────

class TestScoreRecovery(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_failures(self):
        failures = {"total": 0, "with_lesson": 0, "with_rule": 0, "categories": {}}
        d = self.mod.score_recovery(failures)
        self.assertEqual(d.score, 0.5)
        self.assertEqual(d.evidence_count, 0)

    def test_all_rules(self):
        failures = {"total": 10, "with_lesson": 10, "with_rule": 10, "categories": {}}
        d = self.mod.score_recovery(failures)
        self.assertEqual(d.score, 1.0)

    def test_no_rules_no_lessons(self):
        failures = {"total": 5, "with_lesson": 0, "with_rule": 0, "categories": {}}
        d = self.mod.score_recovery(failures)
        self.assertEqual(d.score, 0.0)

    def test_partial_recovery(self):
        failures = {"total": 10, "with_lesson": 7, "with_rule": 3, "categories": {}}
        d = self.mod.score_recovery(failures)
        # 0.3 * 0.6 + 0.7 * 0.4 = 0.18 + 0.28 = 0.46
        self.assertAlmostEqual(d.score, 0.46, places=2)

    def test_recommendations_low_rules(self):
        failures = {"total": 10, "with_lesson": 3, "with_rule": 2, "categories": {}}
        d = self.mod.score_recovery(failures)
        self.assertTrue(len(d.recommendations) > 0)


# ── Test score_learning_velocity ──────────────────────────────────────────────

class TestScoreLearningVelocity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_learnings(self):
        d = self.mod.score_learning_velocity(
            {"total": 0, "agents": {}, "per_agent": []})
        self.assertEqual(d.score, 0.0)

    def test_high_volume_many_agents(self):
        d = self.mod.score_learning_velocity(
            {"total": 60, "agents": {"a": 20, "b": 15, "c": 10, "d": 10, "e": 5},
             "per_agent": []})
        self.assertGreaterEqual(d.score, 0.9)

    def test_few_learnings_one_agent(self):
        d = self.mod.score_learning_velocity(
            {"total": 3, "agents": {"dev": 3}, "per_agent": []})
        self.assertLess(d.score, 0.3)

    def test_recommends_more_agents(self):
        d = self.mod.score_learning_velocity(
            {"total": 20, "agents": {"dev": 20}, "per_agent": []})
        self.assertTrue(any("agent" in r.lower() for r in d.recommendations))


# ── Test score_contradiction_resolution ───────────────────────────────────────

class TestScoreContradictionResolution(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_contradictions(self):
        d = self.mod.score_contradiction_resolution(
            {"total": 0, "active": 0, "resolved": 0})
        self.assertEqual(d.score, 0.5)

    def test_all_resolved(self):
        d = self.mod.score_contradiction_resolution(
            {"total": 10, "active": 0, "resolved": 10})
        self.assertEqual(d.score, 1.0)

    def test_none_resolved(self):
        d = self.mod.score_contradiction_resolution(
            {"total": 10, "active": 10, "resolved": 0})
        self.assertEqual(d.score, 0.0)

    def test_recommends_on_active(self):
        d = self.mod.score_contradiction_resolution(
            {"total": 5, "active": 3, "resolved": 2})
        self.assertTrue(any("active" in r.lower() for r in d.recommendations))


# ── Test score_signal_trend ──────────────────────────────────────────────────

class TestScoreSignalTrend(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_signals(self):
        d = self.mod.score_signal_trend(
            {"cc_fail": 0, "incomplete": 0, "contradiction": 0,
             "guardrail_miss": 0, "expertise_gap": 0})
        self.assertEqual(d.score, 0.7)

    def test_many_signals_low_score(self):
        d = self.mod.score_signal_trend(
            {"cc_fail": 8, "incomplete": 5, "contradiction": 3,
             "guardrail_miss": 4, "expertise_gap": 5})
        self.assertLess(d.score, 0.3)

    def test_critical_penalty(self):
        # 4 cc_fail → critical penalty
        d = self.mod.score_signal_trend(
            {"cc_fail": 4, "incomplete": 0, "contradiction": 0,
             "guardrail_miss": 0, "expertise_gap": 0})
        # Without penalty: 1.0 - 4/25 = 0.84
        # With penalty (4 critical): 0.84 * 0.7 = 0.588
        self.assertLess(d.score, 0.6)

    def test_recommends_cc_fix(self):
        d = self.mod.score_signal_trend(
            {"cc_fail": 3, "incomplete": 0, "contradiction": 0,
             "guardrail_miss": 0, "expertise_gap": 0})
        self.assertTrue(any("CC_FAIL" in r for r in d.recommendations))


# ── Test score_decision_quality ──────────────────────────────────────────────

class TestScoreDecisionQuality(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_decisions(self):
        d = self.mod.score_decision_quality({"total": 0, "reversals": 0})
        self.assertEqual(d.score, 0.5)

    def test_perfect_decisions(self):
        d = self.mod.score_decision_quality({"total": 20, "reversals": 0})
        self.assertEqual(d.score, 1.0)

    def test_high_reversal(self):
        d = self.mod.score_decision_quality({"total": 10, "reversals": 5})
        self.assertLess(d.score, 0.5)

    def test_recommends_consensus(self):
        d = self.mod.score_decision_quality({"total": 10, "reversals": 3})
        self.assertTrue(any("consensus" in r.lower() for r in d.recommendations))


# ── Test score_pattern_recurrence ────────────────────────────────────────────

class TestScorePatternRecurrence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_no_patterns(self):
        d = self.mod.score_pattern_recurrence(
            {"categories": {}},
            {"cc_fail": 0, "incomplete": 0},
        )
        self.assertEqual(d.score, 0.5)

    def test_diverse_categories(self):
        cats = dict.fromkeys(self.mod.FAILURE_CATEGORIES, 2)
        d = self.mod.score_pattern_recurrence(
            {"categories": cats},
            {},
        )
        # diversity = 6/6 = 1.0, concentration = 2/12 = 0.167
        # score = (1-0.167)*0.6 + 1.0*0.4 = 0.50 + 0.40 = 0.90
        self.assertGreater(d.score, 0.8)

    def test_concentrated_failure(self):
        d = self.mod.score_pattern_recurrence(
            {"categories": {"CC-FAIL": 10}},
            {},
        )
        # diversity = 1/6 = 0.167, concentration = 10/10 = 1.0
        # score = (1-1.0)*0.6 + 0.167*0.4 = 0 + 0.067 = 0.067
        self.assertLess(d.score, 0.15)

    def test_recommends_guardrail(self):
        d = self.mod.score_pattern_recurrence(
            {"categories": {"CC-FAIL": 8, "HALLUCINATION": 1}},
            {},
        )
        self.assertTrue(any("guardrail" in r.lower() for r in d.recommendations))


# ── Test compute_antifragile_score ────────────────────────────────────────────

class TestComputeAntifragileScore(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_project(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.dimensions), 6)
        self.assertIn(r.level, {"FRAGILE", "ROBUST", "ANTIFRAGILE"})

    def test_fragile_project(self):
        _create_memory_tree(
            self.tmpdir,
            failures=(
                "### [2026-01-01] CC-FAIL — err1\n"
                "### [2026-01-02] CC-FAIL — err2\n"
                "### [2026-01-03] CC-FAIL — err3\n"
                "### [2026-01-04] CC-FAIL — err4\n"
                "### [2026-01-05] CC-FAIL — err5\n"
            ),
            decisions=(
                "- [2026-01-01] décision annulé\n"
                "- [2026-01-02] revert config\n"
                "- [2026-01-03] rollback\n"
            ),
        )
        r = self.mod.compute_antifragile_score(self.tmpdir)
        # Many failures with no rules/lessons + high reversals → fragile
        self.assertLess(r.global_score, 40)

    def test_antifragile_project(self):
        learnings = {
            "dev.md": "\n".join(
                f"- [2026-01-{i:02d}] Learning #{i}" for i in range(1, 21)
            ),
            "qa.md": "\n".join(
                f"- [2026-01-{i:02d}] QA Learning #{i}" for i in range(1, 16)
            ),
            "architect.md": "\n".join(
                f"- [2026-01-{i:02d}] Arch Learning #{i}" for i in range(1, 11)
            ),
            "pm.md": "\n".join(
                f"- [2026-01-{i:02d}] PM Learning #{i}" for i in range(1, 6)
            ),
            "sm.md": "\n".join(
                f"- [2026-01-{i:02d}] SM Learning #{i}" for i in range(1, 4)
            ),
        }
        _create_memory_tree(
            self.tmpdir,
            failures=(
                "### [2026-01-01] CC-FAIL — err1\n"
                "- Leçon : fix process\n"
                "- Règle instaurée : always validate\n\n"
                "### [2026-01-02] HALLUCINATION — err2\n"
                "- Leçon : double check\n"
                "- Règle instaurée : verify sources\n\n"
                "### [2026-01-03] WRONG-ASSUMPTION — err3\n"
                "- Leçon : ask first\n"
                "- Règle instaurée : assumption check\n"
            ),
            contradictions=(
                "| | c1 | ✅ |\n"
                "| | c2 | ✅ |\n"
                "| | c3 | ✅ |\n"
            ),
            decisions=(
                "- [2026-01-01] Use TypeScript\n"
                "- [2026-01-02] Setup testing\n"
                "- [2026-01-03] Add CI\n"
                "- [2026-01-04] Add monitoring\n"
            ),
            learnings=learnings,
        )
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.assertGreater(r.global_score, 55)

    def test_since_filter(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir, since="2027-01-01")
        self.assertIsNotNone(r)
        self.assertEqual(r.since, "2027-01-01")

    def test_total_evidence(self):
        _create_memory_tree(
            self.tmpdir,
            failures="### [2026-01-01] CC-FAIL — err\n",
            decisions="- [2026-01-01] dec1\n",
        )
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.assertGreater(r.total_evidence, 0)

    def test_level_fragile(self):
        _create_memory_tree(self.tmpdir)
        # Empty project defaults to mostly 0.5 → ROBUST range
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.assertIn(r.level, {"FRAGILE", "ROBUST", "ANTIFRAGILE"})

    def test_summary_non_empty(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.assertTrue(len(r.summary) > 0)


# ── Test save_score / load_history ────────────────────────────────────────────

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-output").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_save_and_load(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        self.mod.save_score(r, self.tmpdir)

        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["score"], r.global_score)

    def test_load_empty(self):
        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(history, [])

    def test_multiple_saves(self):
        _create_memory_tree(self.tmpdir)
        for _ in range(3):
            r = self.mod.compute_antifragile_score(self.tmpdir)
            self.mod.save_score(r, self.tmpdir)

        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(len(history), 3)

    def test_save_creates_output_dir(self):
        root = self.tmpdir / "fresh"
        _create_memory_tree(root)
        r = self.mod.compute_antifragile_score(root)
        path = self.mod.save_score(r, root)
        self.assertTrue(path.exists())

    def test_corrupted_history(self):
        hist_file = self.tmpdir / "_grimoire-output" / self.mod.HISTORY_FILE
        hist_file.write_text("not json", encoding="utf-8")
        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(history, [])


# ── Test render_report ────────────────────────────────────────────────────────

class TestRenderReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_contains_score(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        report = self.mod.render_report(r)
        self.assertIn(str(r.global_score), report)
        self.assertIn(r.level, report)

    def test_contains_dimensions(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        report = self.mod.render_report(r)
        for d in r.dimensions:
            self.assertIn(d.name, report)

    def test_contains_table(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir)
        report = self.mod.render_report(r)
        self.assertIn("| Dimension |", report)

    def test_since_displayed(self):
        _create_memory_tree(self.tmpdir)
        r = self.mod.compute_antifragile_score(self.tmpdir, since="2026-01-01")
        report = self.mod.render_report(r)
        self.assertIn("2026-01-01", report)


# ── Test render_trend ────────────────────────────────────────────────────────

class TestRenderTrend(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_empty_history(self):
        result = self.mod.render_trend([])
        self.assertIn("Aucun", result)

    def test_single_entry(self):
        history = [
            {"timestamp": "2026-01-01T00:00:00", "score": 50,
             "level": "ROBUST", "evidence": 10},
        ]
        result = self.mod.render_trend(history)
        self.assertIn("50/100", result)

    def test_trend_calculation(self):
        history = [
            {"timestamp": "2026-01-01", "score": 40, "level": "ROBUST", "evidence": 5},
            {"timestamp": "2026-02-01", "score": 60, "level": "ANTIFRAGILE", "evidence": 10},
        ]
        result = self.mod.render_trend(history)
        self.assertIn("📈", result)  # positive trend

    def test_negative_trend(self):
        history = [
            {"timestamp": "2026-01-01", "score": 70, "level": "ANTIFRAGILE", "evidence": 10},
            {"timestamp": "2026-02-01", "score": 30, "level": "ROBUST", "evidence": 5},
        ]
        result = self.mod.render_trend(history)
        self.assertIn("📉", result)

    def test_average_with_3_entries(self):
        history = [
            {"timestamp": "2026-01-01", "score": 30, "level": "ROBUST", "evidence": 5},
            {"timestamp": "2026-02-01", "score": 50, "level": "ROBUST", "evidence": 10},
            {"timestamp": "2026-03-01", "score": 70, "level": "ANTIFRAGILE", "evidence": 15},
        ]
        result = self.mod.render_trend(history)
        self.assertIn("Moyenne", result)


# ── Test constantes ──────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_weights_sum_to_one(self):
        total = sum(self.mod.WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0, places=3)

    def test_thresholds(self):
        self.assertEqual(self.mod.FRAGILE_THRESHOLD, 30)
        self.assertEqual(self.mod.ROBUST_THRESHOLD, 60)

    def test_failure_categories(self):
        self.assertEqual(len(self.mod.FAILURE_CATEGORIES), 6)

    def test_sil_markers_keys(self):
        expected = {"cc_fail", "incomplete", "contradiction",
                    "guardrail_miss", "expertise_gap"}
        self.assertEqual(set(self.mod.SIL_MARKERS.keys()), expected)


if __name__ == "__main__":
    unittest.main()
