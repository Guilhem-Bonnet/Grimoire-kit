#!/usr/bin/env python3
"""
Tests avancés pour context-guard.py — intégration avec tmpdir.

Fonctions testées :
  - resolve_agent_loads()
  - compute_budget()
  - find_agents()
  - analyze_file_for_optimize()
  - parse_model_affinity()
  - generate_recommendations()
  - load_available_models()
  - do_optimize() (capture stdout)
  - score_model_for_agent() — cas étendus
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_cg():
    return importlib.import_module("context-guard")


class TestResolveAgentLoads(unittest.TestCase):
    """Integration tests for resolve_agent_loads()."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Créer la structure minimale
        agents_dir = self.tmpdir / "_grimoire/_config/custom/agents"
        agents_dir.mkdir(parents=True)
        (self.tmpdir / "framework").mkdir(parents=True)
        (self.tmpdir / "_grimoire/_memory").mkdir(parents=True)

        # Agent file
        self.agent_path = agents_dir / "test-agent.md"
        self.agent_path.write_text("# Test Agent\n<activation critical='MANDATORY'>\nNEVER break character\n")

        # agent-base.md
        (self.tmpdir / "framework" / "agent-base.md").write_text("# Base Protocol\nThis is the base.\n" * 50)

        # project-context.yaml
        (self.tmpdir / "project-context.yaml").write_text("project:\n  name: TestProject\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_loads_agent_file(self):
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        agent_loads = [item for item in loads if item.role == "agent-definition"]
        self.assertEqual(len(agent_loads), 1)
        self.assertEqual(agent_loads[0].path, self.agent_path)

    def test_loads_base_protocol(self):
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        base_loads = [item for item in loads if item.role == "base-protocol"]
        self.assertEqual(len(base_loads), 1)

    def test_loads_project_context(self):
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        proj_loads = [item for item in loads if item.role == "project"]
        self.assertEqual(len(proj_loads), 1)

    def test_loads_memory_files(self):
        # Créer un fichier mémoire agent-spécifique
        mem = self.tmpdir / "_grimoire/_memory/test-agent-learnings.md"
        mem.write_text("- learned something\n- learned another\n")
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        mem_loads = [item for item in loads if item.role == "memory"]
        self.assertGreaterEqual(len(mem_loads), 1)

    def test_loads_failure_museum(self):
        museum = self.tmpdir / "_grimoire/_memory/failure-museum.md"
        museum.write_text("# Failure Museum\n## Error 1\n- description\n")
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        mem_loads = [item for item in loads if item.role == "memory" and "failure" in str(item.path)]
        self.assertEqual(len(mem_loads), 1)

    def test_trace_partial_load(self):
        # Créer un gros TRACE (300 lines)
        trace_dir = self.tmpdir / "_grimoire-output"
        trace_dir.mkdir(parents=True)
        trace = trace_dir / "Grimoire_TRACE.md"
        trace.write_text("\n".join(f"Line {i}" for i in range(300)))
        loads = self.cg.resolve_agent_loads(self.agent_path, self.tmpdir)
        trace_loads = [item for item in loads if item.role == "trace"]
        self.assertEqual(len(trace_loads), 1)
        if trace_loads[0].loaded:
            # Should only have ~200 last lines
            self.assertIn("Line 299", trace_loads[0].content)

    def test_no_crash_with_missing_dirs(self):
        empty = Path(tempfile.mkdtemp())
        loads = self.cg.resolve_agent_loads(self.agent_path, empty)
        self.assertIsInstance(loads, list)
        self.assertGreater(len(loads), 0)
        shutil.rmtree(empty, ignore_errors=True)


class TestComputeBudget(unittest.TestCase):
    """Test compute_budget() — end-to-end budget calculation."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())
        agents_dir = self.tmpdir / "_grimoire/_config/custom/agents"
        agents_dir.mkdir(parents=True)
        (self.tmpdir / "framework").mkdir(parents=True)
        (self.tmpdir / "_grimoire/_memory").mkdir(parents=True)

        self.agent_path = agents_dir / "atlas.md"
        self.agent_path.write_text("# Atlas Agent\n<activation critical='MANDATORY'>\nNEVER break character\n" * 100)
        (self.tmpdir / "framework" / "agent-base.md").write_text("# Base\n" * 200)
        (self.tmpdir / "project-context.yaml").write_text("project:\n  name: Test\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_agent_budget(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "copilot")
        self.assertIsInstance(budget, self.cg.AgentBudget)
        self.assertEqual(budget.agent_id, "atlas")
        self.assertEqual(budget.model, "copilot")

    def test_total_tokens_positive(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "copilot")
        self.assertGreater(budget.total_tokens, 0)

    def test_pct_reasonable(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "copilot")
        self.assertGreater(budget.pct, 0)
        self.assertLess(budget.pct, 100)

    def test_status_ok_for_small_agent(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "gemini-1.5-pro")
        # With a 1M context window, a small agent should be OK
        self.assertEqual(budget.status, "OK")

    def test_different_models_different_pct(self):
        budget_big = self.cg.compute_budget(self.agent_path, self.tmpdir, "gemini-1.5-pro")
        budget_small = self.cg.compute_budget(self.agent_path, self.tmpdir, "llama3")
        self.assertLess(budget_big.pct, budget_small.pct)

    def test_remaining_tokens(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "copilot")
        expected_remaining = budget.model_window - budget.total_tokens
        self.assertEqual(budget.remaining_tokens, expected_remaining)

    def test_biggest_files(self):
        budget = self.cg.compute_budget(self.agent_path, self.tmpdir, "copilot")
        biggest = budget.biggest_files(2)
        self.assertLessEqual(len(biggest), 2)
        if len(biggest) >= 2:
            self.assertGreaterEqual(biggest[0].tokens, biggest[1].tokens)


class TestFindAgents(unittest.TestCase):
    """Test find_agents() — agent discovery."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.custom_dir = self.tmpdir / "_grimoire/_config/custom/agents"
        self.custom_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_finds_agents_with_activation(self):
        (self.custom_dir / "hawk.md").write_text(
            "<activation critical='MANDATORY'>\nYou are Hawk.\n"
        )
        agents = self.cg.find_agents(self.tmpdir)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].stem, "hawk")

    def test_finds_agents_with_never_break(self):
        (self.custom_dir / "forge.md").write_text(
            "You must NEVER break character until given an exit command.\n"
        )
        agents = self.cg.find_agents(self.tmpdir)
        self.assertEqual(len(agents), 1)

    def test_ignores_templates(self):
        (self.custom_dir / "custom-agent.tpl.md").write_text(
            "<activation critical='MANDATORY'>\n"
        )
        agents = self.cg.find_agents(self.tmpdir)
        self.assertEqual(len(agents), 0)

    def test_ignores_non_agent_md(self):
        (self.custom_dir / "README.md").write_text("# Readme\nThis is not an agent.\n")
        agents = self.cg.find_agents(self.tmpdir)
        self.assertEqual(len(agents), 0)

    def test_finds_in_archetypes(self):
        arch_dir = self.tmpdir / "archetypes/web-app/agents"
        arch_dir.mkdir(parents=True)
        (arch_dir / "fullstack-dev.md").write_text(
            "NEVER break character\nYou are a fullstack dev.\n"
        )
        agents = self.cg.find_agents(self.tmpdir)
        self.assertEqual(len(agents), 1)

    def test_empty_project(self):
        empty = Path(tempfile.mkdtemp())
        agents = self.cg.find_agents(empty)
        self.assertEqual(len(agents), 0)
        shutil.rmtree(empty, ignore_errors=True)


