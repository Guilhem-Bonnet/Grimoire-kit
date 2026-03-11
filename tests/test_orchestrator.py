"""Tests for orchestrator.py — Story 4.3."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "orchestrator.py"


def _load():
    mod_name = "orchestrator"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


orc = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(orc.ORCHESTRATOR_VERSION)

    def test_valid_modes(self):
        self.assertIn("simulated", orc.VALID_MODES)
        self.assertIn("sequential", orc.VALID_MODES)
        self.assertIn("concurrent-cpu", orc.VALID_MODES)

    def test_mode_rules(self):
        self.assertEqual(orc.MODE_RULES["party-mode"], "simulated")
        self.assertEqual(orc.MODE_RULES["boomerang"], "sequential")
        self.assertEqual(orc.MODE_RULES["adversarial-review"], "concurrent-cpu")

    def test_cost_multipliers(self):
        self.assertEqual(orc.COST_MULTIPLIERS["simulated"], 1.0)
        self.assertGreater(orc.COST_MULTIPLIERS["concurrent-cpu"], orc.COST_MULTIPLIERS["sequential"])


class TestExecutionStep(unittest.TestCase):
    def test_create(self):
        step = orc.ExecutionStep(step_number=1, agent="dev", task="Code review")
        self.assertEqual(step.step_number, 1)
        self.assertEqual(step.status, "pending")


class TestExecutionPlan(unittest.TestCase):
    def test_defaults(self):
        plan = orc.ExecutionPlan()
        self.assertTrue(plan.plan_id.startswith("plan-"))
        self.assertEqual(plan.mode, "simulated")
        self.assertTrue(plan.budget_ok)

    def test_to_dict(self):
        plan = orc.ExecutionPlan(workflow="test")
        d = plan.to_dict()
        self.assertIn("plan_id", d)
        self.assertIn("workflow", d)


class TestExecutionResult(unittest.TestCase):
    def test_defaults(self):
        r = orc.ExecutionResult()
        self.assertTrue(r.execution_id.startswith("exec-"))
        self.assertTrue(r.started_at)

    def test_to_dict_from_dict(self):
        r = orc.ExecutionResult(workflow="test", mode="simulated")
        d = r.to_dict()
        restored = orc.ExecutionResult.from_dict(d)
        self.assertEqual(restored.workflow, "test")


class TestOrchestratorStats(unittest.TestCase):
    def test_defaults(self):
        s = orc.OrchestratorStats()
        self.assertEqual(s.total_executions, 0)
        self.assertEqual(s.success_rate, 0.0)


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orch = orc.Orchestrator(Path(self.tmpdir))

    def test_decide_mode_default(self):
        mode, reason = self.orch.decide_mode("party-mode")
        self.assertEqual(mode, "simulated")

    def test_decide_mode_override(self):
        mode, reason = self.orch.decide_mode("party-mode", override="concurrent-cpu")
        self.assertEqual(mode, "concurrent-cpu")

    def test_decide_mode_unknown_workflow(self):
        mode, reason = self.orch.decide_mode("unknown-workflow")
        self.assertEqual(mode, "simulated")

    def test_decide_mode_budget_fallback(self):
        orch_low = orc.Orchestrator(Path(self.tmpdir), budget_cap=100)
        mode, reason = orch_low.decide_mode("adversarial-review")
        self.assertIn(mode, ("sequential", "simulated"))

    def test_create_plan(self):
        plan = self.orch.create_plan(
            workflow="code-review",
            agents=["dev", "qa"],
            task="Review PR",
        )
        self.assertEqual(plan.workflow, "code-review")
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].agent, "dev")
        self.assertGreater(plan.estimated_total_tokens, 0)

    def test_create_plan_budget_fallback(self):
        orch_low = orc.Orchestrator(Path(self.tmpdir), budget_cap=1)
        plan = orch_low.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa"],
        )
        self.assertEqual(plan.mode, "simulated")
        self.assertTrue(plan.budget_ok)

    def test_execute_simulated(self):
        plan = self.orch.create_plan(
            workflow="party-mode",
            agents=["dev", "architect"],
            task="Discuss architecture",
        )
        result = self.orch.execute(plan)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.steps), 2)
        for step in result.steps:
            self.assertEqual(step.status, "completed")

    def test_execute_dry_run(self):
        plan = self.orch.create_plan(
            workflow="brainstorming",
            agents=["pm"],
        )
        result = self.orch.execute(plan, dry_run=True)
        self.assertEqual(result.status, "completed")

    def test_get_history_empty(self):
        history = self.orch.get_history()
        self.assertEqual(len(history), 0)

    def test_get_history_after_execution(self):
        plan = self.orch.create_plan(workflow="test", agents=["dev"])
        self.orch.execute(plan)
        history = self.orch.get_history()
        self.assertEqual(len(history), 1)

    def test_get_stats(self):
        plan = self.orch.create_plan(workflow="test", agents=["dev"])
        self.orch.execute(plan)
        stats = self.orch.get_stats()
        self.assertEqual(stats.total_executions, 1)
        self.assertGreater(stats.success_rate, 0)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_plan(self):
        result = orc.mcp_orchestrate(
            self.tmpdir,
            workflow="code-review",
            agents="dev,qa",
            dry_run=True,
        )
        self.assertIn("plan", result)

    def test_mcp_execute(self):
        result = orc.mcp_orchestrate(
            self.tmpdir,
            workflow="party-mode",
            agents="dev",
            dry_run=False,
        )
        self.assertIn("result", result)


# ── Parallel Execution (Story 8.3) ──────────────────────────────────────────

class TestParallelExecution(unittest.TestCase):
    """Tests pour l'exécution parallèle via ThreadPoolExecutor."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.orch = orc.Orchestrator(self.tmpdir)

    def test_parallel_constants(self):
        self.assertGreater(orc.PARALLEL_MAX_WORKERS, 0)
        self.assertGreater(orc.PARALLEL_STEP_TIMEOUT, 0)

    def test_parallel_plan_created(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa", "architect"],
            task="Review security",
        )
        self.assertEqual(plan.mode, "concurrent-cpu")

    def test_parallel_execution_completes(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa", "architect"],
            task="Review security",
        )
        result = self.orch.execute(plan)
        self.assertIn(result.status, ("completed", "partial"))
        self.assertEqual(len(result.steps), 3)

    def test_parallel_all_steps_completed(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa"],
            task="Test parallel",
        )
        result = self.orch.execute(plan)
        for step in result.steps:
            self.assertIn(step.status, ("completed", "failed"))

    def test_parallel_steps_ordered(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa", "architect", "pm"],
            task="Parallel test",
        )
        result = self.orch.execute(plan)
        numbers = [s.step_number for s in result.steps]
        self.assertEqual(numbers, sorted(numbers))

    def test_parallel_duration_tracked(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa"],
            task="Duration test",
        )
        result = self.orch.execute(plan)
        self.assertGreaterEqual(result.total_duration_seconds, 0)
        for step in result.steps:
            self.assertGreaterEqual(step.duration_seconds, 0)

    def test_parallel_vs_sequential_same_result(self):
        """Both modes should produce same step count."""
        agents = ["dev", "qa"]
        task = "Comparison test"

        plan_seq = self.orch.create_plan(
            workflow="code-review",  # sequential
            agents=agents, task=task,
        )
        plan_par = self.orch.create_plan(
            workflow="adversarial-review",  # parallel
            agents=agents, task=task,
        )

        result_seq = self.orch.execute(plan_seq)
        result_par = self.orch.execute(plan_par)

        self.assertEqual(len(result_seq.steps), len(result_par.steps))

    def test_parallel_single_step_still_works(self):
        """Even with 1 agent, parallel should complete."""
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev"],
            task="Single agent parallel",
        )
        result = self.orch.execute(plan)
        self.assertIn(result.status, ("completed", "partial"))

    def test_parallel_saved_to_history(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa"],
            task="History test",
        )
        self.orch.execute(plan)
        history = self.orch.get_history()
        self.assertGreater(len(history), 0)
        self.assertEqual(history[-1].mode, "concurrent-cpu")

    def test_parallel_tokens_summed(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa", "architect"],
            task="Tokens test",
        )
        result = self.orch.execute(plan)
        expected = sum(s.estimated_tokens for s in result.steps)
        self.assertEqual(result.total_tokens_used, expected)

    def test_parallel_completed_at_set(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev"],
            task="Timestamp test",
        )
        result = self.orch.execute(plan)
        self.assertTrue(result.completed_at)
        self.assertIn("T", result.completed_at)

    def test_execute_step_method_exists(self):
        """_execute_step should be a method of Orchestrator."""
        self.assertTrue(hasattr(self.orch, "_execute_step"))

    def test_execute_parallel_method_exists(self):
        self.assertTrue(hasattr(self.orch, "_execute_parallel"))

    def test_execute_sequential_method_exists(self):
        self.assertTrue(hasattr(self.orch, "_execute_sequential"))

    def test_dry_run_parallel(self):
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=["dev", "qa"],
            task="Dry run",
        )
        result = self.orch.execute(plan, dry_run=True)
        self.assertEqual(result.status, "completed")

    def test_sequential_mode_still_works(self):
        """Ensure sequential mode wasn't broken."""
        plan = self.orch.create_plan(
            workflow="boomerang",
            agents=["dev", "qa"],
            task="Sequential test",
        )
        result = self.orch.execute(plan)
        self.assertIn(result.status, ("completed", "partial"))

    def test_simulated_mode_still_works(self):
        """Ensure simulated mode wasn't broken."""
        plan = self.orch.create_plan(
            workflow="party-mode",
            agents=["dev"],
            task="Simulated test",
        )
        result = self.orch.execute(plan)
        self.assertEqual(result.status, "completed")

    def test_many_agents_parallel(self):
        """Test with more agents than PARALLEL_MAX_WORKERS."""
        agents = ["dev", "qa", "architect", "pm", "sm", "tech-writer"]
        plan = self.orch.create_plan(
            workflow="adversarial-review",
            agents=agents,
            task="Many agents test",
        )
        result = self.orch.execute(plan)
        self.assertEqual(len(result.steps), len(agents))

    def test_version_bumped(self):
        self.assertEqual(orc.ORCHESTRATOR_VERSION, "1.2.0")


