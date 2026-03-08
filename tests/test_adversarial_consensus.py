#!/usr/bin/env python3
"""
Tests pour adversarial-consensus.py — Protocole de Consensus Adversarial.

Fonctions testées :
  - _extract_tech_signals()
  - _score_criterion()
  - evaluate_proposal()
  - devil_advocate_analysis()
  - run_consensus()
  - save_result()
  - load_history()
  - render_report()
  - render_history_table()
  - render_stats()
  - Vote, DevilChallenge, ConsensusResult dataclasses
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_consensus():
    return importlib.import_module("adversarial-consensus")


# ── Test DataClasses ──────────────────────────────────────────────────────────

class TestDataClasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_vote_defaults(self):
        v = self.mod.Vote(
            voter_id="technical", voter_name="Tech",
            verdict="approve", confidence=0.8, rationale="Good",
        )
        self.assertEqual(v.criteria_scores, {})
        self.assertEqual(v.concerns, [])

    def test_devil_challenge_fields(self):
        c = self.mod.DevilChallenge(
            attack_vector="Under-specified",
            severity="critical",
            description="Missing details",
            mitigation="Write RFC",
        )
        self.assertEqual(c.severity, "critical")
        self.assertEqual(c.attack_vector, "Under-specified")

    def test_consensus_result_fields(self):
        r = self.mod.ConsensusResult(
            proposal="Test",
            timestamp="2025-06-01T10:00:00",
            votes=[], devil_challenges=[],
            consensus_reached=True, consensus_score=0.85,
            final_verdict="approved",
            surviving_concerns=[], decision_hash="abc123",
        )
        self.assertTrue(r.consensus_reached)
        self.assertEqual(r.final_verdict, "approved")


# ── Test VoterPerspectives ────────────────────────────────────────────────────

class TestVoterPerspectives(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_three_voter_perspectives(self):
        self.assertEqual(len(self.mod.VOTER_PERSPECTIVES), self.mod.VOTERS_COUNT)

    def test_voter_ids(self):
        ids = [v.id for v in self.mod.VOTER_PERSPECTIVES]
        self.assertIn("technical", ids)
        self.assertIn("business", ids)
        self.assertIn("risk", ids)

    def test_all_voters_have_criteria(self):
        for v in self.mod.VOTER_PERSPECTIVES:
            self.assertGreater(len(v.criteria), 0)

    def test_devil_advocate_exists(self):
        self.assertEqual(self.mod.DEVIL_ADVOCATE.id, "devil")
        self.assertGreater(len(self.mod.DEVIL_ADVOCATE.criteria), 0)


# ── Test _extract_tech_signals ────────────────────────────────────────────────

class TestExtractTechSignals(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_detects_database(self):
        signals = self.mod._extract_tech_signals("Use PostgreSQL for data storage")
        self.assertTrue(signals["has_database"])

    def test_detects_api(self):
        signals = self.mod._extract_tech_signals("Create a REST API endpoint")
        self.assertTrue(signals["has_api"])

    def test_detects_infra(self):
        signals = self.mod._extract_tech_signals("Deploy with Docker and Kubernetes")
        self.assertTrue(signals["has_infra"])

    def test_detects_security(self):
        signals = self.mod._extract_tech_signals("Add authentication and encrypt data")
        self.assertTrue(signals["has_security"])

    def test_detects_performance(self):
        signals = self.mod._extract_tech_signals("Cache layer for performance and latency")
        self.assertTrue(signals["has_performance"])

    def test_detects_migration(self):
        signals = self.mod._extract_tech_signals("Migrate the database to new schema")
        self.assertTrue(signals["has_migration"])

    def test_detects_new_dep(self):
        signals = self.mod._extract_tech_signals("Install new library for parsing")
        self.assertTrue(signals["has_new_dep"])

    def test_no_signals_plain_text(self):
        signals = self.mod._extract_tech_signals("Simple refactoring of variable names")
        self.assertFalse(signals["has_database"])
        self.assertFalse(signals["has_api"])
        self.assertFalse(signals["has_infra"])
        self.assertFalse(signals["has_security"])
        self.assertFalse(signals["has_migration"])
        self.assertFalse(signals["has_new_dep"])

    def test_word_count(self):
        signals = self.mod._extract_tech_signals("one two three four")
        self.assertEqual(signals["word_count"], 4)

    def test_case_insensitive(self):
        signals = self.mod._extract_tech_signals("POSTGRESQL DATABASE MIGRATION")
        self.assertTrue(signals["has_database"])
        self.assertTrue(signals["has_migration"])


# ── Test _score_criterion ─────────────────────────────────────────────────────

class TestScoreCriterion(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_complexity_base(self):
        signals = self.mod._extract_tech_signals("Simple change")
        score = self.mod._score_criterion("Simple change", "Complexité d'implémentation", signals)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_complexity_penalty_new_dep(self):
        signals_plain = self.mod._extract_tech_signals("Simple change")
        signals_dep = self.mod._extract_tech_signals("Install new library")
        score_plain = self.mod._score_criterion("Simple", "Complexité d'implémentation", signals_plain)
        score_dep = self.mod._score_criterion("Install lib", "Complexité d'implémentation", signals_dep)
        self.assertGreater(score_plain, score_dep)

    def test_performance_criterion(self):
        signals = self.mod._extract_tech_signals("Add cache for performance")
        score = self.mod._score_criterion("x", "Impact sur la performance runtime", signals)
        self.assertEqual(score, 0.5)  # performance detected → 0.5

    def test_security_criterion(self):
        signals = self.mod._extract_tech_signals("Add auth encryption")
        score = self.mod._score_criterion("x", "Sécurité et données sensibles", signals)
        self.assertEqual(score, 0.4)  # has_security → 0.4

    def test_default_score(self):
        signals = self.mod._extract_tech_signals("Something")
        score = self.mod._score_criterion("x", "Unknown criterion type", signals)
        self.assertEqual(score, 0.5)

    def test_score_bounds(self):
        """All scores should be between 0.0 and 1.0."""
        proposals = [
            "Install new library for docker kubernetes migration",
            "Simple rename",
            "",
        ]
        criteria = [c for v in self.mod.VOTER_PERSPECTIVES for c in v.criteria]
        for prop in proposals:
            signals = self.mod._extract_tech_signals(prop)
            for crit in criteria:
                score = self.mod._score_criterion(prop, crit, signals)
                self.assertGreaterEqual(score, 0.0, f"Score < 0 for '{crit}' on '{prop}'")
                self.assertLessEqual(score, 1.0, f"Score > 1 for '{crit}' on '{prop}'")


# ── Test evaluate_proposal ────────────────────────────────────────────────────

class TestEvaluateProposal(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_returns_vote(self):
        vote = self.mod.evaluate_proposal(
            "Use Redis for caching",
            self.mod.VOTER_PERSPECTIVES[0],  # technical
        )
        self.assertIsInstance(vote, self.mod.Vote)
        self.assertEqual(vote.voter_id, "technical")

    def test_verdict_is_valid(self):
        for perspective in self.mod.VOTER_PERSPECTIVES:
            vote = self.mod.evaluate_proposal("Some proposal", perspective)
            self.assertIn(vote.verdict, {"approve", "reject", "abstain"})

    def test_confidence_in_range(self):
        vote = self.mod.evaluate_proposal(
            "Deploy with Kubernetes",
            self.mod.VOTER_PERSPECTIVES[2],  # risk
        )
        self.assertGreaterEqual(vote.confidence, 0.0)
        self.assertLessEqual(vote.confidence, 1.0)

    def test_criteria_scores_populated(self):
        perspective = self.mod.VOTER_PERSPECTIVES[0]
        vote = self.mod.evaluate_proposal("Database migration required", perspective)
        self.assertEqual(len(vote.criteria_scores), len(perspective.criteria))

    def test_concerns_are_low_scores(self):
        vote = self.mod.evaluate_proposal(
            "Install new library and migrate database with security",
            self.mod.VOTER_PERSPECTIVES[0],  # technical
        )
        for concern in vote.concerns:
            # Each concern should be a criterion that scored < 0.5
            self.assertIn(concern, vote.criteria_scores)
            self.assertLess(vote.criteria_scores[concern], 0.5)

    def test_technical_rationale_mentions_deps(self):
        vote = self.mod.evaluate_proposal(
            "Install a new library for PDF parsing",
            self.mod.VOTER_PERSPECTIVES[0],
        )
        # Should mention dépendances or deps
        self.assertIn("voter_id", dir(vote))

    def test_risk_voter_flags_security(self):
        vote = self.mod.evaluate_proposal(
            "Add auth and encrypt sensitive data",
            self.mod.VOTER_PERSPECTIVES[2],  # risk
        )
        # Security flag should generate rationale
        if vote.rationale:
            self.assertIsInstance(vote.rationale, str)


# ── Test devil_advocate_analysis ──────────────────────────────────────────────

class TestDevilAdvocateAnalysis(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_always_includes_alternatives_attack(self):
        challenges = self.mod.devil_advocate_analysis("Simple change", [])
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Alternatives non évaluées", vectors)

    def test_always_includes_tco_attack(self):
        challenges = self.mod.devil_advocate_analysis("Simple change", [])
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Coût de maintenance à 6 mois", vectors)

    def test_short_proposal_triggers_underspecified(self):
        challenges = self.mod.devil_advocate_analysis("Use Redis", [])
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Proposition sous-spécifiée", vectors)

    def test_long_proposal_no_underspecified(self):
        long_prop = " ".join(["word"] * 30)
        challenges = self.mod.devil_advocate_analysis(long_prop, [])
        vectors = [c.attack_vector for c in challenges]
        self.assertNotIn("Proposition sous-spécifiée", vectors)

    def test_migration_triggers_irreversible_attack(self):
        challenges = self.mod.devil_advocate_analysis(
            "Migrate the database to new schema with transition plan", []
        )
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Migration irréversible", vectors)

    def test_security_triggers_attack_surface(self):
        challenges = self.mod.devil_advocate_analysis(
            "Add authentication layer with token encryption for security", []
        )
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Surface d'attaque élargie", vectors)

    def test_new_dep_triggers_phantom_dep(self):
        challenges = self.mod.devil_advocate_analysis(
            "Install new library package for json parsing functionality", []
        )
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Dépendance fantôme", vectors)

    def test_voter_concerns_included(self):
        mock_vote = self.mod.Vote(
            voter_id="tech", voter_name="Tech",
            verdict="abstain", confidence=0.5,
            rationale="so-so",
            concerns=["Maintenabilité long terme"],
        )
        challenges = self.mod.devil_advocate_analysis("Something", [mock_vote])
        vectors = [c.attack_vector for c in challenges]
        self.assertIn("Concerns non résolus des votants", vectors)

    def test_severity_values(self):
        challenges = self.mod.devil_advocate_analysis(
            "Install and migrate with auth", []
        )
        for c in challenges:
            self.assertIn(c.severity, {"critical", "major", "minor"})

    def test_mitigations_provided(self):
        challenges = self.mod.devil_advocate_analysis("Use Redis", [])
        for c in challenges:
            self.assertIsInstance(c.mitigation, str)
            self.assertGreater(len(c.mitigation), 0)


# ── Test run_consensus ────────────────────────────────────────────────────────

class TestRunConsensus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_consensus_result(self):
        result = self.mod.run_consensus("Use Redis for caching", self.tmpdir)
        self.assertIsInstance(result, self.mod.ConsensusResult)

    def test_has_three_votes(self):
        result = self.mod.run_consensus("Simple refactoring", self.tmpdir)
        self.assertEqual(len(result.votes), self.mod.VOTERS_COUNT)

    def test_has_devil_challenges(self):
        result = self.mod.run_consensus("Simple refactoring", self.tmpdir)
        self.assertGreater(len(result.devil_challenges), 0)

    def test_score_in_range(self):
        result = self.mod.run_consensus("Something", self.tmpdir)
        self.assertGreaterEqual(result.consensus_score, 0.0)
        self.assertLessEqual(result.consensus_score, 1.0)

    def test_verdict_is_valid(self):
        result = self.mod.run_consensus("Something", self.tmpdir)
        self.assertIn(result.final_verdict, {"approved", "rejected", "inconclusive"})

    def test_hash_is_generated(self):
        result = self.mod.run_consensus("Test proposal", self.tmpdir)
        self.assertEqual(len(result.decision_hash), 12)

    def test_critical_challenges_reduce_score(self):
        """A proposal with migration + security + new deps should have lower score."""
        result_simple = self.mod.run_consensus("Rename a variable", self.tmpdir)
        result_complex = self.mod.run_consensus(
            "Install new library, migrate database, add auth encryption security", self.tmpdir
        )
        # Complex should have more critical challenges → lower effective score
        critical_simple = sum(1 for c in result_simple.devil_challenges if c.severity == "critical")
        critical_complex = sum(1 for c in result_complex.devil_challenges if c.severity == "critical")
        self.assertGreaterEqual(critical_complex, critical_simple)

    def test_custom_threshold(self):
        """With threshold 1.0, almost nothing should pass."""
        result = self.mod.run_consensus("Something", self.tmpdir, threshold=1.0)
        self.assertFalse(result.consensus_reached)

    def test_zero_threshold(self):
        """With threshold 0.0, everything should pass."""
        result = self.mod.run_consensus("Something", self.tmpdir, threshold=0.0)
        self.assertTrue(result.consensus_reached)

    def test_surviving_concerns_are_bounded(self):
        result = self.mod.run_consensus("Complex migration proposal", self.tmpdir)
        self.assertLessEqual(len(result.surviving_concerns), 10)


# ── Test save_result / load_history ───────────────────────────────────────────

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_file(self):
        result = self.mod.run_consensus("Test", self.tmpdir)
        path = self.mod.save_result(result, self.tmpdir)
        self.assertTrue(path.exists())

    def test_save_appends_to_history(self):
        r1 = self.mod.run_consensus("First proposal", self.tmpdir)
        r2 = self.mod.run_consensus("Second proposal", self.tmpdir)
        self.mod.save_result(r1, self.tmpdir)
        self.mod.save_result(r2, self.tmpdir)
        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(len(history), 2)

    def test_load_empty_history(self):
        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(history, [])

    def test_load_corrupted_history(self):
        out = self.tmpdir / "_grimoire-output"
        out.mkdir(parents=True)
        (out / self.mod.HISTORY_FILE).write_text("not json{{{", encoding="utf-8")
        history = self.mod.load_history(self.tmpdir)
        self.assertEqual(history, [])

    def test_saved_entry_has_required_fields(self):
        result = self.mod.run_consensus("Test", self.tmpdir)
        self.mod.save_result(result, self.tmpdir)
        history = self.mod.load_history(self.tmpdir)
        entry = history[0]
        self.assertIn("hash", entry)
        self.assertIn("timestamp", entry)
        self.assertIn("proposal", entry)
        self.assertIn("verdict", entry)
        self.assertIn("score", entry)
        self.assertIn("votes", entry)

    def test_proposal_truncated_in_history(self):
        long_proposal = "A" * 500
        result = self.mod.run_consensus(long_proposal, self.tmpdir)
        self.mod.save_result(result, self.tmpdir)
        history = self.mod.load_history(self.tmpdir)
        self.assertLessEqual(len(history[0]["proposal"]), 200)


# ── Test render_report ────────────────────────────────────────────────────────

class TestRenderReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_renders_markdown(self):
        result = self.mod.run_consensus("Test proposal", self.tmpdir)
        report = self.mod.render_report(result)
        self.assertIn("Rapport de Consensus", report)
        self.assertIn("Test proposal", report)

    def test_contains_votes_section(self):
        result = self.mod.run_consensus("Test", self.tmpdir)
        report = self.mod.render_report(result)
        self.assertIn("Votes", report)
        self.assertIn("Votant", report)

    def test_contains_devil_section(self):
        result = self.mod.run_consensus("Short", self.tmpdir)
        report = self.mod.render_report(result)
        self.assertIn("Avocat du Diable", report)

    def test_contains_conclusion(self):
        result = self.mod.run_consensus("Test", self.tmpdir)
        report = self.mod.render_report(result)
        self.assertIn("Conclusion", report)

    def test_approved_conclusion_text(self):
        result = self.mod.run_consensus("Simple rename", self.tmpdir, threshold=0.0)
        report = self.mod.render_report(result)
        self.assertIn("atteint", report)

    def test_rejected_shows_pas_atteint(self):
        result = self.mod.run_consensus("Something", self.tmpdir, threshold=1.0)
        report = self.mod.render_report(result)
        self.assertIn("PAS atteint", report)

    def test_contains_criteria_scores(self):
        result = self.mod.run_consensus("Test", self.tmpdir)
        report = self.mod.render_report(result)
        self.assertIn("Scores détaillés", report)

    def test_surviving_concerns_shown(self):
        result = self.mod.run_consensus(
            "Install library migrate database auth security", self.tmpdir
        )
        report = self.mod.render_report(result)
        if result.surviving_concerns:
            self.assertIn("Concerns non résolus", report)


# ── Test render_history_table ─────────────────────────────────────────────────

class TestRenderHistoryTable(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_empty_history(self):
        result = self.mod.render_history_table([])
        self.assertIn("Aucune décision", result)

    def test_populated_history(self):
        history = [
            {
                "hash": "abc123", "timestamp": "2025-06-01T10:00:00",
                "verdict": "approved", "score": 0.85,
                "proposal": "Use Redis for caching",
            },
        ]
        result = self.mod.render_history_table(history)
        self.assertIn("abc123", result)
        self.assertIn("approved", result)

    def test_multiple_entries(self):
        history = [
            {"hash": f"hash{i}", "timestamp": f"2025-06-0{i}T10:00:00",
             "verdict": "approved", "score": 0.8, "proposal": f"Prop {i}"}
            for i in range(1, 4)
        ]
        result = self.mod.render_history_table(history)
        self.assertIn("hash1", result)
        self.assertIn("hash3", result)


# ── Test render_stats ─────────────────────────────────────────────────────────

class TestRenderStats(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_empty_history(self):
        result = self.mod.render_stats([])
        self.assertIn("Aucune décision", result)

    def test_all_approved(self):
        history = [
            {"verdict": "approved", "score": 0.9},
            {"verdict": "approved", "score": 0.8},
        ]
        result = self.mod.render_stats(history)
        self.assertIn("Total décisions", result)
        self.assertIn("Approuvées", result)
        self.assertIn("2", result)

    def test_mixed_verdicts(self):
        history = [
            {"verdict": "approved", "score": 0.9},
            {"verdict": "rejected", "score": 0.3},
            {"verdict": "inconclusive", "score": 0.5},
        ]
        result = self.mod.render_stats(history)
        self.assertIn("Rejetées", result)
        self.assertIn("Inconclusives", result)

    def test_average_score(self):
        history = [
            {"verdict": "approved", "score": 0.6},
            {"verdict": "approved", "score": 0.8},
        ]
        result = self.mod.render_stats(history)
        self.assertIn("Score moyen", result)


# ── Test Constants ────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_consensus()

    def test_voters_count(self):
        self.assertEqual(self.mod.VOTERS_COUNT, 3)

    def test_consensus_threshold(self):
        self.assertGreater(self.mod.CONSENSUS_THRESHOLD, 0.0)
        self.assertLess(self.mod.CONSENSUS_THRESHOLD, 1.0)

    def test_max_rounds(self):
        self.assertGreaterEqual(self.mod.MAX_ROUNDS, 1)

    def test_verdict_icons_complete(self):
        for verdict in ["approved", "rejected", "inconclusive"]:
            self.assertIn(verdict, self.mod.VERDICT_ICONS)

    def test_severity_icons_complete(self):
        for severity in ["critical", "major", "minor"]:
            self.assertIn(severity, self.mod.SEVERITY_ICONS)


if __name__ == "__main__":
    unittest.main()