class TestAnalyzeFileForOptimize(unittest.TestCase):
    """Test analyze_file_for_optimize()."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_heavy_comments_python(self):
        f = self.tmpdir / "heavy.py"
        # Need > 500 tokens (~4 chars/token), so generate enough content
        lines = ["# This is a long comment line number " + str(i) + " with padding text words" for i in range(200)]
        lines += ["code_variable_name_" + str(i) + " = 'value'" for i in range(50)]
        f.write_text("\n".join(lines))
        hints = self.cg.analyze_file_for_optimize(f, "agent-definition")
        comment_hints = [h for h in hints if h.category == "comments"]
        self.assertGreater(len(comment_hints), 0)

    def test_heavy_comments_yaml(self):
        f = self.tmpdir / "heavy.yaml"
        lines = ["# This is a yaml comment line number " + str(i) + " with extra padding" for i in range(200)]
        lines += [f"key_name_{i}: value_content_{i}" for i in range(50)]
        f.write_text("\n".join(lines))
        hints = self.cg.analyze_file_for_optimize(f, "agent-definition")
        comment_hints = [h for h in hints if h.category == "comments"]
        self.assertGreater(len(comment_hints), 0)

    def test_small_file_ignored(self):
        f = self.tmpdir / "small.py"
        f.write_text("x = 1\n")
        hints = self.cg.analyze_file_for_optimize(f, "agent-definition")
        self.assertEqual(len(hints), 0)

    def test_shared_heavy_protocol(self):
        f = self.tmpdir / "agent-base.md"
        f.write_text("# Protocol\n" * 1000)
        hints = self.cg.analyze_file_for_optimize(f, "base-protocol")
        shared = [h for h in hints if h.category == "shared-heavy"]
        self.assertGreater(len(shared), 0)

    def test_extractable_code_block(self):
        f = self.tmpdir / "readme.md"
        # Need > 500 tokens total AND code block > 15 lines with > 200 tokens
        lines = ["# README with enough content to exceed token threshold\n\n"]
        lines += [f"This is paragraph line {i} with enough words to generate tokens.\n" for i in range(40)]
        lines += ["\n```python\n"]
        lines += [f"variable_name_{i} = some_function_call(argument_{i}, option_{i})\n" for i in range(25)]
        lines += ["```\n\n", "More text after the code block.\n"]
        f.write_text("".join(lines))
        hints = self.cg.analyze_file_for_optimize(f, "agent-definition")
        extractable = [h for h in hints if h.category == "extractable"]
        self.assertGreater(len(extractable), 0)

    def test_missing_file(self):
        hints = self.cg.analyze_file_for_optimize(
            self.tmpdir / "nonexistent.py", "agent-definition"
        )
        self.assertEqual(len(hints), 0)


class TestParseModelAffinity(unittest.TestCase):
    """Test parse_model_affinity() — YAML frontmatter parsing."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parses_valid_frontmatter(self):
        f = self.tmpdir / "agent-with-affinity.md"
        f.write_text(
            "---\n"
            "name: test-agent\n"
            "model_affinity:\n"
            "  reasoning: extreme\n"
            "  context_window: large\n"
            "  speed: slow-ok\n"
            "  cost: any\n"
            "---\n"
            "# Agent Content\n"
        )
        affinity = self.cg.parse_model_affinity(f)
        self.assertIsNotNone(affinity)
        self.assertEqual(affinity.reasoning, "extreme")
        self.assertEqual(affinity.context_window, "large")
        self.assertEqual(affinity.speed, "slow-ok")
        self.assertEqual(affinity.cost, "any")

    def test_returns_none_without_affinity(self):
        f = self.tmpdir / "no-affinity.md"
        f.write_text(
            "---\n"
            "name: basic-agent\n"
            "---\n"
            "Content\n"
        )
        affinity = self.cg.parse_model_affinity(f)
        self.assertIsNone(affinity)

    def test_returns_none_without_frontmatter(self):
        f = self.tmpdir / "no-fm.md"
        f.write_text("# Agent\nNo frontmatter here.\n")
        affinity = self.cg.parse_model_affinity(f)
        self.assertIsNone(affinity)

    def test_missing_file(self):
        affinity = self.cg.parse_model_affinity(self.tmpdir / "nope.md")
        self.assertIsNone(affinity)

    def test_defaults_for_partial_affinity(self):
        f = self.tmpdir / "partial.md"
        f.write_text(
            "---\n"
            "model_affinity:\n"
            "  reasoning: high\n"
            "---\n"
        )
        affinity = self.cg.parse_model_affinity(f)
        self.assertIsNotNone(affinity)
        self.assertEqual(affinity.reasoning, "high")
        self.assertEqual(affinity.context_window, "medium")  # default


