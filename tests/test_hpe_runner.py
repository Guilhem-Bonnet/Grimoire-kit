#!/usr/bin/env python3
"""
Tests pour hpe-runner.py — Hybrid Parallelism Engine Runner (BM-58).

Fonctions testées :
  - HPETask / HPEPlan / WaveResult / Checkpoint  (dataclasses)
  - save_plan() / load_plan()
  - save_checkpoint() / load_checkpoint()
  - append_history()
  - build_plan_from_definition() / validate_plan()
  - get_dependency_map() / detect_cycles() / topological_layers() / critical_path()
  - get_ready_tasks() / get_opportunistic_tasks() / schedule_wave()
  - mark_task_running() / mark_task_done() / mark_task_failed()
  - apply_failure_strategy() / retry_task() / skip_task()
  - create_checkpoint() / restore_from_checkpoint()
  - execute_wave() / run_plan()
  - get_plan_status()
  - mcp_hpe_plan() / mcp_hpe_run() / mcp_hpe_status()
  - cmd_plan() / cmd_run() / cmd_status() / cmd_critical() / cmd_resume()
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

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "hpe-runner.py"


def _import_mod():
    """Import le module hpe-runner via importlib."""
    mod_name = "hpe_runner"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "hpe-runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_project(root: Path) -> Path:
    """Créer un projet minimal avec répertoire HPE."""
    hpe_dir = root / "_grimoire-output" / ".hpe"
    hpe_dir.mkdir(parents=True, exist_ok=True)
    return root


def _simple_definition(task_count=3, with_deps=True):
    """Crée une définition YAML simple pour tests."""
    tasks = []
    for i in range(task_count):
        t = {
            "id": f"t{i}",
            "agent": "dev" if i % 2 == 0 else "qa",
            "task": f"Task {i} description",
            "depends_on": [f"t{i-1}"] if (with_deps and i > 0) else [],
            "output_key": f"out_{i}",
        }
        tasks.append(t)
    return {
        "description": "Test plan",
        "dag": {
            "tasks": tasks,
            "config": {"max_parallel": 5, "on_failure": "pause-and-escalate"},
        },
    }


def _parallel_definition():
    """Crée une définition avec tâches parallèles indépendantes."""
    return {
        "description": "Parallel plan",
        "dag": {
            "tasks": [
                {"id": "a", "agent": "dev", "task": "Task A", "depends_on": [], "output_key": "out_a"},
                {"id": "b", "agent": "qa", "task": "Task B", "depends_on": [], "output_key": "out_b"},
                {"id": "c", "agent": "architect", "task": "Task C", "depends_on": [], "output_key": "out_c"},
                {"id": "d", "agent": "sm", "task": "Task D", "depends_on": ["a", "b", "c"], "output_key": "out_d"},
            ],
            "config": {"max_parallel": 5},
        },
    }


def _diamond_definition():
    """Crée un DAG en diamant : t0 → (t1, t2) → t3."""
    return {
        "description": "Diamond plan",
        "dag": {
            "tasks": [
                {"id": "t0", "agent": "analyst", "task": "Analyze", "depends_on": [], "output_key": "analysis"},
                {"id": "t1", "agent": "dev", "task": "Implement A", "depends_on": ["t0"], "output_key": "impl_a"},
                {"id": "t2", "agent": "dev", "task": "Implement B", "depends_on": ["t0"], "output_key": "impl_b"},
                {"id": "t3", "agent": "qa", "task": "Validate", "depends_on": ["t1", "t2"], "output_key": "validation"},
            ],
            "config": {"max_parallel": 3},
        },
    }


def _make_plan(mod, definition=None):
    """Construit un HPEPlan depuis une définition."""
    defn = definition or _simple_definition()
    return mod.build_plan_from_definition(defn)


# ══════════════════════════════════════════════════════════════════════════════
# Dataclass tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHPETask(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        t = self.mod.HPETask()
        self.assertEqual(t.status, "pending")
        self.assertEqual(t.mode, "parallel")
        self.assertEqual(t.max_retries, 2)
        self.assertEqual(t.attempt, 0)


class TestHPEPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_id(self):
        p = self.mod.HPEPlan()
        self.assertTrue(p.plan_id.startswith("hpe-"))

    def test_defaults(self):
        p = self.mod.HPEPlan()
        self.assertEqual(p.state, "pending")
        self.assertEqual(p.tasks, [])
        self.assertEqual(p.waves_completed, 0)

    def test_timestamps(self):
        p = self.mod.HPEPlan()
        self.assertTrue(p.created_at)
        self.assertEqual(p.created_at, p.updated_at)


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_id(self):
        cp = self.mod.Checkpoint()
        self.assertTrue(cp.checkpoint_id.startswith("cp-"))


# ══════════════════════════════════════════════════════════════════════════════
# File I/O
# ══════════════════════════════════════════════════════════════════════════════


class TestPlanIO(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load(self):
        plan = _make_plan(self.mod)
        self.mod.save_plan(self.tmp, plan)
        loaded = self.mod.load_plan(self.tmp, plan.plan_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.plan_id, plan.plan_id)
        self.assertEqual(len(loaded.tasks), len(plan.tasks))

    def test_load_nonexistent(self):
        result = self.mod.load_plan(self.tmp, "hpe-nope")
        self.assertIsNone(result)

    def test_load_corrupted(self):
        pp = self.mod._plan_path(self.tmp, "hpe-bad")
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text("{bad json!!!", encoding="utf-8")
        result = self.mod.load_plan(self.tmp, "hpe-bad")
        self.assertIsNone(result)


class TestCheckpointIO(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load(self):
        cp = self.mod.Checkpoint(plan_id="hpe-test", state={"plan_state": "running"})
        self.mod.save_checkpoint(self.tmp, cp)
        loaded = self.mod.load_checkpoint(self.tmp, cp.checkpoint_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.plan_id, "hpe-test")

    def test_load_nonexistent(self):
        result = self.mod.load_checkpoint(self.tmp, "cp-nope")
        self.assertIsNone(result)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_history(self):
        self.mod.append_history(self.tmp, {"event": "test"})
        hp = self.mod._history_path(self.tmp)
        self.assertTrue(hp.exists())
        data = json.loads(hp.read_text(encoding="utf-8").strip())
        self.assertEqual(data["event"], "test")
        self.assertIn("timestamp", data)


# ══════════════════════════════════════════════════════════════════════════════
# DAG Builder
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_basic_build(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        self.assertEqual(len(plan.tasks), 3)
        self.assertEqual(plan.description, "Test plan")

    def test_task_ids_assigned(self):
        plan = _make_plan(self.mod, _simple_definition(2))
        ids = {t["id"] for t in plan.tasks}
        self.assertEqual(ids, {"t0", "t1"})

    def test_config_extracted(self):
        plan = _make_plan(self.mod, _simple_definition())
        self.assertEqual(plan.config["max_parallel"], 5)
        self.assertEqual(plan.config["on_failure"], "pause-and-escalate")

    def test_invalid_mode_fallback(self):
        defn = {
            "dag": {
                "tasks": [{"id": "t0", "agent": "dev", "task": "x", "mode": "BOGUS"}],
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        self.assertEqual(plan.tasks[0]["mode"], "parallel")

    def test_empty_definition(self):
        plan = self.mod.build_plan_from_definition({})
        self.assertEqual(len(plan.tasks), 0)


class TestValidatePlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_valid_plan(self):
        plan = _make_plan(self.mod, _simple_definition())
        errors = self.mod.validate_plan(plan)
        self.assertEqual(errors, [])

    def test_empty_plan(self):
        plan = self.mod.HPEPlan()
        errors = self.mod.validate_plan(plan)
        self.assertIn("Plan has no tasks", errors)

    def test_unknown_dependency(self):
        defn = {
            "dag": {
                "tasks": [{"id": "t0", "agent": "dev", "task": "x", "depends_on": ["ghost"]}],
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        errors = self.mod.validate_plan(plan)
        self.assertTrue(any("unknown task 'ghost'" in e for e in errors))

    def test_cycle_detected(self):
        defn = {
            "dag": {
                "tasks": [
                    {"id": "a", "agent": "dev", "task": "x", "depends_on": ["b"]},
                    {"id": "b", "agent": "dev", "task": "y", "depends_on": ["a"]},
                ],
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        errors = self.mod.validate_plan(plan)
        self.assertTrue(any("Cycles" in e for e in errors))

    def test_bad_checkpoint_after(self):
        defn = {
            "dag": {
                "tasks": [{"id": "t0", "agent": "dev", "task": "x"}],
                "config": {"checkpoint_after": ["ghost"]},
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        errors = self.mod.validate_plan(plan)
        self.assertTrue(any("ghost" in e for e in errors))


# ══════════════════════════════════════════════════════════════════════════════
# DAG Analysis
# ══════════════════════════════════════════════════════════════════════════════


class TestTopologicalLayers(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_linear_chain(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 3)
        self.assertEqual(layers[0], ["t0"])
        self.assertEqual(layers[1], ["t1"])
        self.assertEqual(layers[2], ["t2"])

    def test_parallel_independent(self):
        plan = _make_plan(self.mod, _parallel_definition())
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 2)
        self.assertEqual(sorted(layers[0]), ["a", "b", "c"])
        self.assertEqual(layers[1], ["d"])

    def test_diamond(self):
        plan = _make_plan(self.mod, _diamond_definition())
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 3)
        self.assertEqual(layers[0], ["t0"])
        self.assertEqual(sorted(layers[1]), ["t1", "t2"])
        self.assertEqual(layers[2], ["t3"])


class TestCriticalPath(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_single_task(self):
        defn = {"dag": {"tasks": [{"id": "t0", "agent": "dev", "task": "x"}]}}
        plan = self.mod.build_plan_from_definition(defn)
        cp = self.mod.critical_path(plan)
        self.assertEqual(cp, ["t0"])

    def test_linear(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        cp = self.mod.critical_path(plan)
        self.assertEqual(cp, ["t0", "t1", "t2"])

    def test_empty(self):
        plan = self.mod.HPEPlan()
        cp = self.mod.critical_path(plan)
        self.assertEqual(cp, [])

    def test_diamond_path(self):
        plan = _make_plan(self.mod, _diamond_definition())
        cp = self.mod.critical_path(plan)
        self.assertIn("t0", cp)
        self.assertIn("t3", cp)
        self.assertEqual(cp[-1], "t3")


class TestDetectCycles(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_cycles(self):
        plan = _make_plan(self.mod, _simple_definition())
        cycles = self.mod.detect_cycles(plan)
        self.assertEqual(cycles, [])

    def test_cycle(self):
        defn = {
            "dag": {
                "tasks": [
                    {"id": "a", "agent": "dev", "task": "x", "depends_on": ["b"]},
                    {"id": "b", "agent": "dev", "task": "y", "depends_on": ["a"]},
                ],
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        cycles = self.mod.detect_cycles(plan)
        self.assertGreater(len(cycles), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler
# ══════════════════════════════════════════════════════════════════════════════


class TestGetReadyTasks(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_all_independent_ready(self):
        plan = _make_plan(self.mod, _parallel_definition())
        ready = self.mod.get_ready_tasks(plan)
        self.assertEqual(len(ready), 3)  # a, b, c — d depends on them

    def test_chain_first_only(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        ready = self.mod.get_ready_tasks(plan)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "t0")

    def test_after_done(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        plan.tasks[0]["status"] = "done"
        ready = self.mod.get_ready_tasks(plan)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "t1")


class TestGetOpportunistic(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_opportunistic_by_default(self):
        plan = _make_plan(self.mod, _diamond_definition())
        opp = self.mod.get_opportunistic_tasks(plan)
        self.assertEqual(len(opp), 0)

    def test_opportunistic_partial_deps(self):
        plan = _make_plan(self.mod, _diamond_definition())
        # Make t3 opportunistic
        plan.tasks[3]["mode"] = "opportunistic"
        # Complete only t1 (t2 still pending)
        plan.tasks[0]["status"] = "done"  # t0
        plan.tasks[1]["status"] = "done"  # t1
        # t2 still pending, t3 depends on t1+t2
        opp = self.mod.get_opportunistic_tasks(plan)
        self.assertEqual(len(opp), 1)
        self.assertEqual(opp[0]["id"], "t3")


class TestScheduleWave(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_parallel_wave(self):
        plan = _make_plan(self.mod, _parallel_definition())
        wave = self.mod.schedule_wave(plan)
        self.assertEqual(sorted(wave), ["a", "b", "c"])

    def test_respects_max_parallel(self):
        defn = _parallel_definition()
        defn["dag"]["config"]["max_parallel"] = 2
        plan = self.mod.build_plan_from_definition(defn)
        wave = self.mod.schedule_wave(plan)
        self.assertEqual(len(wave), 2)

    def test_sequential_wave(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        wave = self.mod.schedule_wave(plan)
        self.assertEqual(wave, ["t0"])

    def test_priority_ordering(self):
        defn = {
            "dag": {
                "tasks": [
                    {"id": "lo", "agent": "dev", "task": "x", "priority": "low"},
                    {"id": "hi", "agent": "dev", "task": "y", "priority": "critical"},
                ],
                "config": {"max_parallel": 1},
            },
        }
        plan = self.mod.build_plan_from_definition(defn)
        wave = self.mod.schedule_wave(plan)
        self.assertEqual(wave, ["hi"])


# ══════════════════════════════════════════════════════════════════════════════
# Task state transitions
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskTransitions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_mark_running(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan = self.mod.mark_task_running(plan, "t0")
        self.assertEqual(plan.tasks[0]["status"], "running")
        self.assertTrue(plan.tasks[0]["started_at"])
        self.assertEqual(plan.tasks[0]["attempt"], 1)

    def test_mark_done(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan = self.mod.mark_task_done(plan, "t0", {"value": 42})
        self.assertEqual(plan.tasks[0]["status"], "done")
        self.assertEqual(plan.tasks[0]["result"]["value"], 42)
        self.assertTrue(plan.tasks[0]["completed_at"])

    def test_mark_done_stores_output(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan = self.mod.mark_task_done(plan, "t0", {"val": "ok"})
        self.assertEqual(plan.outputs["out_0"], {"val": "ok"})

    def test_mark_failed(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan = self.mod.mark_task_failed(plan, "t0", "boom")
        self.assertEqual(plan.tasks[0]["status"], "failed")
        self.assertEqual(plan.tasks[0]["error"], "boom")

    def test_mark_nonexistent_noop(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan = self.mod.mark_task_running(plan, "ghost")
        # Should not crash, just noop
        self.assertEqual(plan.tasks[0]["status"], "pending")


# ══════════════════════════════════════════════════════════════════════════════
# Failure strategies
# ══════════════════════════════════════════════════════════════════════════════


class TestFailureStrategies(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_stop_all(self):
        defn = _simple_definition(3, with_deps=True)
        defn["dag"]["config"]["on_failure"] = "stop-all"
        plan = self.mod.build_plan_from_definition(defn)
        plan.tasks[0]["status"] = "failed"
        plan, action = self.mod.apply_failure_strategy(plan, "t0")
        self.assertEqual(plan.state, "failed")
        self.assertIn("stop-all", action)
        # All pending tasks should be cancelled
        for t in plan.tasks:
            if t["id"] != "t0":
                self.assertEqual(t["status"], "cancelled")

    def test_continue_others(self):
        defn = _diamond_definition()
        defn["dag"]["config"]["on_failure"] = "continue-others"
        plan = self.mod.build_plan_from_definition(defn)
        plan.tasks[0]["status"] = "done"  # t0
        plan.tasks[1]["status"] = "failed"  # t1
        plan, action = self.mod.apply_failure_strategy(plan, "t1")
        self.assertIn("continue-others", action)
        # t3 depends on t1 → should be cancelled
        t3 = next(t for t in plan.tasks if t["id"] == "t3")
        self.assertEqual(t3["status"], "cancelled")
        # t2 is independent of t1 → should remain pending
        t2 = next(t for t in plan.tasks if t["id"] == "t2")
        self.assertEqual(t2["status"], "pending")

    def test_pause_and_escalate(self):
        defn = _simple_definition(3, with_deps=True)
        defn["dag"]["config"]["on_failure"] = "pause-and-escalate"
        plan = self.mod.build_plan_from_definition(defn)
        plan.tasks[0]["status"] = "failed"
        plan, action = self.mod.apply_failure_strategy(plan, "t0")
        self.assertEqual(plan.state, "paused")
        self.assertIn("pause-and-escalate", action)


class TestRetryTask(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_retry_success(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan.tasks[0]["status"] = "failed"
        plan.tasks[0]["attempt"] = 1
        plan.state = "paused"
        plan = self.mod.retry_task(plan, "t0")
        self.assertEqual(plan.tasks[0]["status"], "pending")
        self.assertEqual(plan.state, "running")

    def test_retry_max_exceeded(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan.tasks[0]["status"] = "failed"
        plan.tasks[0]["attempt"] = 2
        with self.assertRaises(ValueError, msg="max retries"):
            self.mod.retry_task(plan, "t0")

    def test_retry_not_failed(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        with self.assertRaises(ValueError, msg="not failed"):
            self.mod.retry_task(plan, "t0")

    def test_retry_not_found(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        with self.assertRaises(ValueError, msg="not found"):
            self.mod.retry_task(plan, "ghost")


class TestSkipTask(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_skip(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan.tasks[0]["status"] = "failed"
        plan.state = "paused"
        plan = self.mod.skip_task(plan, "t0")
        self.assertEqual(plan.tasks[0]["status"], "skipped")
        self.assertEqual(plan.state, "running")

    def test_skip_not_found(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        with self.assertRaises(ValueError):
            self.mod.skip_task(plan, "ghost")


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoints
# ══════════════════════════════════════════════════════════════════════════════


class TestCheckpoints(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_checkpoint(self):
        plan = _make_plan(self.mod, _simple_definition(2, with_deps=False))
        plan.tasks[0]["status"] = "done"
        plan.outputs["out_0"] = {"val": "ok"}
        cp = self.mod.create_checkpoint(plan, trigger_task="t0")
        self.assertEqual(cp.plan_id, plan.plan_id)
        self.assertEqual(cp.trigger_task, "t0")
        self.assertEqual(cp.state["task_states"]["t0"]["status"], "done")
        self.assertEqual(cp.outputs["out_0"]["val"], "ok")

    def test_restore_from_checkpoint(self):
        plan = _make_plan(self.mod, _simple_definition(2, with_deps=False))
        plan.tasks[0]["status"] = "done"
        plan.outputs["out_0"] = {"val": "ok"}
        plan.state = "running"
        plan.waves_completed = 1
        cp = self.mod.create_checkpoint(plan)

        # Reset plan
        plan2 = _make_plan(self.mod, _simple_definition(2, with_deps=False))
        plan2.plan_id = plan.plan_id
        plan2 = self.mod.restore_from_checkpoint(plan2, cp)
        self.assertEqual(plan2.tasks[0]["status"], "done")
        self.assertEqual(plan2.outputs["out_0"]["val"], "ok")
        self.assertEqual(plan2.waves_completed, 1)


# ══════════════════════════════════════════════════════════════════════════════
# Execution Engine
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteWave(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan, wr = self.mod.execute_wave(plan, self.tmp)
        self.assertEqual(len(wr.succeeded), 1)
        self.assertEqual(wr.succeeded[0], "t0")
        self.assertEqual(plan.tasks[0]["status"], "done")

    def test_with_executor_success(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))

        def executor(task, outputs):
            return True, {"computed": True}

        plan, wr = self.mod.execute_wave(plan, self.tmp, executor=executor)
        self.assertEqual(len(wr.succeeded), 1)
        self.assertEqual(plan.tasks[0]["result"]["computed"], True)

    def test_with_executor_failure(self):
        defn = _simple_definition(1, with_deps=False)
        defn["dag"]["config"]["on_failure"] = "pause-and-escalate"
        plan = self.mod.build_plan_from_definition(defn)

        def executor(task, outputs):
            return False, {"error": "crash"}

        plan, wr = self.mod.execute_wave(plan, self.tmp, executor=executor)
        self.assertEqual(len(wr.failed), 1)
        self.assertEqual(plan.tasks[0]["status"], "failed")

    def test_executor_exception(self):
        defn = _simple_definition(1, with_deps=False)
        defn["dag"]["config"]["on_failure"] = "continue-others"
        plan = self.mod.build_plan_from_definition(defn)

        def executor(task, outputs):
            raise RuntimeError("kaboom")

        plan, wr = self.mod.execute_wave(plan, self.tmp, executor=executor)
        self.assertEqual(len(wr.failed), 1)
        self.assertIn("kaboom", plan.tasks[0]["error"])

    def test_checkpoint_triggered(self):
        defn = _simple_definition(1, with_deps=False)
        defn["dag"]["config"]["checkpoint_after"] = ["t0"]
        plan = self.mod.build_plan_from_definition(defn)
        plan, _wr = self.mod.execute_wave(plan, self.tmp)
        # checkpoint should have been saved
        cp_dir = self.mod._checkpoints_dir(self.tmp)
        self.assertTrue(cp_dir.exists())
        cp_files = list(cp_dir.glob("*.json"))
        self.assertGreater(len(cp_files), 0)

    def test_no_ready_wave(self):
        plan = _make_plan(self.mod, _simple_definition(1, with_deps=False))
        plan.tasks[0]["status"] = "done"
        plan, wr = self.mod.execute_wave(plan, self.tmp)
        self.assertEqual(wr.task_ids, [])
        self.assertEqual(wr.succeeded, [])


class TestRunPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_dry_run_sequential(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        plan, results = self.mod.run_plan(plan, self.tmp)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 3)
        for t in plan.tasks:
            self.assertEqual(t["status"], "done")

    def test_full_dry_run_parallel(self):
        plan = _make_plan(self.mod, _parallel_definition())
        plan, results = self.mod.run_plan(plan, self.tmp)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 2)  # wave 1: a,b,c — wave 2: d

    def test_full_dry_run_diamond(self):
        plan = _make_plan(self.mod, _diamond_definition())
        plan, results = self.mod.run_plan(plan, self.tmp)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 3)  # 3 waves

    def test_invalid_plan_raises(self):
        plan = self.mod.HPEPlan()
        with self.assertRaises(ValueError):
            self.mod.run_plan(plan, self.tmp)

    def test_run_with_failures_pauses(self):
        defn = _simple_definition(3, with_deps=True)
        defn["dag"]["config"]["on_failure"] = "pause-and-escalate"
        plan = self.mod.build_plan_from_definition(defn)

        call_count = 0
        def executor(task, outputs):
            nonlocal call_count
            call_count += 1
            if task["id"] == "t0":
                return False, {"error": "oops"}
            return True, {}

        plan, _results = self.mod.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "paused")

    def test_outputs_propagated(self):
        plan = _make_plan(self.mod, _simple_definition(2, with_deps=True))

        def executor(task, outputs):
            return True, {"from": task["id"]}

        plan, _results = self.mod.run_plan(plan, self.tmp, executor=executor)
        self.assertIn("out_0", plan.outputs)
        self.assertIn("out_1", plan.outputs)
        self.assertEqual(plan.outputs["out_0"]["from"], "t0")

    def test_max_waves_safety(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        plan, results = self.mod.run_plan(plan, self.tmp, max_waves=1)
        # Only 1 wave executed
        self.assertEqual(len(results), 1)


# ══════════════════════════════════════════════════════════════════════════════
# Plan Status
# ══════════════════════════════════════════════════════════════════════════════


class TestGetPlanStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_empty_plan(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        status = self.mod.get_plan_status(plan)
        self.assertEqual(status["total_tasks"], 3)
        self.assertEqual(status["progress_pct"], 0)
        self.assertIn("critical_path", status)

    def test_partial_progress(self):
        plan = _make_plan(self.mod, _simple_definition(2, with_deps=False))
        plan.tasks[0]["status"] = "done"
        status = self.mod.get_plan_status(plan)
        self.assertEqual(status["progress_pct"], 50.0)


# ══════════════════════════════════════════════════════════════════════════════
# MCP Interface
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mcp_plan_valid(self):
        defn = _parallel_definition()
        result = self.mod.mcp_hpe_plan(json.dumps(defn), project_root=str(self.tmp))
        self.assertIn("plan_id", result)
        self.assertEqual(result["validation"], "OK")
        self.assertEqual(result["task_count"], 4)

    def test_mcp_plan_invalid(self):
        defn = {"dag": {"tasks": []}}
        result = self.mod.mcp_hpe_plan(json.dumps(defn), project_root=str(self.tmp))
        self.assertIn("error", result)


class TestMCPRun(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mcp_run(self):
        defn = _parallel_definition()
        created = self.mod.mcp_hpe_plan(json.dumps(defn), project_root=str(self.tmp))
        result = self.mod.mcp_hpe_run(created["plan_id"], project_root=str(self.tmp))
        self.assertEqual(result["final_state"], "completed")

    def test_mcp_run_not_found(self):
        result = self.mod.mcp_hpe_run("hpe-nope", project_root=str(self.tmp))
        self.assertIn("error", result)


class TestMCPStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mcp_status(self):
        defn = _simple_definition(2)
        created = self.mod.mcp_hpe_plan(json.dumps(defn), project_root=str(self.tmp))
        result = self.mod.mcp_hpe_status(created["plan_id"], project_root=str(self.tmp))
        self.assertEqual(result["total_tasks"], 2)

    def test_mcp_status_not_found(self):
        result = self.mod.mcp_hpe_status("hpe-nope", project_root=str(self.tmp))
        self.assertIn("error", result)


# ══════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ══════════════════════════════════════════════════════════════════════════════


class TestCLIPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_plan_json_file(self):
        defn = _parallel_definition()
        defn_file = self.tmp / "plan.json"
        defn_file.write_text(json.dumps(defn), encoding="utf-8")
        rc = self.mod.main(["--project-root", str(self.tmp), "plan", "--file", str(defn_file)])
        self.assertEqual(rc, 0)

    def test_cli_plan_file_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "plan", "--file", "/nope.json"])
        self.assertEqual(rc, 1)


class TestCLIRun(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_run(self):
        defn = _parallel_definition()
        plan = self.mod.build_plan_from_definition(defn)
        self.mod.save_plan(self.tmp, plan)
        rc = self.mod.main(["--project-root", str(self.tmp), "run", "--plan-id", plan.plan_id])
        self.assertEqual(rc, 0)

    def test_cli_run_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "run", "--plan-id", "hpe-nope"])
        self.assertEqual(rc, 1)


class TestCLIStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_status(self):
        plan = _make_plan(self.mod, _simple_definition())
        self.mod.save_plan(self.tmp, plan)
        rc = self.mod.main(["--project-root", str(self.tmp), "status", "--plan-id", plan.plan_id])
        self.assertEqual(rc, 0)

    def test_cli_status_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "status", "--plan-id", "hpe-nope"])
        self.assertEqual(rc, 1)


class TestCLICritical(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_critical(self):
        plan = _make_plan(self.mod, _diamond_definition())
        self.mod.save_plan(self.tmp, plan)
        rc = self.mod.main(["--project-root", str(self.tmp), "critical", "--plan-id", plan.plan_id])
        self.assertEqual(rc, 0)


class TestCLIResume(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_resume(self):
        plan = _make_plan(self.mod, _simple_definition(3, with_deps=True))
        plan.tasks[0]["status"] = "done"
        plan.waves_completed = 1
        self.mod.save_plan(self.tmp, plan)
        cp = self.mod.create_checkpoint(plan, trigger_task="t0")
        self.mod.save_checkpoint(self.tmp, cp)
        rc = self.mod.main(["--project-root", str(self.tmp), "resume", "--checkpoint", cp.checkpoint_id])
        self.assertEqual(rc, 0)

    def test_cli_resume_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "resume", "--checkpoint", "cp-nope"])
        self.assertEqual(rc, 1)


class TestMainNoCommand(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_command(self):
        rc = self.mod.main([])
        self.assertEqual(rc, 0)


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
        self.assertIn("hpe-runner", r.stdout)


if __name__ == "__main__":
    unittest.main()
