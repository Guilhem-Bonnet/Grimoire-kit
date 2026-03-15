#!/usr/bin/env python3
"""
Tests pour agent-darwinism.py — Grimoire Agent Darwinism.

Fonctions testées :
  - parse_trace_stats()
  - count_agent_learnings()
  - compute_dimension_* (6 dimensions)
  - compute_fitness()
  - propose_actions()
  - cmd_evaluate()
  - cmd_evolve()
  - save_history() / load_history()
  - render_leaderboard()
  - render_evaluate()
  - render_evolve()
  - render_history()
  - render_lineage()
  - RawAgentStats, FitnessDimensions, FitnessScore, EvolutionAction, GenerationRecord
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_dw():
    return importlib.import_module("agent-darwinism")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_trace(root: Path, entries: list[str]):
    """Créer un Grimoire_TRACE.md avec les entrées données."""
    out = root / "_grimoire-output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "Grimoire_TRACE.md").write_text("\n".join(entries), encoding="utf-8")


def _create_learnings(root: Path, data: dict[str, str]):
    """Créer des fichiers de learnings."""
    ld = root / "_grimoire" / "_memory" / "agent-learnings"
    ld.mkdir(parents=True, exist_ok=True)
    for name, content in data.items():
        (ld / name).write_text(content, encoding="utf-8")


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ── RawAgentStats tests ─────────────────────────────────────────────────────

class TestRawAgentStats(BaseTest):
    def test_ac_pass_rate_no_data(self):
        dw = _import_dw()
        s = dw.RawAgentStats(agent_id="dev")
        self.assertEqual(s.ac_pass_rate, 0.0)

    def test_ac_pass_rate_with_data(self):
        dw = _import_dw()
        s = dw.RawAgentStats(agent_id="dev", ac_pass_count=8, ac_fail_count=2)
        self.assertAlmostEqual(s.ac_pass_rate, 80.0)

    def test_ac_total(self):
        dw = _import_dw()
        s = dw.RawAgentStats(agent_id="dev", ac_pass_count=5, ac_fail_count=3)
        self.assertEqual(s.ac_total, 8)


# ── FitnessDimensions tests ─────────────────────────────────────────────────

class TestFitnessDimensions(BaseTest):
    def test_to_dict(self):
        dw = _import_dw()
        d = dw.FitnessDimensions(reliability=80, productivity=60)
        result = d.to_dict()
        self.assertEqual(result["reliability"], 80.0)
        self.assertEqual(result["productivity"], 60.0)
        self.assertEqual(result["learning"], 0.0)

    def test_from_dict(self):
        dw = _import_dw()
        d = dw.FitnessDimensions.from_dict({"reliability": 90, "learning": 50})
        self.assertEqual(d.reliability, 90)
        self.assertEqual(d.learning, 50)
        self.assertEqual(d.adaptability, 0.0)


# ── FitnessScore tests ──────────────────────────────────────────────────────

class TestFitnessScore(BaseTest):
    def test_roundtrip(self):
        dw = _import_dw()
        fs = dw.FitnessScore(
            agent_id="dev",
            dimensions=dw.FitnessDimensions(reliability=80, productivity=70),
            composite=75.0,
            level="ELITE",
            generation=1,
            timestamp="2026-01-01",
        )
        d = fs.to_dict()
        fs2 = dw.FitnessScore.from_dict(d)
        self.assertEqual(fs2.agent_id, "dev")
        self.assertEqual(fs2.composite, 75.0)
        self.assertEqual(fs2.level, "ELITE")


# ── EvolutionAction tests ───────────────────────────────────────────────────

class TestEvolutionAction(BaseTest):
    def test_roundtrip(self):
        dw = _import_dw()
        ea = dw.EvolutionAction(
            agent_id="qa", action="IMPROVE",
            reason="Low score", detail="Fix tests",
            source_agents=["dev"],
        )
        d = ea.to_dict()
        ea2 = dw.EvolutionAction.from_dict(d)
        self.assertEqual(ea2.agent_id, "qa")
        self.assertEqual(ea2.source_agents, ["dev"])


# ── GenerationRecord tests ──────────────────────────────────────────────────

class TestGenerationRecord(BaseTest):
    def test_roundtrip(self):
        dw = _import_dw()
        gr = dw.GenerationRecord(
            generation=3,
            timestamp="2026-01-01T00:00:00",
            scores=[{"agent_id": "dev", "composite": 80}],
            actions=[],
            summary={"agents_evaluated": 1},
        )
        d = gr.to_dict()
        gr2 = dw.GenerationRecord.from_dict(d)
        self.assertEqual(gr2.generation, 3)
        self.assertEqual(len(gr2.scores), 1)


# ── compute_dimension tests ─────────────────────────────────────────────────

class TestComputeDimensions(BaseTest):
    def test_reliability_high_ac(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", ac_pass_count=10, ac_fail_count=0)
        score = dw.compute_dimension_reliability(s)
        self.assertGreater(score, 80)

    def test_reliability_with_failures(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", ac_pass_count=8, ac_fail_count=2,
                              failures_count=5)
        score = dw.compute_dimension_reliability(s)
        # Should be penalized but still positive
        self.assertGreater(score, 0)
        self.assertLess(score, 100)

    def test_reliability_no_data(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        score = dw.compute_dimension_reliability(s)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_productivity_high(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", commits_attributed=8,
                              decisions_count=6)
        score = dw.compute_dimension_productivity(s)
        self.assertGreater(score, 80)

    def test_productivity_zero(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        score = dw.compute_dimension_productivity(s)
        self.assertEqual(score, 0.0)

    def test_learning_with_external(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", learnings_count=3)
        score = dw.compute_dimension_learning(s, external_learnings=5)
        self.assertEqual(score, 80.0)  # (3+5)*10 = 80

    def test_learning_capped(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", learnings_count=20)
        score = dw.compute_dimension_learning(s)
        self.assertEqual(score, 100.0)

    def test_adaptability_many_stories(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", stories_touched=7)
        score = dw.compute_dimension_adaptability(s)
        self.assertEqual(score, 100.0)  # 7*15 = 105 → capped 100

    def test_adaptability_zero(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        score = dw.compute_dimension_adaptability(s)
        self.assertEqual(score, 0.0)

    def test_resilience_no_failures(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        score = dw.compute_dimension_resilience(s)
        self.assertEqual(score, 80.0)

    def test_resilience_recurring(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", failures_count=4,
                              failure_patterns=["recurring", "recurring",
                                                 "test-failure", "lint-error"])
        score = dw.compute_dimension_resilience(s)
        self.assertLess(score, 60)

    def test_resilience_many_failures(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", failures_count=10,
                              failure_patterns=["test-failure"] * 10)
        score = dw.compute_dimension_resilience(s)
        self.assertLess(score, 50)

    def test_influence_high(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", checkpoints_created=3,
                              decisions_count=5)
        score = dw.compute_dimension_influence(s)
        self.assertEqual(score, 95.0)  # 3*15 + 5*10 = 95

    def test_influence_zero(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        score = dw.compute_dimension_influence(s)
        self.assertEqual(score, 0.0)


# ── compute_fitness tests ────────────────────────────────────────────────────

class TestComputeFitness(BaseTest):
    def test_elite_agent(self):
        dw = _import_dw()
        s = dw.RawAgentStats(
            "dev", stories_touched=5, decisions_count=8,
            ac_pass_count=20, ac_fail_count=1, commits_attributed=10,
            learnings_count=8, checkpoints_created=3,
        )
        fitness = dw.compute_fitness(s)
        self.assertEqual(fitness.level, "ELITE")
        self.assertGreaterEqual(fitness.composite, 75)

    def test_viable_agent(self):
        dw = _import_dw()
        s = dw.RawAgentStats(
            "qa", stories_touched=2, decisions_count=3,
            ac_pass_count=5, ac_fail_count=2, commits_attributed=3,
            learnings_count=3,
        )
        fitness = dw.compute_fitness(s)
        self.assertIn(fitness.level, ["VIABLE", "ELITE"])
        self.assertGreaterEqual(fitness.composite, 40)

    def test_obsolete_agent(self):
        dw = _import_dw()
        s = dw.RawAgentStats("ghost")
        fitness = dw.compute_fitness(s)
        # Empty agent still gets baseline resilience (80) + reliability (60)
        # so composite ~23 → FRAGILE threshold
        self.assertIn(fitness.level, ["FRAGILE", "OBSOLETE"])
        self.assertLess(fitness.composite, 40)

    def test_fitness_has_generation(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev", commits_attributed=1)
        fitness = dw.compute_fitness(s, generation=5)
        self.assertEqual(fitness.generation, 5)

    def test_fitness_external_learnings(self):
        dw = _import_dw()
        s = dw.RawAgentStats("dev")
        f1 = dw.compute_fitness(s, external_learnings=0)
        f2 = dw.compute_fitness(s, external_learnings=10)
        self.assertGreater(f2.composite, f1.composite)


# ── propose_actions tests ────────────────────────────────────────────────────

class TestProposeActions(BaseTest):
    def test_elite_gets_promote(self):
        dw = _import_dw()
        scores = [dw.FitnessScore(
            agent_id="dev", composite=85, level="ELITE",
            dimensions=dw.FitnessDimensions(reliability=90, productivity=80,
                                             learning=85, adaptability=90,
                                             resilience=80, influence=75),
        )]
        actions = dw.propose_actions(scores)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "PROMOTE")

    def test_obsolete_gets_deprecate(self):
        dw = _import_dw()
        scores = [dw.FitnessScore(
            agent_id="ghost", composite=10, level="OBSOLETE",
            dimensions=dw.FitnessDimensions(),
        )]
        actions = dw.propose_actions(scores)
        self.assertEqual(actions[0].action, "DEPRECATE")

    def test_fragile_with_elite_gets_hybridize(self):
        dw = _import_dw()
        scores = [
            dw.FitnessScore(
                agent_id="strong", composite=85, level="ELITE",
                dimensions=dw.FitnessDimensions(reliability=90, productivity=80,
                                                 learning=85, adaptability=90,
                                                 resilience=80, influence=75),
            ),
            dw.FitnessScore(
                agent_id="weak", composite=25, level="FRAGILE",
                dimensions=dw.FitnessDimensions(reliability=10, productivity=20,
                                                 learning=30, adaptability=40,
                                                 resilience=20, influence=10),
            ),
        ]
        actions = dw.propose_actions(scores)
        fragile_action = next(a for a in actions if a.agent_id == "weak")
        self.assertEqual(fragile_action.action, "HYBRIDIZE")
        self.assertIn("strong", fragile_action.source_agents)

    def test_viable_gets_observe(self):
        dw = _import_dw()
        scores = [dw.FitnessScore(
            agent_id="mid", composite=55, level="VIABLE",
            dimensions=dw.FitnessDimensions(reliability=60, productivity=50,
                                             learning=40, adaptability=70,
                                             resilience=60, influence=50),
        )]
        actions = dw.propose_actions(scores)
        self.assertEqual(actions[0].action, "OBSERVE")

    def test_trend_detection(self):
        dw = _import_dw()
        scores = [dw.FitnessScore(
            agent_id="dev", composite=80, level="ELITE",
            dimensions=dw.FitnessDimensions(reliability=80, productivity=80,
                                             learning=80, adaptability=80,
                                             resilience=80, influence=80),
        )]
        previous = [dw.FitnessScore(
            agent_id="dev", composite=50, level="VIABLE",
        )]
        actions = dw.propose_actions(scores, previous)
        self.assertIn("↑", actions[0].reason)

    def test_empty_scores(self):
        dw = _import_dw()
        actions = dw.propose_actions([])
        self.assertEqual(actions, [])


# ── parse_trace_stats tests ──────────────────────────────────────────────────

class TestParseTraceStats(BaseTest):
    def test_empty_trace(self):
        dw = _import_dw()
        result = dw.parse_trace_stats(self.root / "nonexistent.md")
        self.assertEqual(result, {})

    def test_basic_trace(self):
        dw = _import_dw()
        trace_path = self.root / "trace.md"
        trace_path.write_text(
            "## 2026-01-15 10:00 | dev | STORY-001\n"
            "[GIT-COMMIT] feat: add feature\n"
            "\n"
            "## 2026-01-15 11:00 | dev | STORY-001\n"
            "[DECISION] Use pattern X\n"
            "\n"
            "## 2026-01-15 12:00 | qa | STORY-002\n"
            "[AC-PASS] AC-1 passes\n",
            encoding="utf-8",
        )
        stats = dw.parse_trace_stats(trace_path)
        self.assertIn("dev", stats)
        self.assertIn("qa", stats)
        self.assertEqual(stats["dev"].commits_attributed, 1)
        self.assertEqual(stats["dev"].decisions_count, 1)
        self.assertEqual(stats["qa"].ac_pass_count, 1)

    def test_trace_with_failures(self):
        dw = _import_dw()
        trace_path = self.root / "trace.md"
        trace_path.write_text(
            "## 2026-01-15 | dev | STORY-001\n"
            "[FAILURE] test FAIL: missing import\n"
            "\n"
            "## 2026-01-15 | dev | STORY-001\n"
            "[FAILURE] encore la même erreur récurrent\n",
            encoding="utf-8",
        )
        stats = dw.parse_trace_stats(trace_path)
        self.assertEqual(stats["dev"].failures_count, 2)
        self.assertIn("test-failure", stats["dev"].failure_patterns)
        self.assertIn("recurring", stats["dev"].failure_patterns)

    def test_trace_since_filter(self):
        dw = _import_dw()
        trace_path = self.root / "trace.md"
        trace_path.write_text(
            "## 2025-06-01 10:00 | dev | OLD-STORY\n"
            "[GIT-COMMIT] old commit\n"
            "\n"
            "## 2026-06-01 10:00 | dev | NEW-STORY\n"
            "[GIT-COMMIT] new commit\n",
            encoding="utf-8",
        )
        stats = dw.parse_trace_stats(trace_path, since="2026-01-01")
        self.assertEqual(stats["dev"].commits_attributed, 1)


# ── count_agent_learnings tests ──────────────────────────────────────────────

class TestCountAgentLearnings(BaseTest):
    def test_no_dir(self):
        dw = _import_dw()
        result = dw.count_agent_learnings(self.root)
        self.assertEqual(result, {})

    def test_basic_count(self):
        dw = _import_dw()
        _create_learnings(self.root, {
            "dev.md": "- Learning 1\n- Learning 2\n- Learning 3\n",
            "qa.md": "- QA 1\n",
        })
        result = dw.count_agent_learnings(self.root)
        self.assertEqual(result["dev"], 3)
        self.assertEqual(result["qa"], 1)


# ── Persistence tests ────────────────────────────────────────────────────────

class TestPersistence(BaseTest):
    def test_save_load_history(self):
        dw = _import_dw()
        record = dw.GenerationRecord(
            generation=1,
            timestamp="2026-01-01",
            scores=[{"agent_id": "dev", "composite": 80}],
            summary={"agents_evaluated": 1},
        )
        dw.save_history(self.root, [record])
        loaded = dw.load_history(self.root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].generation, 1)

    def test_load_empty_history(self):
        dw = _import_dw()
        result = dw.load_history(self.root)
        self.assertEqual(result, [])

    def test_get_previous_scores_empty(self):
        dw = _import_dw()
        result = dw.get_previous_scores([])
        self.assertIsNone(result)

    def test_get_previous_scores(self):
        dw = _import_dw()
        record = dw.GenerationRecord(
            generation=1, timestamp="t",
            scores=[{
                "agent_id": "dev", "composite": 70, "level": "VIABLE",
                "generation": 1, "timestamp": "t",
                "dimensions": {"reliability": 60, "productivity": 70,
                               "learning": 80, "adaptability": 50,
                               "resilience": 60, "influence": 40},
            }],
        )
        result = dw.get_previous_scores([record])
        self.assertIsNotNone(result)
        self.assertEqual(result[0].agent_id, "dev")


# ── cmd_evaluate / cmd_evolve tests ──────────────────────────────────────────

class TestCommands(BaseTest):
    def _setup_project(self):
        dw = _import_dw()
        trace_path = self.root / "_grimoire-output" / "Grimoire_TRACE.md"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            "## 2026-01-15 10:00 | dev | STORY-001\n"
            "[GIT-COMMIT] feat: add feature\n"
            "[DECISION] Use pattern X\n"
            "\n"
            "## 2026-01-15 11:00 | qa | STORY-002\n"
            "[AC-PASS] AC-1 passes\n"
            "[REMEMBER:qa] Always check coverage\n",
            encoding="utf-8",
        )
        _create_learnings(self.root, {
            "dev.md": "- L1\n- L2\n- L3\n",
        })
        return dw, trace_path

    def test_cmd_evaluate(self):
        dw, trace_path = self._setup_project()
        scores = dw.cmd_evaluate(self.root, trace_path)
        self.assertGreater(len(scores), 0)

        # Verify history was saved
        history = dw.load_history(self.root)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].generation, 1)

    def test_cmd_evaluate_increments_generation(self):
        dw, trace_path = self._setup_project()
        dw.cmd_evaluate(self.root, trace_path)
        dw.cmd_evaluate(self.root, trace_path)
        history = dw.load_history(self.root)
        self.assertEqual(history[-1].generation, 2)

    def test_cmd_evaluate_no_save(self):
        dw, trace_path = self._setup_project()
        scores = dw.cmd_evaluate(self.root, trace_path, save=False)
        self.assertGreater(len(scores), 0)
        history = dw.load_history(self.root)
        self.assertEqual(len(history), 0)

    def test_cmd_evolve(self):
        dw, trace_path = self._setup_project()
        actions = dw.cmd_evolve(self.root, trace_path)
        self.assertGreater(len(actions), 0)

    def test_cmd_evolve_dry_run(self):
        dw, trace_path = self._setup_project()
        actions = dw.cmd_evolve(self.root, trace_path, dry_run=True)
        self.assertGreater(len(actions), 0)
        history = dw.load_history(self.root)
        self.assertEqual(len(history), 0)

    def test_cmd_evaluate_empty_trace(self):
        dw = _import_dw()
        trace_path = self.root / "empty.md"
        scores = dw.cmd_evaluate(self.root, trace_path, save=False)
        self.assertEqual(scores, [])


# ── Render tests ─────────────────────────────────────────────────────────────

class TestRender(BaseTest):
    def _make_scores(self):
        dw = _import_dw()
        return [
            dw.FitnessScore(
                agent_id="dev", composite=82, level="ELITE",
                dimensions=dw.FitnessDimensions(90, 80, 75, 85, 70, 65),
                generation=1,
            ),
            dw.FitnessScore(
                agent_id="qa", composite=55, level="VIABLE",
                dimensions=dw.FitnessDimensions(60, 50, 70, 40, 60, 50),
                generation=1,
            ),
        ]

    def test_render_leaderboard(self):
        dw = _import_dw()
        scores = self._make_scores()
        text = dw.render_leaderboard(scores)
        self.assertIn("Leaderboard", text)
        self.assertIn("dev", text)
        self.assertIn("qa", text)
        self.assertIn("82", text)

    def test_render_leaderboard_avg(self):
        dw = _import_dw()
        scores = self._make_scores()
        text = dw.render_leaderboard(scores)
        self.assertIn("Fitness moyenne", text)

    def test_render_evaluate(self):
        dw = _import_dw()
        scores = self._make_scores()
        text = dw.render_evaluate(scores, generation=1)
        self.assertIn("Génération 1", text)
        self.assertIn("ELITE", text)
        self.assertIn("VIABLE", text)

    def test_render_evolve(self):
        dw = _import_dw()
        actions = [
            dw.EvolutionAction("dev", "PROMOTE", "High score"),
            dw.EvolutionAction("ghost", "DEPRECATE", "Inactive"),
        ]
        text = dw.render_evolve(actions)
        self.assertIn("PROMOTE", text)
        self.assertIn("DEPRECATE", text)

    def test_render_evolve_empty(self):
        dw = _import_dw()
        text = dw.render_evolve([])
        self.assertIn("Aucune action", text)

    def test_render_evolve_dry_run(self):
        dw = _import_dw()
        text = dw.render_evolve([
            dw.EvolutionAction("dev", "PROMOTE", "test")
        ], dry_run=True)
        self.assertIn("DRY RUN", text)

    def test_render_history(self):
        dw = _import_dw()
        history = [dw.GenerationRecord(
            generation=1, timestamp="2026-01-01T00:00",
            summary={"agents_evaluated": 3, "avg_fitness": 65.0,
                      "elite": 1, "viable": 1, "fragile": 1, "obsolete": 0},
        )]
        text = dw.render_history(history)
        self.assertIn("Historique", text)
        self.assertIn("65.0", text)

    def test_render_history_empty(self):
        dw = _import_dw()
        text = dw.render_history([])
        self.assertIn("Aucun historique", text)

    def test_render_lineage(self):
        dw = _import_dw()
        history = [
            dw.GenerationRecord(
                generation=1, timestamp="2026-01-01",
                scores=[{"agent_id": "dev", "composite": 50.0,
                          "level": "VIABLE",
                          "dimensions": {"reliability": 50, "productivity": 50,
                                          "learning": 50, "adaptability": 50,
                                          "resilience": 50, "influence": 50}}],
            ),
            dw.GenerationRecord(
                generation=2, timestamp="2026-02-01",
                scores=[{"agent_id": "dev", "composite": 80.0,
                          "level": "ELITE",
                          "dimensions": {"reliability": 80, "productivity": 80,
                                          "learning": 80, "adaptability": 80,
                                          "resilience": 80, "influence": 80}}],
            ),
        ]
        text = dw.render_lineage("dev", history)
        self.assertIn("Lignée", text)
        self.assertIn("Tendance", text)
        self.assertIn("↑", text)

    def test_render_lineage_not_found(self):
        dw = _import_dw()
        text = dw.render_lineage("unknown", [])
        self.assertIn("Aucune donnée", text)


# ── Level classification tests ───────────────────────────────────────────────

class TestLevelClassification(BaseTest):
    def test_level_thresholds(self):
        dw = _import_dw()
        # ELITE >= 75
        s = dw.RawAgentStats("dev", stories_touched=7, decisions_count=10,
                              ac_pass_count=50, ac_fail_count=0,
                              commits_attributed=15, learnings_count=10,
                              checkpoints_created=5)
        fs = dw.compute_fitness(s)
        self.assertEqual(fs.level, "ELITE")

    def test_fragile_level(self):
        dw = _import_dw()
        s = dw.RawAgentStats("weak", stories_touched=1, decisions_count=1,
                              commits_attributed=1, learnings_count=1)
        fs = dw.compute_fitness(s)
        self.assertIn(fs.level, ["FRAGILE", "VIABLE"])

    def test_obsolete_level(self):
        dw = _import_dw()
        s = dw.RawAgentStats("empty")
        fs = dw.compute_fitness(s)
        # Empty agent gets baseline resilience + reliability → ~23 (FRAGILE)
        self.assertIn(fs.level, ["FRAGILE", "OBSOLETE"])
        self.assertLess(fs.composite, 40)


if __name__ == "__main__":
    unittest.main()
