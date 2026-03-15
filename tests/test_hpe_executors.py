#!/usr/bin/env python3
"""
Tests pour hpe-executors.py — HPE Execution Backends (BM-58 / BM-19).

Fonctions testées :
  - ExecutionTrace / BackendCapability  (dataclasses)
  - save_trace()
  - detect_backends() / best_available_backend()
  - DryRunExecutor
  - SequentialExecutor (sans LLM, avec LLM callback, avec LLM failure)
  - MCPExecutor (sans agent-caller, avec mock)
  - AutoExecutor (auto-détection, fallback MCP→Sequential, force_backend)
  - Factory functions (auto_executor, dry_executor, sequential_executor, mcp_executor)
  - mcp_hpe_detect_backends() / mcp_hpe_execute_task()
  - cmd_detect() / cmd_test()
  - main()
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
from unittest.mock import MagicMock, patch

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "hpe-executors.py"


def _import_mod():
    """Import le module hpe-executors via importlib."""
    mod_name = "hpe_executors"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "hpe-executors.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_project(root: Path) -> Path:
    """Créer un projet minimal avec répertoire HPE."""
    (root / "_grimoire-output" / ".hpe").mkdir(parents=True, exist_ok=True)
    return root


def _sample_task(task_id="t1", agent="dev", task_desc="Implement feature X",
                 depends_on=None, output_key="out_1"):
    """Crée un dict de tâche HPE pour tests."""
    return {
        "id": task_id,
        "agent": agent,
        "task": task_desc,
        "depends_on": depends_on or [],
        "output_key": output_key,
        "priority": "medium",
        "mode": "parallel",
        "status": "pending",
    }


def _sample_outputs():
    """Outputs accumulés d'exemple."""
    return {
        "analysis": {"findings": ["F1", "F2"]},
        "architecture": {"adr": "ADR-001"},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Dataclass tests
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutionTrace(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_id(self):
        t = self.mod.ExecutionTrace()
        self.assertTrue(t.trace_id.startswith("tr-"))

    def test_auto_timestamp(self):
        t = self.mod.ExecutionTrace()
        self.assertTrue(t.timestamp)

    def test_defaults(self):
        t = self.mod.ExecutionTrace()
        self.assertEqual(t.status, "")
        self.assertEqual(t.backend, "")
        self.assertEqual(t.tokens_used, 0)


class TestBackendCapability(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        b = self.mod.BackendCapability()
        self.assertEqual(b.name, "")
        self.assertFalse(b.available)
        self.assertEqual(b.priority, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Trace Writer
# ══════════════════════════════════════════════════════════════════════════════


class TestSaveTrace(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_creates_file(self):
        trace = self.mod.ExecutionTrace(
            task_id="t1", agent="dev", backend="dry-run", status="success",
        )
        self.mod.save_trace(self.tmp, trace)
        trace_dir = self.tmp / self.mod.TRACE_DIR
        self.assertTrue(trace_dir.exists())
        files = list(trace_dir.glob("*.json"))
        self.assertEqual(len(files), 1)

    def test_save_valid_json(self):
        trace = self.mod.ExecutionTrace(task_id="t1")
        self.mod.save_trace(self.tmp, trace)
        trace_dir = self.tmp / self.mod.TRACE_DIR
        f = next(iter(trace_dir.glob("*.json")))
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["task_id"], "t1")


# ══════════════════════════════════════════════════════════════════════════════
# Backend Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectBackends(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_always_has_sequential(self):
        backends = self.mod.detect_backends(self.tmp)
        names = [b.name for b in backends if b.available]
        self.assertIn("sequential", names)

    def test_always_has_dry_run(self):
        backends = self.mod.detect_backends(self.tmp)
        names = [b.name for b in backends if b.available]
        self.assertIn("dry-run", names)

    def test_sorted_by_priority(self):
        backends = self.mod.detect_backends(self.tmp)
        priorities = [b.priority for b in backends]
        self.assertEqual(priorities, sorted(priorities))

    def test_best_available_always_returns(self):
        best = self.mod.best_available_backend(self.tmp)
        self.assertIn(best, ("mcp", "message-bus", "sequential", "dry-run"))


# ══════════════════════════════════════════════════════════════════════════════
# DryRunExecutor
# ══════════════════════════════════════════════════════════════════════════════


class TestDryRunExecutor(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_always_succeeds(self):
        executor = self.mod.DryRunExecutor(self.tmp)
        task = _sample_task()
        success, result = executor(task, {})
        self.assertTrue(success)
        self.assertTrue(result["dry_run"])

    def test_returns_task_info(self):
        executor = self.mod.DryRunExecutor(self.tmp)
        task = _sample_task(task_id="t42", agent="qa")
        _success, result = executor(task, {})
        self.assertEqual(result["task_id"], "t42")
        self.assertEqual(result["agent"], "qa")

    def test_creates_trace(self):
        executor = self.mod.DryRunExecutor(self.tmp)
        executor(_sample_task(), {})
        trace_dir = self.tmp / self.mod.TRACE_DIR
        self.assertGreater(len(list(trace_dir.glob("*.json"))), 0)

    def test_no_trace_without_root(self):
        executor = self.mod.DryRunExecutor(None)
        success, _result = executor(_sample_task(), {})
        self.assertTrue(success)

    def test_with_outputs(self):
        executor = self.mod.DryRunExecutor(self.tmp)
        success, _result = executor(_sample_task(), _sample_outputs())
        self.assertTrue(success)


# ══════════════════════════════════════════════════════════════════════════════
# SequentialExecutor
# ══════════════════════════════════════════════════════════════════════════════


class TestSequentialExecutor(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prompt_only_mode(self):
        """Sans LLM callback, retourne le prompt construit."""
        executor = self.mod.SequentialExecutor(self.tmp)
        task = _sample_task()
        success, result = executor(task, {})
        self.assertTrue(success)
        self.assertEqual(result["mode"], "sequential-prompt")
        self.assertIn("prompt", result)
        self.assertIn("Implement feature X", result["prompt"])

    def test_prompt_includes_context(self):
        executor = self.mod.SequentialExecutor(self.tmp)
        task = _sample_task(depends_on=["analysis"])
        outputs = {"analysis": {"findings": ["bug1"]}}
        success, result = executor(task, outputs)
        self.assertTrue(success)
        self.assertIn("analysis", result["prompt"])
        self.assertIn("bug1", result["prompt"])

    def test_with_llm_callback_success(self):
        """Avec LLM callback, exécute et retourne le résultat."""
        def fake_llm(prompt, agent):
            return {"answer": "done", "agent_used": agent}

        executor = self.mod.SequentialExecutor(self.tmp, llm_callback=fake_llm)
        task = _sample_task(agent="qa")
        success, result = executor(task, {})
        self.assertTrue(success)
        self.assertEqual(result["answer"], "done")
        self.assertEqual(result["agent_used"], "qa")

    def test_with_llm_callback_string_result(self):
        def fake_llm(prompt, agent):
            return "text response"

        executor = self.mod.SequentialExecutor(self.tmp, llm_callback=fake_llm)
        success, result = executor(_sample_task(), {})
        self.assertTrue(success)
        self.assertEqual(result["response"], "text response")

    def test_with_llm_callback_failure(self):
        def bad_llm(prompt, agent):
            raise RuntimeError("LLM down")

        executor = self.mod.SequentialExecutor(self.tmp, llm_callback=bad_llm)
        success, result = executor(_sample_task(), {})
        self.assertFalse(success)
        self.assertIn("LLM down", result["error"])

    def test_creates_trace(self):
        executor = self.mod.SequentialExecutor(self.tmp)
        executor(_sample_task(), {})
        trace_dir = self.tmp / self.mod.TRACE_DIR
        self.assertGreater(len(list(trace_dir.glob("*.json"))), 0)

    def test_prompt_structure_without_deps(self):
        """Sans dépendances, pas de section contexte."""
        executor = self.mod.SequentialExecutor(self.tmp)
        prompt = executor._build_prompt(
            _sample_task(task_id="t5", agent="architect"),
            {},
        )
        self.assertIn("# Tâche : t5", prompt)
        self.assertIn("**Agent** : architect", prompt)
        self.assertIn("## Instructions", prompt)
        self.assertNotIn("## Contexte des tâches précédentes", prompt)

    def test_prompt_structure_with_deps(self):
        """Avec dépendances, la section contexte apparaît."""
        executor = self.mod.SequentialExecutor(self.tmp)
        prompt = executor._build_prompt(
            _sample_task(task_id="t5", agent="architect", depends_on=["prev"]),
            {"prev": {"key": "val"}},
        )
        self.assertIn("# Tâche : t5", prompt)
        self.assertIn("## Contexte des tâches précédentes", prompt)
        self.assertIn("prev", prompt)


# ══════════════════════════════════════════════════════════════════════════════
# MCPExecutor
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPExecutor(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unavailable_graceful(self):
        """Sans agent-caller disponible, échoue proprement."""
        # Patch _load_agent_caller to return None
        with patch.object(self.mod, '_load_agent_caller', return_value=None):
            executor = self.mod.MCPExecutor(self.tmp)
            self.assertFalse(executor.available)
            success, result = executor(_sample_task(), {})
            self.assertFalse(success)
            self.assertIn("non disponible", result["error"])

    def test_available_with_mock_caller(self):
        """Avec un mock agent-caller, exécute via MCP."""
        mock_response = MagicMock()
        mock_response.status = "success"
        mock_response.response = "Task completed"
        mock_response.tokens_used = 500
        mock_response.model_used = "claude-opus-4"
        mock_response.call_id = "call-123"

        mock_caller = MagicMock()
        mock_caller.call.return_value = mock_response

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.mod, '_load_agent_caller', return_value=mock_mod):
            executor = self.mod.MCPExecutor(self.tmp)
            self.assertTrue(executor.available)
            success, result = executor(_sample_task(), {})
            self.assertTrue(success)
            self.assertEqual(result["response"], "Task completed")
            self.assertEqual(result["tokens_used"], 500)

    def test_mcp_call_failure(self):
        """MCP appel échoue → retourne (False, error)."""
        mock_response = MagicMock()
        mock_response.status = "error"
        mock_response.response = "Agent timeout"
        mock_response.tokens_used = 0
        mock_response.model_used = ""

        mock_caller = MagicMock()
        mock_caller.call.return_value = mock_response

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.mod, '_load_agent_caller', return_value=mock_mod):
            executor = self.mod.MCPExecutor(self.tmp)
            success, result = executor(_sample_task(), {})
            self.assertFalse(success)
            self.assertEqual(result["error"], "Agent timeout")

    def test_mcp_call_exception(self):
        """MCP lance une exception → retourne (False, error)."""
        mock_caller = MagicMock()
        mock_caller.call.side_effect = ConnectionError("Server down")

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.mod, '_load_agent_caller', return_value=mock_mod):
            executor = self.mod.MCPExecutor(self.tmp)
            success, result = executor(_sample_task(), _sample_outputs())
            self.assertFalse(success)
            self.assertIn("Server down", result["error"])

    def test_creates_trace_on_success(self):
        mock_response = MagicMock()
        mock_response.status = "success"
        mock_response.response = "OK"
        mock_response.tokens_used = 100
        mock_response.model_used = "test"
        mock_response.call_id = "c1"

        mock_caller = MagicMock()
        mock_caller.call.return_value = mock_response

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.mod, '_load_agent_caller', return_value=mock_mod):
            executor = self.mod.MCPExecutor(self.tmp)
            executor(_sample_task(), {})
        trace_dir = self.tmp / self.mod.TRACE_DIR
        self.assertGreater(len(list(trace_dir.glob("*.json"))), 0)


# ══════════════════════════════════════════════════════════════════════════════
# AutoExecutor
# ══════════════════════════════════════════════════════════════════════════════


class TestAutoExecutor(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallback_to_sequential(self):
        """Sans MCP, tombe sur séquentiel."""
        with patch.object(self.mod, '_load_agent_caller', return_value=None):
            executor = self.mod.AutoExecutor(self.tmp)
            self.assertEqual(executor.active_backend, "sequential")
            success, result = executor(_sample_task(), {})
            self.assertTrue(success)
            self.assertEqual(result["mode"], "sequential-prompt")

    def test_force_dry_run(self):
        executor = self.mod.AutoExecutor(self.tmp, force_backend="dry-run")
        self.assertEqual(executor.active_backend, "dry-run")
        success, result = executor(_sample_task(), {})
        self.assertTrue(success)
        self.assertTrue(result.get("dry_run"))

    def test_force_sequential(self):
        executor = self.mod.AutoExecutor(self.tmp, force_backend="sequential")
        self.assertEqual(executor.active_backend, "sequential")
        success, result = executor(_sample_task(), {})
        self.assertTrue(success)
        self.assertEqual(result["mode"], "sequential-prompt")

    def test_force_mcp_without_caller(self):
        """Forcer MCP sans agent-caller = échec."""
        with patch.object(self.mod, '_load_agent_caller', return_value=None):
            executor = self.mod.AutoExecutor(self.tmp, force_backend="mcp")
            success, _result = executor(_sample_task(), {})
            self.assertFalse(success)

    def test_mcp_failure_triggers_fallback(self):
        """MCP échoue → fallback séquentiel automatique."""
        mock_caller = MagicMock()
        mock_caller.call.side_effect = ConnectionError("down")

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.mod, '_load_agent_caller', return_value=mock_mod):
            executor = self.mod.AutoExecutor(self.tmp)
            self.assertEqual(executor.active_backend, "mcp")
            success, _result = executor(_sample_task(), {})
            # Should have fallen back to sequential
            self.assertTrue(success)
            self.assertEqual(executor._fallback_count, 1)
            self.assertEqual(executor._mcp_failures, 1)

    def test_stats(self):
        executor = self.mod.AutoExecutor(self.tmp)
        stats = executor.stats
        self.assertIn("active_backend", stats)
        self.assertIn("mcp_available", stats)
        self.assertIn("fallback_count", stats)
        self.assertEqual(stats["fallback_count"], 0)

    def test_with_llm_callback(self):
        def fake_llm(prompt, agent):
            return {"answer": "done"}

        with patch.object(self.mod, '_load_agent_caller', return_value=None):
            executor = self.mod.AutoExecutor(self.tmp, llm_callback=fake_llm)
            success, result = executor(_sample_task(), {})
            self.assertTrue(success)
            self.assertEqual(result["answer"], "done")


# ══════════════════════════════════════════════════════════════════════════════
# Factory Functions
# ══════════════════════════════════════════════════════════════════════════════


class TestFactoryFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_executor_factory(self):
        ex = self.mod.auto_executor(self.tmp)
        self.assertIsInstance(ex, self.mod.AutoExecutor)

    def test_auto_executor_with_force(self):
        ex = self.mod.auto_executor(self.tmp, force_backend="dry-run")
        self.assertEqual(ex.active_backend, "dry-run")

    def test_dry_executor_factory(self):
        ex = self.mod.dry_executor(self.tmp)
        self.assertIsInstance(ex, self.mod.DryRunExecutor)

    def test_dry_executor_no_root(self):
        ex = self.mod.dry_executor(None)
        self.assertIsInstance(ex, self.mod.DryRunExecutor)

    def test_sequential_executor_factory(self):
        ex = self.mod.sequential_executor(self.tmp)
        self.assertIsInstance(ex, self.mod.SequentialExecutor)

    def test_mcp_executor_factory(self):
        ex = self.mod.mcp_executor(self.tmp)
        self.assertIsInstance(ex, self.mod.MCPExecutor)

    def test_string_path_accepted(self):
        ex = self.mod.auto_executor(str(self.tmp))
        self.assertIsInstance(ex, self.mod.AutoExecutor)


# ══════════════════════════════════════════════════════════════════════════════
# MCP Interface
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPDetectBackends(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_backends(self):
        result = self.mod.mcp_hpe_detect_backends(str(self.tmp))
        self.assertIn("backends", result)
        self.assertIn("recommended", result)
        self.assertIsInstance(result["backends"], list)
        self.assertGreater(len(result["backends"]), 0)

    def test_recommended_is_valid(self):
        result = self.mod.mcp_hpe_detect_backends(str(self.tmp))
        self.assertIn(result["recommended"], ("mcp", "message-bus", "sequential", "dry-run"))


class TestMCPExecuteTask(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_backend(self):
        task = _sample_task()
        result = self.mod.mcp_hpe_execute_task(
            json.dumps(task), "{}", backend="dry-run", project_root=str(self.tmp),
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["dry_run"])

    def test_sequential_backend(self):
        task = _sample_task()
        result = self.mod.mcp_hpe_execute_task(
            json.dumps(task), "{}", backend="sequential", project_root=str(self.tmp),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["mode"], "sequential-prompt")

    def test_auto_backend(self):
        task = _sample_task()
        result = self.mod.mcp_hpe_execute_task(
            json.dumps(task), "{}", backend="auto", project_root=str(self.tmp),
        )
        self.assertTrue(result["success"])

    def test_with_outputs(self):
        task = _sample_task(depends_on=["prev"])
        outputs = {"prev": {"data": "ok"}}
        result = self.mod.mcp_hpe_execute_task(
            json.dumps(task), json.dumps(outputs),
            backend="sequential", project_root=str(self.tmp),
        )
        self.assertTrue(result["success"])
        self.assertIn("prev", result["result"]["prompt"])


# ══════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ══════════════════════════════════════════════════════════════════════════════


class TestCLIDetect(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detect_text(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "detect"])
        self.assertEqual(rc, 0)

    def test_detect_json(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "--json", "detect"])
        self.assertEqual(rc, 0)


class TestCLITest(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run(self):
        rc = self.mod.main([
            "--project-root", str(self.tmp),
            "test", "--backend", "dry-run", "--agent", "dev", "--task", "ping",
        ])
        self.assertEqual(rc, 0)

    def test_sequential(self):
        rc = self.mod.main([
            "--project-root", str(self.tmp),
            "test", "--backend", "sequential", "--agent", "qa",
        ])
        self.assertEqual(rc, 0)

    def test_json_output(self):
        rc = self.mod.main([
            "--project-root", str(self.tmp), "--json",
            "test", "--backend", "dry-run",
        ])
        self.assertEqual(rc, 0)


class TestMainNoCommand(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_command(self):
        rc = self.mod.main([])
        self.assertEqual(rc, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Integration: HPE Runner + Executors
# ══════════════════════════════════════════════════════════════════════════════


class TestIntegrationWithHPERunner(unittest.TestCase):
    """Tests d'intégration entre hpe-runner.py et hpe-executors.py."""

    def setUp(self):
        self.exec_mod = _import_mod()

        # Import hpe-runner
        runner_name = "hpe_runner"
        if runner_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                runner_name, KIT_DIR / "framework" / "tools" / "hpe-runner.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[runner_name] = mod
            spec.loader.exec_module(mod)
        self.runner_mod = sys.modules[runner_name]

        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_executor_with_runner(self):
        """DryRunExecutor fonctionne comme executor du HPE Runner."""
        defn = {
            "description": "Integration test",
            "dag": {
                "tasks": [
                    {"id": "a", "agent": "dev", "task": "Impl", "output_key": "impl"},
                    {"id": "b", "agent": "qa", "task": "Test", "depends_on": ["a"], "output_key": "test"},
                ],
                "config": {"max_parallel": 5},
            },
        }
        plan = self.runner_mod.build_plan_from_definition(defn)
        executor = self.exec_mod.dry_executor(self.tmp)

        plan, results = self.runner_mod.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 2)
        self.assertIn("impl", plan.outputs)
        self.assertIn("test", plan.outputs)

    def test_sequential_executor_with_runner(self):
        """SequentialExecutor fonctionne comme executor du HPE Runner."""
        defn = {
            "description": "Sequential integration",
            "dag": {
                "tasks": [
                    {"id": "t0", "agent": "analyst", "task": "Analyze requirements", "output_key": "analysis"},
                    {"id": "t1", "agent": "dev", "task": "Implement", "depends_on": ["t0"], "output_key": "code"},
                ],
                "config": {"max_parallel": 3},
            },
        }
        plan = self.runner_mod.build_plan_from_definition(defn)
        executor = self.exec_mod.sequential_executor(self.tmp)

        plan, _results = self.runner_mod.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "completed")
        # Sequential prompt mode → outputs contain prompts
        self.assertIn("analysis", plan.outputs)
        self.assertIn("code", plan.outputs)

    def test_auto_executor_with_runner(self):
        """AutoExecutor (fallback séquentiel) fonctionne avec HPE Runner."""
        defn = {
            "description": "Auto integration",
            "dag": {
                "tasks": [
                    {"id": "x", "agent": "dev", "task": "Do X", "output_key": "x"},
                    {"id": "y", "agent": "dev", "task": "Do Y", "output_key": "y"},
                    {"id": "z", "agent": "qa", "task": "Validate", "depends_on": ["x", "y"], "output_key": "z"},
                ],
                "config": {"max_parallel": 5},
            },
        }
        plan = self.runner_mod.build_plan_from_definition(defn)
        executor = self.exec_mod.auto_executor(self.tmp)

        plan, results = self.runner_mod.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 2)  # wave1: x,y — wave2: z

    def test_llm_callback_integration(self):
        """LLM callback reçoit les outputs propagés des tâches précédentes."""
        received_prompts = []

        def capture_llm(prompt, agent):
            received_prompts.append((prompt, agent))
            return {"agent": agent, "done": True}

        defn = {
            "description": "LLM callback test",
            "dag": {
                "tasks": [
                    {"id": "a", "agent": "analyst", "task": "Analyze", "output_key": "analysis"},
                    {"id": "b", "agent": "dev", "task": "Implement based on analysis",
                     "depends_on": ["a"], "output_key": "impl"},
                ],
            },
        }
        plan = self.runner_mod.build_plan_from_definition(defn)
        executor = self.exec_mod.sequential_executor(self.tmp, llm_callback=capture_llm)

        plan, _results = self.runner_mod.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(received_prompts), 2)
        # Second prompt should reference the analysis output
        self.assertEqual(received_prompts[0][1], "analyst")
        self.assertEqual(received_prompts[1][1], "dev")

    def test_failure_and_fallback(self):
        """Échec MCP → fallback séquentiel, plan complète quand même."""
        mock_caller = MagicMock()
        mock_caller.call.side_effect = ConnectionError("MCP server down")

        mock_mod = MagicMock()
        mock_mod.AgentCaller.return_value = mock_caller
        mock_mod.AgentCallRequest = lambda **kw: MagicMock(**kw)

        with patch.object(self.exec_mod, '_load_agent_caller', return_value=mock_mod):
            defn = {
                "description": "Fallback test",
                "dag": {
                    "tasks": [
                        {"id": "t0", "agent": "dev", "task": "Work", "output_key": "out"},
                    ],
                },
            }
            plan = self.runner_mod.build_plan_from_definition(defn)
            executor = self.exec_mod.auto_executor(self.tmp)

            plan, _results = self.runner_mod.run_plan(plan, self.tmp, executor=executor)
            self.assertEqual(plan.state, "completed")
            self.assertEqual(executor._fallback_count, 1)


# ══════════════════════════════════════════════════════════════════════════════
# CLI subprocess smoke test
# ══════════════════════════════════════════════════════════════════════════════


class TestCLISmoke(unittest.TestCase):
    def test_version(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("hpe-executors", r.stdout)

    def test_detect_subprocess(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _make_project(tmp)
            r = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmp), "detect"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("sequential", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
