#!/usr/bin/env python3
"""
Tests pour agent-task-system.py — Agent Task System (ATS).

Fonctions testées :
  - TaskAtom / TaskGraph / ScheduleBatch  (dataclasses)
  - load_graph() / save_graph()
  - append_history()
  - find_task() / add_task() / update_task_status() / add_result()
  - compute_stats()
  - get_dependency_map() / detect_cycles() / topological_sort() / critical_path()
  - get_ready_tasks() / schedule_next_batch()
  - validate_delivery()
  - mcp_ats_create_task() / mcp_ats_schedule() / mcp_ats_status() / mcp_ats_update_status() / mcp_ats_graph()
  - cmd_create() / cmd_graph() / cmd_schedule() / cmd_status() / cmd_inspect() / cmd_reset()
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
from dataclasses import asdict
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "agent-task-system.py"


def _import_mod():
    """Import le module agent-task-system via importlib."""
    mod_name = "agent_task_system"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "agent-task-system.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_project(root: Path) -> Path:
    """Créer un projet minimal avec un répertoire ATS."""
    ats_dir = root / "_grimoire-output" / ".ats"
    ats_dir.mkdir(parents=True, exist_ok=True)
    return root


def _make_task(mod, task_id="ta-test-001", title="Test task", task_type="analytical",
               priority="normal", depends_on=None, status="pending"):
    """Crée un TaskAtom avec des valeurs prédéfinies."""
    t = mod.TaskAtom(
        task_id=task_id,
        title=title,
        task_type=task_type,
        priority=priority,
        depends_on=depends_on or [],
        status=status,
    )
    return t


def _make_graph_with_tasks(mod, tasks_data):
    """Crée un TaskGraph avec une liste de dicts de tâches."""
    graph = mod.TaskGraph(graph_id="test-graph")
    for td in tasks_data:
        task = _make_task(mod, **td)
        graph.tasks.append(asdict(task))
    return graph


# ══════════════════════════════════════════════════════════════════════════════
# Dataclass tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskAtom(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_generates_task_id(self):
        t = self.mod.TaskAtom(title="foo")
        self.assertTrue(t.task_id.startswith("ta-"))
        self.assertGreater(len(t.task_id), 10)

    def test_explicit_task_id(self):
        t = self.mod.TaskAtom(task_id="ta-custom-001", title="foo")
        self.assertEqual(t.task_id, "ta-custom-001")

    def test_default_values(self):
        t = self.mod.TaskAtom()
        self.assertEqual(t.task_type, "analytical")
        self.assertEqual(t.priority, "normal")
        self.assertEqual(t.status, "pending")
        self.assertEqual(t.attempts, 0)
        self.assertEqual(t.depends_on, [])
        self.assertEqual(t.results, [])

    def test_created_at_populated(self):
        t = self.mod.TaskAtom()
        self.assertTrue(t.created_at)
        self.assertEqual(t.created_at, t.updated_at)

    def test_budget_default(self):
        t = self.mod.TaskAtom()
        self.assertIn("max_tokens", t.budget)
        self.assertEqual(t.budget["max_tokens"], 50_000)


class TestTaskGraph(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_generates_graph_id(self):
        g = self.mod.TaskGraph()
        self.assertTrue(g.graph_id.startswith("ag-"))

    def test_empty_by_default(self):
        g = self.mod.TaskGraph()
        self.assertEqual(g.tasks, [])
        self.assertEqual(g.goal, "")


class TestScheduleBatch(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        b = self.mod.ScheduleBatch()
        self.assertEqual(b.batch_id, "")
        self.assertEqual(b.task_ids, [])
        self.assertEqual(b.reason, "")


# ══════════════════════════════════════════════════════════════════════════════
# Graph I/O
# ══════════════════════════════════════════════════════════════════════════════


class TestGraphIO(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_empty(self):
        g = self.mod.load_graph(self.tmp)
        self.assertIsInstance(g, self.mod.TaskGraph)
        self.assertEqual(g.tasks, [])

    def test_save_and_load_roundtrip(self):
        g = self.mod.TaskGraph(graph_id="test-rt", goal="roundtrip test")
        task = _make_task(self.mod, task_id="ta-rt-001", title="RT task")
        g.tasks.append(asdict(task))
        self.mod.save_graph(self.tmp, g)
        g2 = self.mod.load_graph(self.tmp)
        self.assertEqual(g2.graph_id, "test-rt")
        self.assertEqual(g2.goal, "roundtrip test")
        self.assertEqual(len(g2.tasks), 1)
        self.assertEqual(g2.tasks[0]["task_id"], "ta-rt-001")

    def test_load_corrupted_json(self):
        gp = self.mod._graph_path(self.tmp)
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_text("{invalid json!!!", encoding="utf-8")
        g = self.mod.load_graph(self.tmp)
        self.assertEqual(g.tasks, [])

    def test_save_creates_directory(self):
        fresh = self.tmp / "subdir"
        g = self.mod.TaskGraph()
        self.mod.save_graph(fresh, g)
        self.assertTrue(self.mod._graph_path(fresh).exists())

    def test_append_history(self):
        self.mod.append_history(self.tmp, {"event": "test_event", "detail": "x"})
        hp = self.mod._history_path(self.tmp)
        self.assertTrue(hp.exists())
        lines = hp.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["event"], "test_event")
        self.assertIn("timestamp", data)

    def test_append_history_multiple(self):
        self.mod.append_history(self.tmp, {"event": "e1"})
        self.mod.append_history(self.tmp, {"event": "e2"})
        hp = self.mod._history_path(self.tmp)
        lines = hp.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Graph Operations
# ══════════════════════════════════════════════════════════════════════════════


class TestGraphOperations(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_find_task_exists(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-find-001", title="Find me")
        g.tasks.append(asdict(task))
        found = self.mod.find_task(g, "ta-find-001")
        self.assertIsNotNone(found)
        self.assertEqual(found["title"], "Find me")

    def test_find_task_not_found(self):
        g = self.mod.TaskGraph()
        self.assertIsNone(self.mod.find_task(g, "ta-nope"))

    def test_add_task(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-add-001")
        g = self.mod.add_task(g, task)
        self.assertEqual(len(g.tasks), 1)

    def test_add_task_duplicate_raises(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-dup-001")
        g = self.mod.add_task(g, task)
        task2 = _make_task(self.mod, task_id="ta-dup-001")
        with self.assertRaises(ValueError):
            self.mod.add_task(g, task2)

    def test_update_task_status(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-upd-001")
        g.tasks.append(asdict(task))
        g = self.mod.update_task_status(g, "ta-upd-001", "running")
        self.assertEqual(g.tasks[0]["status"], "running")

    def test_update_task_invalid_status(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-upd-002")
        g.tasks.append(asdict(task))
        with self.assertRaises(ValueError, msg="Invalid status"):
            self.mod.update_task_status(g, "ta-upd-002", "bogus")

    def test_update_task_not_found(self):
        g = self.mod.TaskGraph()
        with self.assertRaises(ValueError, msg="not found"):
            self.mod.update_task_status(g, "ta-nope", "done")

    def test_add_result(self):
        g = self.mod.TaskGraph()
        task = _make_task(self.mod, task_id="ta-res-001")
        g.tasks.append(asdict(task))
        result = self.mod.TaskResult(attempt=1, status="success", quality_score=0.95)
        g = self.mod.add_result(g, "ta-res-001", result)
        self.assertEqual(len(g.tasks[0]["results"]), 1)
        self.assertEqual(g.tasks[0]["attempts"], 1)

    def test_add_result_not_found(self):
        g = self.mod.TaskGraph()
        result = self.mod.TaskResult(attempt=1, status="success")
        with self.assertRaises(ValueError):
            self.mod.add_result(g, "ta-nope", result)


# ══════════════════════════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════════════════════════


class TestComputeStats(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_empty_graph(self):
        g = self.mod.TaskGraph()
        stats = self.mod.compute_stats(g)
        self.assertEqual(stats["total"], 0)

    def test_stats_counts(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "done", "task_type": "creative", "priority": "high"},
            {"task_id": "t2", "status": "pending", "task_type": "analytical", "priority": "normal"},
            {"task_id": "t3", "status": "done", "task_type": "creative", "priority": "high"},
            {"task_id": "t4", "status": "failed", "task_type": "evaluative", "priority": "low"},
        ])
        stats = self.mod.compute_stats(g)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["by_status"]["done"], 2)
        self.assertEqual(stats["by_status"]["pending"], 1)
        self.assertEqual(stats["by_status"]["failed"], 1)
        self.assertEqual(stats["progress_pct"], 50.0)
        self.assertEqual(stats["failure_rate"], 25.0)

    def test_all_done(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "done"},
            {"task_id": "t2", "status": "done"},
        ])
        stats = self.mod.compute_stats(g)
        self.assertEqual(stats["progress_pct"], 100.0)


# ══════════════════════════════════════════════════════════════════════════════
# DAG Topological Analysis
# ══════════════════════════════════════════════════════════════════════════════


class TestDependencyMap(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_deps(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ])
        dm = self.mod.get_dependency_map(g)
        self.assertEqual(dm["t1"], set())
        self.assertEqual(dm["t2"], set())

    def test_with_deps(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2", "depends_on": ["t1"]},
        ])
        dm = self.mod.get_dependency_map(g)
        self.assertEqual(dm["t2"], {"t1"})


class TestDetectCycles(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_cycles(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2", "depends_on": ["t1"]},
            {"task_id": "t3", "depends_on": ["t2"]},
        ])
        cycles = self.mod.detect_cycles(g)
        self.assertEqual(cycles, [])

    def test_simple_cycle(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "depends_on": ["t2"]},
            {"task_id": "t2", "depends_on": ["t1"]},
        ])
        cycles = self.mod.detect_cycles(g)
        self.assertGreater(len(cycles), 0)

    def test_self_cycle(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "depends_on": ["t1"]},
        ])
        cycles = self.mod.detect_cycles(g)
        self.assertGreater(len(cycles), 0)


class TestTopologicalSort(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_linear_chain(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2", "depends_on": ["t1"]},
            {"task_id": "t3", "depends_on": ["t2"]},
        ])
        order = self.mod.topological_sort(g)
        self.assertEqual(order, ["t1", "t2", "t3"])

    def test_diamond(self):
        """  t1 → t2, t3 → t4  (t4 depends on t2 and t3)."""
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2", "depends_on": ["t1"]},
            {"task_id": "t3", "depends_on": ["t1"]},
            {"task_id": "t4", "depends_on": ["t2", "t3"]},
        ])
        order = self.mod.topological_sort(g)
        self.assertEqual(order[0], "t1")
        self.assertEqual(order[-1], "t4")
        self.assertIn("t2", order[1:3])
        self.assertIn("t3", order[1:3])

    def test_independent_tasks(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2"},
            {"task_id": "t3"},
        ])
        order = self.mod.topological_sort(g)
        self.assertEqual(len(order), 3)
        # All independent — sorted deterministically
        self.assertEqual(order, sorted(order))


class TestCriticalPath(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_single_task(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "task_type": "analytical"},
        ])
        cp = self.mod.critical_path(g)
        self.assertEqual(cp, ["t1"])

    def test_chain_is_critical(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "task_type": "creative"},       # 300s
            {"task_id": "t2", "depends_on": ["t1"], "task_type": "creative"},  # 300s
        ])
        cp = self.mod.critical_path(g)
        self.assertEqual(cp, ["t1", "t2"])

    def test_longest_path_chosen(self):
        # t1 (creative 300s) → t3 (creative 300s) = 600s
        # t2 (meta 30s) → t3 = 330s
        # critical path = t1 → t3
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "task_type": "creative"},
            {"task_id": "t2", "task_type": "meta"},
            {"task_id": "t3", "depends_on": ["t1", "t2"], "task_type": "creative"},
        ])
        cp = self.mod.critical_path(g)
        self.assertIn("t1", cp)
        self.assertIn("t3", cp)
        self.assertEqual(cp[-1], "t3")

    def test_empty_graph(self):
        g = self.mod.TaskGraph()
        cp = self.mod.critical_path(g)
        self.assertEqual(cp, [])


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler
# ══════════════════════════════════════════════════════════════════════════════


class TestGetReadyTasks(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_all_ready(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 2)

    def test_blocked_by_dependency(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "pending"},
            {"task_id": "t2", "status": "pending", "depends_on": ["t1"]},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["task_id"], "t1")

    def test_unblocked_after_done(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "done"},
            {"task_id": "t2", "status": "pending", "depends_on": ["t1"]},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["task_id"], "t2")

    def test_unblocked_after_cancelled(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "cancelled"},
            {"task_id": "t2", "status": "pending", "depends_on": ["t1"]},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["task_id"], "t2")

    def test_running_not_ready(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "running"},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 0)

    def test_done_not_ready(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "done"},
        ])
        ready = self.mod.get_ready_tasks(g)
        self.assertEqual(len(ready), 0)


class TestScheduleNextBatch(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_ready_tasks(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1", "status": "done"},
        ])
        batch = self.mod.schedule_next_batch(g)
        self.assertEqual(batch.task_ids, [])
        self.assertIn("No tasks ready", batch.reason)

    def test_respects_max_parallel(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": f"t{i}"} for i in range(10)
        ])
        batch = self.mod.schedule_next_batch(g, max_parallel=3)
        self.assertEqual(len(batch.task_ids), 3)

    def test_default_max_parallel_is_4(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": f"t{i}"} for i in range(10)
        ])
        batch = self.mod.schedule_next_batch(g)
        self.assertEqual(len(batch.task_ids), 4)

    def test_batch_has_id(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
        ])
        batch = self.mod.schedule_next_batch(g)
        self.assertTrue(batch.batch_id.startswith("batch-"))

    def test_priority_ordering(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t-low", "priority": "low"},
            {"task_id": "t-crit", "priority": "critical"},
            {"task_id": "t-high", "priority": "high"},
        ])
        batch = self.mod.schedule_next_batch(g, max_parallel=1)
        self.assertEqual(batch.task_ids[0], "t-crit")

    def test_budget_constraint(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ])
        # Default budget per task is 1.0 USD
        batch = self.mod.schedule_next_batch(g, budget_remaining=0.5)
        self.assertEqual(batch.task_ids, [])
        self.assertIn("Budget insufficient", batch.reason)

    def test_partial_budget(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2"},
            {"task_id": "t3"},
        ])
        batch = self.mod.schedule_next_batch(g, max_parallel=10, budget_remaining=2.0)
        self.assertEqual(len(batch.task_ids), 2)

    def test_deps_chain_single_batch(self):
        g = _make_graph_with_tasks(self.mod, [
            {"task_id": "t1"},
            {"task_id": "t2", "depends_on": ["t1"]},
            {"task_id": "t3", "depends_on": ["t2"]},
        ])
        batch = self.mod.schedule_next_batch(g)
        # Only t1 is ready
        self.assertEqual(batch.task_ids, ["t1"])


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateDelivery(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_exists_pass(self):
        (self.tmp / "output.md").write_text("# Result", encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "output.md", "validations": ["file_exists"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertTrue(result["all_passed"])

    def test_file_exists_fail(self):
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "missing.md", "validations": ["file_exists"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertFalse(result["all_passed"])

    def test_svg_valid(self):
        (self.tmp / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "icon.svg", "validations": ["svg_valid"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertTrue(result["all_passed"])

    def test_svg_invalid(self):
        (self.tmp / "bad.svg").write_text("not an svg", encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "bad.svg", "validations": ["svg_valid"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertFalse(result["all_passed"])

    def test_viewbox_check(self):
        (self.tmp / "v.svg").write_text('<svg viewBox="0 0 24 24"></svg>', encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "v.svg", "validations": ["viewbox_24x24"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertTrue(result["all_passed"])

    def test_size_check_pass(self):
        small = self.tmp / "small.svg"
        small.write_text("<svg></svg>", encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "small.svg", "validations": ["optimized_size_lt_10kb"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertTrue(result["all_passed"])

    def test_size_check_fail(self):
        big = self.tmp / "big.svg"
        big.write_text("x" * 20_000, encoding="utf-8")
        task = {
            "task_id": "t1",
            "delivery_contract": {
                "outputs": [{"path": "big.svg", "validations": ["optimized_size_lt_10kb"]}],
            },
        }
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertFalse(result["all_passed"])

    def test_no_contract(self):
        task = {"task_id": "t1", "delivery_contract": {}}
        result = self.mod.validate_delivery(task, self.tmp)
        self.assertTrue(result["all_passed"])


# ══════════════════════════════════════════════════════════════════════════════
# MCP Interface
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPCreateTask(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_returns_dict(self):
        result = self.mod.mcp_ats_create_task(
            title="MCP test task",
            project_root=str(self.tmp),
        )
        self.assertIn("task_id", result)
        self.assertEqual(result["title"], "MCP test task")

    def test_create_persists(self):
        self.mod.mcp_ats_create_task(title="persisted", project_root=str(self.tmp))
        g = self.mod.load_graph(self.tmp)
        self.assertEqual(len(g.tasks), 1)

    def test_create_with_deps(self):
        result = self.mod.mcp_ats_create_task(
            title="with deps",
            depends_on="t1,t2",
            project_root=str(self.tmp),
        )
        self.assertEqual(result["depends_on"], ["t1", "t2"])

    def test_create_invalid_type_fallback(self):
        result = self.mod.mcp_ats_create_task(
            title="bad type",
            task_type="NOPE",
            project_root=str(self.tmp),
        )
        self.assertEqual(result["task_type"], "analytical")

    def test_create_invalid_priority_fallback(self):
        result = self.mod.mcp_ats_create_task(
            title="bad prio",
            priority="EXTREME",
            project_root=str(self.tmp),
        )
        self.assertEqual(result["priority"], "normal")


class TestMCPSchedule(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_schedule(self):
        result = self.mod.mcp_ats_schedule(project_root=str(self.tmp))
        self.assertEqual(result["task_ids"], [])

    def test_schedule_with_tasks(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        self.mod.mcp_ats_create_task(title="t2", project_root=str(self.tmp))
        result = self.mod.mcp_ats_schedule(project_root=str(self.tmp))
        self.assertEqual(len(result["task_ids"]), 2)


class TestMCPStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_status(self):
        result = self.mod.mcp_ats_status(project_root=str(self.tmp))
        self.assertIn("stats", result)
        self.assertEqual(result["stats"]["total"], 0)

    def test_status_with_tasks(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        result = self.mod.mcp_ats_status(project_root=str(self.tmp))
        self.assertEqual(result["stats"]["total"], 1)
        self.assertIn("ready_tasks", result)
        self.assertEqual(len(result["ready_tasks"]), 1)


class TestMCPUpdateStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_update_status(self):
        created = self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        tid = created["task_id"]
        result = self.mod.mcp_ats_update_status(tid, "running", project_root=str(self.tmp))
        self.assertEqual(result["status"], "running")

    def test_update_status_not_found_raises(self):
        with self.assertRaises(ValueError):
            self.mod.mcp_ats_update_status("ta-nope", "done", project_root=str(self.tmp))


class TestMCPGraph(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_graph(self):
        result = self.mod.mcp_ats_graph(project_root=str(self.tmp))
        self.assertIn("tasks", result)
        self.assertEqual(result["tasks"], [])

    def test_graph_with_tasks(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        result = self.mod.mcp_ats_graph(project_root=str(self.tmp))
        self.assertEqual(len(result["tasks"]), 1)


# ══════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ══════════════════════════════════════════════════════════════════════════════


class TestCLICreate(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_create(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "create", "--title", "CLI test"])
        self.assertEqual(rc, 0)
        g = self.mod.load_graph(self.tmp)
        self.assertEqual(len(g.tasks), 1)

    def test_cli_create_json(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "--json", "create", "--title", "JSON test"])
        self.assertEqual(rc, 0)


class TestCLIGraph(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_graph_empty(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "graph"])
        self.assertEqual(rc, 0)

    def test_cli_graph_with_tasks(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        rc = self.mod.main(["--project-root", str(self.tmp), "graph"])
        self.assertEqual(rc, 0)

    def test_cli_graph_json(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        rc = self.mod.main(["--project-root", str(self.tmp), "--json", "graph"])
        self.assertEqual(rc, 0)


class TestCLISchedule(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_schedule_empty(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "schedule"])
        self.assertEqual(rc, 0)

    def test_cli_schedule_with_tasks(self):
        self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        rc = self.mod.main(["--project-root", str(self.tmp), "schedule"])
        self.assertEqual(rc, 0)


class TestCLIStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_status(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "status"])
        self.assertEqual(rc, 0)


class TestCLIInspect(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inspect_existing(self):
        created = self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        tid = created["task_id"]
        rc = self.mod.main(["--project-root", str(self.tmp), "inspect", "--id", tid])
        self.assertEqual(rc, 0)

    def test_inspect_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "inspect", "--id", "ta-nope"])
        self.assertEqual(rc, 1)


class TestCLIReset(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reset_existing(self):
        created = self.mod.mcp_ats_create_task(title="t1", project_root=str(self.tmp))
        tid = created["task_id"]
        self.mod.mcp_ats_update_status(tid, "running", project_root=str(self.tmp))
        rc = self.mod.main(["--project-root", str(self.tmp), "reset", "--id", tid])
        self.assertEqual(rc, 0)
        g = self.mod.load_graph(self.tmp)
        t = self.mod.find_task(g, tid)
        self.assertEqual(t["status"], "pending")
        self.assertEqual(t["attempts"], 0)

    def test_reset_not_found(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "reset", "--id", "ta-nope"])
        self.assertEqual(rc, 1)


class TestMainNoCommand(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_command_shows_help(self):
        rc = self.mod.main([])
        self.assertEqual(rc, 0)


# ══════════════════════════════════════════════════════════════════════════════
# CLI subprocess smoke test
# ══════════════════════════════════════════════════════════════════════════════


class TestCLISmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_version(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
            env={**__import__("os").environ, "COLUMNS": "200"},
        )
        self.assertEqual(r.returncode, 0)
        # Normalise les retours à la ligne provoqués par le wrapping argparse
        normalized = " ".join(r.stdout.split())
        self.assertIn("agent-task-system", normalized)

    def test_status_subprocess(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--project-root", str(self.tmp), "status"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)

    def test_create_subprocess(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--project-root", str(self.tmp),
             "create", "--title", "subprocess test"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("TaskAtom créé", r.stdout)


if __name__ == "__main__":
    unittest.main()