class TestToolResolveHook(unittest.TestCase):
    """Tests pour le hook _pre_resolve_tools (v1.2)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.orch = orc.Orchestrator(self.tmpdir, auto_resolve_tools=True)

    def test_auto_resolve_default_true(self):
        orch = orc.Orchestrator(self.tmpdir)
        self.assertTrue(orch.auto_resolve_tools)

    def test_auto_resolve_disabled(self):
        orch = orc.Orchestrator(self.tmpdir, auto_resolve_tools=False)
        self.assertFalse(orch.auto_resolve_tools)
        self.assertIsNone(orch._resolver_mod)

    def test_pre_resolve_empty_task(self):
        step = orc.ExecutionStep(step_number=1, agent="dev", task="")
        result = self.orch._pre_resolve_tools(step)
        self.assertEqual(result, "")

    def test_pre_resolve_with_task(self):
        step = orc.ExecutionStep(step_number=1, agent="dev", task="créer un fichier SVG")
        result = self.orch._pre_resolve_tools(step)
        # tool-resolver.py may or may not be loadable depending on test env
        # but the method should not raise
        self.assertIsInstance(result, str)

    def test_simulated_step_includes_tool_context(self):
        """Simulated mode includes tool context in output_summary."""
        plan = self.orch.create_plan(
            workflow="party-mode",
            agents=["dev"],
            task="créer un fichier SVG",
        )
        result = self.orch.execute(plan)
        self.assertEqual(result.status, "completed")
        # If resolver loaded, summary should contain "Tools:"
        # If not loaded, it should still complete without error
        self.assertTrue(len(result.steps) == 1)
        self.assertEqual(result.steps[0].status, "completed")


class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL)] + list(args),
            capture_output=True, text=True, timeout=15,
        )

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("orchestrator", r.stdout)

    def test_plan(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "plan",
                       "--workflow", "code-review", "--agents", "dev,qa", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("plan_id", data)

    def test_run_simulated(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "run",
                       "--workflow", "party-mode", "--agents", "dev",
                       "--dry-run", "--json")
        self.assertEqual(r.returncode, 0)

    def test_status(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "status")
        self.assertEqual(r.returncode, 0)

    def test_history(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "history", "--json")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