class TestGenerateRecommendations(unittest.TestCase):
    """Test generate_recommendations()."""

    def setUp(self):
        self.cg = _import_cg()

    def test_no_budgets(self):
        recs = self.cg.generate_recommendations([])
        self.assertEqual(len(recs), 0)

    def test_critical_budget_generates_urgent(self):
        loads = [
            self.cg.FileLoad(path=Path("heavy.md"), role="agent-definition",
                             tokens=150_000, loaded=True),
        ]
        budget = self.cg.AgentBudget(
            agent_id="overloaded",
            agent_path=Path("overloaded.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        recs = self.cg.generate_recommendations([budget])
        urgent = [r for r in recs if "URGENT" in r]
        self.assertGreater(len(urgent), 0)

    def test_shared_heavy_file(self):
        shared = self.cg.FileLoad(
            path=Path("agent-base.md"), role="base-protocol",
            tokens=8000, loaded=True,
        )
        budgets = []
        for i in range(3):
            b = self.cg.AgentBudget(
                agent_id=f"agent-{i}",
                agent_path=Path(f"agent-{i}.md"),
                model="copilot",
                model_window=200_000,
                loads=[
                    self.cg.FileLoad(path=Path(f"a{i}.md"), role="agent-definition",
                                     tokens=2000, loaded=True),
                    shared,
                ],
            )
            budgets.append(b)
        recs = self.cg.generate_recommendations(budgets)
        shared_recs = [r for r in recs if "agent-base.md" in r]
        self.assertGreater(len(shared_recs), 0)

    def test_max_6_recommendations(self):
        # Even with many issues, limit to 6
        loads = [
            self.cg.FileLoad(path=Path("huge.md"), role="trace",
                             tokens=180_000, loaded=True),
        ]
        budget = self.cg.AgentBudget(
            agent_id="mega",
            agent_path=Path("mega.md"),
            model="copilot",
            model_window=200_000,
            loads=loads,
        )
        recs = self.cg.generate_recommendations([budget])
        self.assertLessEqual(len(recs), 6)


class TestScoreModelExtended(unittest.TestCase):
    """Extended tests for score_model_for_agent()."""

    def setUp(self):
        self.cg = _import_cg()

    def test_economy_penalty(self):
        """Economy models should be penalized for Grimoire agents."""
        profile = self.cg.MODEL_PROFILES["llama3"]
        affinity = self.cg.ModelAffinity(reasoning="medium", context_window="small",
                                          speed="fast", cost="cheap")
        score = self.cg.score_model_for_agent(profile, affinity, 5000)
        # Even when the affinity matches, economy penalty should reduce score
        self.assertLessEqual(score, 80)

    def test_codex_for_dev_agent(self):
        """Codex should score well for a dev agent needing high reasoning."""
        profile = self.cg.MODEL_PROFILES["codex"]
        affinity = self.cg.ModelAffinity(reasoning="high", context_window="medium",
                                          speed="medium", cost="medium")
        score = self.cg.score_model_for_agent(profile, affinity, 30000)
        self.assertGreater(score, 60)

    def test_window_too_small_critical(self):
        """Agent needing more tokens than window should score very low."""
        profile = self.cg.MODEL_PROFILES["llama3"]  # 8K window
        affinity = self.cg.ModelAffinity(reasoning="low", context_window="large",
                                          speed="fast", cost="cheap")
        score = self.cg.score_model_for_agent(profile, affinity, 10000)
        self.assertLess(score, 30)

    def test_overkill_premium_penalized(self):
        """Using opus for a simple medium-reasoning task should get cost penalty."""
        profile = self.cg.MODEL_PROFILES["claude-opus-4"]
        affinity = self.cg.ModelAffinity(reasoning="medium", context_window="medium",
                                          speed="medium", cost="cheap")
        score_opus = self.cg.score_model_for_agent(profile, affinity, 10000)

        profile_sonnet = self.cg.MODEL_PROFILES["claude-sonnet-4"]
        score_sonnet = self.cg.score_model_for_agent(profile_sonnet, affinity, 10000)
        # Sonnet should score higher for a medium/cheap requirement
        self.assertGreaterEqual(score_sonnet, score_opus)

    def test_score_bounded(self):
        """Score should always be between 0 and 100."""
        for model_id, profile in self.cg.MODEL_PROFILES.items():
            for reasoning in ("low", "medium", "high", "extreme"):
                affinity = self.cg.ModelAffinity(reasoning=reasoning)
                score = self.cg.score_model_for_agent(profile, affinity, 10000)
                self.assertGreaterEqual(score, 0, f"Score negative for {model_id}/{reasoning}")
                self.assertLessEqual(score, 100, f"Score >100 for {model_id}/{reasoning}")


class TestLoadAvailableModels(unittest.TestCase):
    """Test load_available_models()."""

    def setUp(self):
        self.cg = _import_cg()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_project_context(self):
        result = self.cg.load_available_models(self.tmpdir)
        self.assertIsNone(result)

    def test_parses_models_section(self):
        (self.tmpdir / "project-context.yaml").write_text(
            "project:\n"
            "  name: TestProject\n"
            "models:\n"
            "  available:\n"
            '    - id: "gpt-4o"\n'
            '      routing: "default"\n'
            '    - id: "claude-sonnet-4"\n'
            '      routing: "complex"\n'
        )
        result = self.cg.load_available_models(self.tmpdir)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        ids = {m["id"] for m in result}
        self.assertIn("gpt-4o", ids)
        self.assertIn("claude-sonnet-4", ids)


if __name__ == "__main__":
    unittest.main()
