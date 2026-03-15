#!/usr/bin/env python3
"""
Tests pour hpe-monitor.py — HPE Execution Monitor & Dashboard.

Couvre :
  - MonitorData (dataclass)
  - Loaders (plans, checkpoints, traces, history)
  - Plan analysis (plan_summary, build_dag_edges, topological_layers)
  - Text formatting (format_status_text)
  - JSON export (data_to_json)
  - HTML generation (generate_html)
  - CLI commands (generate, status, export)
  - Integration E2E (runner + executors + monitor)
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
TOOL = KIT_DIR / "framework" / "tools" / "hpe-monitor.py"


def _import_mod():
    mod_name = "hpe_monitor"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "hpe-monitor.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_runner():
    mod_name = "hpe_runner"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "hpe-runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_executors():
    mod_name = "hpe_executors"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "hpe-executors.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_project(root: Path) -> Path:
    """Crée un projet minimal avec répertoire HPE."""
    hpe_dir = root / "_grimoire-output" / ".hpe"
    (hpe_dir / "plans").mkdir(parents=True, exist_ok=True)
    (hpe_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
    (hpe_dir / "traces").mkdir(parents=True, exist_ok=True)
    return root


def _write_plan(root: Path, plan_id: str, **kw) -> dict:
    plan = {
        "plan_id": plan_id,
        "description": kw.get("description", "Test plan"),
        "state": kw.get("state", "completed"),
        "tasks": kw.get("tasks", [
            {"id": "a", "agent": "dev", "task": "Do A", "depends_on": [],
             "output_key": "out_a", "status": "done"},
            {"id": "b", "agent": "qa", "task": "Test A", "depends_on": ["a"],
             "output_key": "out_b", "status": "done"},
        ]),
        "config": kw.get("config", {"max_parallel": 5}),
        "outputs": kw.get("outputs", {}),
        "waves_completed": kw.get("waves_completed", 2),
        "created_at": "2026-03-10T10:00:00",
        "updated_at": "2026-03-10T10:01:00",
    }
    path = root / "_grimoire-output" / ".hpe" / "plans" / f"{plan_id}.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def _write_trace(root: Path, trace_id: str, **kw) -> dict:
    trace = {
        "trace_id": trace_id,
        "task_id": kw.get("task_id", "t1"),
        "agent": kw.get("agent", "dev"),
        "backend": kw.get("backend", "dry-run"),
        "status": kw.get("status", "success"),
        "duration_ms": kw.get("duration_ms", 42),
        "tokens_used": kw.get("tokens_used", 0),
        "timestamp": "2026-03-10T10:00:00Z",
    }
    path = root / "_grimoire-output" / ".hpe" / "traces" / f"{trace_id}.json"
    path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    return trace


def _write_checkpoint(root: Path, cp_id: str, plan_id: str = "hpe-test") -> dict:
    cp = {
        "checkpoint_id": cp_id,
        "plan_id": plan_id,
        "trigger_task": "t1",
        "timestamp": "2026-03-10T10:00:30",
    }
    path = root / "_grimoire-output" / ".hpe" / "checkpoints" / f"{cp_id}.json"
    path.write_text(json.dumps(cp, indent=2), encoding="utf-8")
    return cp


def _write_history(root: Path, events: list[dict]) -> None:
    path = root / "_grimoire-output" / ".hpe" / "hpe-history.jsonl"
    lines = [json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# Data Loaders
# ═════════════════════════════════════════════════════════════════════════════


class TestLoadPlans(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        plans = self.mod.load_plans(self.tmp)
        self.assertEqual(plans, [])

    def test_one_plan(self):
        _write_plan(self.tmp, "hpe-test1")
        plans = self.mod.load_plans(self.tmp)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["plan_id"], "hpe-test1")

    def test_multiple_plans(self):
        _write_plan(self.tmp, "hpe-a")
        _write_plan(self.tmp, "hpe-b", state="running")
        plans = self.mod.load_plans(self.tmp)
        self.assertEqual(len(plans), 2)

    def test_invalid_json_skipped(self):
        _write_plan(self.tmp, "hpe-ok")
        bad = self.tmp / "_grimoire-output" / ".hpe" / "plans" / "hpe-bad.json"
        bad.write_text("{invalid", encoding="utf-8")
        plans = self.mod.load_plans(self.tmp)
        self.assertEqual(len(plans), 1)


class TestLoadTraces(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        traces = self.mod.load_traces(self.tmp)
        self.assertEqual(traces, [])

    def test_load_traces(self):
        _write_trace(self.tmp, "tr-001", backend="mcp")
        _write_trace(self.tmp, "tr-002", backend="sequential")
        traces = self.mod.load_traces(self.tmp)
        self.assertEqual(len(traces), 2)


class TestLoadCheckpoints(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        self.assertEqual(self.mod.load_checkpoints(self.tmp), [])

    def test_load(self):
        _write_checkpoint(self.tmp, "cp-001")
        cps = self.mod.load_checkpoints(self.tmp)
        self.assertEqual(len(cps), 1)
        self.assertEqual(cps[0]["checkpoint_id"], "cp-001")


class TestLoadHistory(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        self.assertEqual(self.mod.load_history(self.tmp), [])

    def test_load(self):
        _write_history(self.tmp, [
            {"event": "plan_start", "plan_id": "hpe-1"},
            {"event": "plan_end", "plan_id": "hpe-1", "state": "completed"},
        ])
        events = self.mod.load_history(self.tmp)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "plan_start")


class TestComputeBackendStats(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_empty(self):
        self.assertEqual(self.mod.compute_backend_stats([]), {})

    def test_counts(self):
        traces = [
            {"backend": "mcp"}, {"backend": "mcp"},
            {"backend": "sequential"}, {"backend": "dry-run"},
        ]
        stats = self.mod.compute_backend_stats(traces)
        self.assertEqual(stats["mcp"], 2)
        self.assertEqual(stats["sequential"], 1)
        self.assertEqual(stats["dry-run"], 1)


class TestLoadAll(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_project(self):
        data = self.mod.load_all(self.tmp)
        self.assertEqual(data.plans, [])
        self.assertEqual(data.traces, [])
        self.assertTrue(data.generated_at)

    def test_populated(self):
        _write_plan(self.tmp, "hpe-x")
        _write_trace(self.tmp, "tr-1", backend="dry-run")
        _write_checkpoint(self.tmp, "cp-1")
        _write_history(self.tmp, [{"event": "test"}])
        data = self.mod.load_all(self.tmp)
        self.assertEqual(len(data.plans), 1)
        self.assertEqual(len(data.traces), 1)
        self.assertEqual(len(data.checkpoints), 1)
        self.assertEqual(len(data.history), 1)
        self.assertEqual(data.backends, {"dry-run": 1})


# ═════════════════════════════════════════════════════════════════════════════
# Plan Analysis
# ═════════════════════════════════════════════════════════════════════════════


class TestPlanSummary(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_basic(self):
        plan = {
            "plan_id": "hpe-1",
            "description": "Test",
            "state": "completed",
            "tasks": [
                {"id": "a", "status": "done"},
                {"id": "b", "status": "done"},
                {"id": "c", "status": "failed"},
            ],
            "waves_completed": 3,
        }
        s = self.mod.plan_summary(plan)
        self.assertEqual(s["plan_id"], "hpe-1")
        self.assertEqual(s["total_tasks"], 3)
        self.assertEqual(s["by_status"]["done"], 2)
        self.assertEqual(s["by_status"]["failed"], 1)
        self.assertAlmostEqual(s["progress_pct"], 66.7, places=1)

    def test_empty_plan(self):
        s = self.mod.plan_summary({"tasks": []})
        self.assertEqual(s["total_tasks"], 0)
        self.assertEqual(s["progress_pct"], 0)


class TestBuildDagEdges(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_edges(self):
        plan = {
            "tasks": [
                {"id": "a", "depends_on": []},
                {"id": "b", "depends_on": ["a"]},
                {"id": "c", "depends_on": ["a", "b"]},
            ],
        }
        edges = self.mod.build_dag_edges(plan)
        self.assertEqual(len(edges), 3)
        self.assertIn({"from": "a", "to": "b"}, edges)

    def test_no_deps(self):
        plan = {"tasks": [{"id": "a"}, {"id": "b"}]}
        self.assertEqual(self.mod.build_dag_edges(plan), [])


class TestTopologicalLayers(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_simple_chain(self):
        plan = {
            "tasks": [
                {"id": "a", "depends_on": []},
                {"id": "b", "depends_on": ["a"]},
                {"id": "c", "depends_on": ["b"]},
            ],
        }
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 3)
        self.assertEqual(layers[0], ["a"])
        self.assertEqual(layers[1], ["b"])
        self.assertEqual(layers[2], ["c"])

    def test_parallel(self):
        plan = {
            "tasks": [
                {"id": "a"}, {"id": "b"}, {"id": "c"},
            ],
        }
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 1)
        self.assertEqual(sorted(layers[0]), ["a", "b", "c"])

    def test_diamond(self):
        plan = {
            "tasks": [
                {"id": "a"},
                {"id": "b", "depends_on": ["a"]},
                {"id": "c", "depends_on": ["a"]},
                {"id": "d", "depends_on": ["b", "c"]},
            ],
        }
        layers = self.mod.topological_layers(plan)
        self.assertEqual(len(layers), 3)
        self.assertEqual(layers[0], ["a"])
        self.assertEqual(sorted(layers[1]), ["b", "c"])
        self.assertEqual(layers[2], ["d"])

    def test_empty(self):
        self.assertEqual(self.mod.topological_layers({"tasks": []}), [])


# ═════════════════════════════════════════════════════════════════════════════
# Text Formatting
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatStatusText(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_plans(self):
        data = self.mod.MonitorData(generated_at="2026-01-01T00:00:00Z")
        text = self.mod.format_status_text(data)
        self.assertIn("Aucun plan HPE", text)

    def test_with_plans(self):
        data = self.mod.MonitorData(
            plans=[{
                "plan_id": "hpe-demo",
                "description": "Test Feature",
                "state": "completed",
                "tasks": [
                    {"id": "a", "agent": "dev", "task": "Do", "status": "done", "depends_on": []},
                    {"id": "b", "agent": "qa", "task": "Test", "status": "done", "depends_on": ["a"]},
                ],
                "waves_completed": 2,
            }],
            traces=[{"backend": "dry-run"}],
            backends={"dry-run": 1},
            generated_at="2026-01-01T00:00:00Z",
        )
        text = self.mod.format_status_text(data)
        self.assertIn("hpe-demo", text)
        self.assertIn("100.0%", text)
        self.assertIn("Wave 1", text)
        self.assertIn("Wave 2", text)

    def test_with_checkpoints(self):
        data = self.mod.MonitorData(
            plans=[{
                "plan_id": "hpe-cp",
                "state": "completed",
                "tasks": [{"id": "t1", "status": "done", "agent": "dev", "task": "X"}],
                "waves_completed": 1,
            }],
            checkpoints=[{"checkpoint_id": "cp-1", "plan_id": "hpe-1", "trigger_task": "t1"}],
            generated_at="2026-01-01T00:00:00Z",
        )
        text = self.mod.format_status_text(data)
        self.assertIn("Checkpoints", text)
        self.assertIn("cp-1", text)


# ═════════════════════════════════════════════════════════════════════════════
# JSON Export
# ═════════════════════════════════════════════════════════════════════════════


class TestDataToJson(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_json(self):
        _write_plan(self.tmp, "hpe-j")
        data = self.mod.load_all(self.tmp)
        result = json.loads(self.mod.data_to_json(data))
        self.assertIn("plans", result)
        self.assertIn("plan_summaries", result)
        self.assertIn("dag_layers", result)
        self.assertIn("dag_edges", result)

    def test_dag_layers_present(self):
        _write_plan(self.tmp, "hpe-dag", tasks=[
            {"id": "x", "agent": "dev", "task": "X", "depends_on": [], "status": "done"},
            {"id": "y", "agent": "qa", "task": "Y", "depends_on": ["x"], "status": "done"},
        ])
        data = self.mod.load_all(self.tmp)
        result = json.loads(self.mod.data_to_json(data))
        self.assertIn("hpe-dag", result["dag_layers"])
        layers = result["dag_layers"]["hpe-dag"]
        self.assertEqual(len(layers), 2)


# ═════════════════════════════════════════════════════════════════════════════
# HTML Generation
# ═════════════════════════════════════════════════════════════════════════════


class TestGenerateHTML(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generates_valid_html(self):
        _write_plan(self.tmp, "hpe-html")
        _write_trace(self.tmp, "tr-h1")
        data = self.mod.load_all(self.tmp)
        html = self.mod.generate_html(data)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("HPE Monitor", html)
        self.assertIn("hpe-html", html)

    def test_empty_data_still_valid(self):
        data = self.mod.MonitorData(generated_at="now")
        html = self.mod.generate_html(data)
        self.assertIn("<!DOCTYPE html>", html)

    def test_contains_tabs(self):
        data = self.mod.MonitorData(generated_at="now")
        html = self.mod.generate_html(data)
        for tab in ("overview", "dag", "timeline", "traces", "backends"):
            self.assertIn(f'data-view="{tab}"', html)


# ═════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ═════════════════════════════════════════════════════════════════════════════


class TestCLIGenerate(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generate(self):
        _write_plan(self.tmp, "hpe-gen")
        rc = self.mod.main(["--project-root", str(self.tmp), "generate"])
        self.assertEqual(rc, 0)
        html_file = self.tmp / "_grimoire-output" / "hpe-dashboard.html"
        self.assertTrue(html_file.exists())
        content = html_file.read_text(encoding="utf-8")
        self.assertIn("hpe-gen", content)

    def test_generate_json(self):
        _write_plan(self.tmp, "hpe-gj")
        rc = self.mod.main(["--project-root", str(self.tmp), "--json", "generate"])
        self.assertEqual(rc, 0)


class TestCLIStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_empty(self):
        rc = self.mod.main(["--project-root", str(self.tmp), "status"])
        self.assertEqual(rc, 0)

    def test_status_with_plans(self):
        _write_plan(self.tmp, "hpe-st")
        rc = self.mod.main(["--project-root", str(self.tmp), "status"])
        self.assertEqual(rc, 0)

    def test_status_json(self):
        _write_plan(self.tmp, "hpe-sj")
        rc = self.mod.main(["--project-root", str(self.tmp), "--json", "status"])
        self.assertEqual(rc, 0)


class TestCLIExport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export(self):
        _write_plan(self.tmp, "hpe-exp")
        rc = self.mod.main(["--project-root", str(self.tmp), "export"])
        self.assertEqual(rc, 0)


class TestCLINoCommand(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_no_command_shows_help(self):
        rc = self.mod.main([])
        self.assertEqual(rc, 0)


# ═════════════════════════════════════════════════════════════════════════════
# Integration E2E: Runner + Executors + Monitor
# ═════════════════════════════════════════════════════════════════════════════


class TestE2EIntegration(unittest.TestCase):
    """Test bout en bout : créer un plan, l'exécuter, générer le dashboard."""

    def setUp(self):
        self.monitor = _import_mod()
        self.runner = _import_runner()
        self.executors = _import_executors()
        self.tmp = Path(tempfile.mkdtemp())
        _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_cycle_dry_run(self):
        """Plan → exécution dry-run → dashboard → vérification."""
        defn = {
            "description": "E2E test plan",
            "dag": {
                "tasks": [
                    {"id": "a", "agent": "analyst", "task": "Analyze",
                     "output_key": "analysis"},
                    {"id": "b", "agent": "dev", "task": "Implement",
                     "depends_on": ["a"], "output_key": "code"},
                    {"id": "c", "agent": "qa", "task": "Test",
                     "depends_on": ["b"], "output_key": "tests"},
                ],
            },
        }
        # Execute
        plan = self.runner.build_plan_from_definition(defn)
        executor = self.executors.dry_executor(self.tmp)
        plan, results = self.runner.run_plan(plan, self.tmp, executor=executor)
        self.assertEqual(plan.state, "completed")
        self.assertEqual(len(results), 3)  # 3 waves (chain)

        # Monitor
        data = self.monitor.load_all(self.tmp)
        self.assertEqual(len(data.plans), 1)
        self.assertGreater(len(data.traces), 0)
        self.assertGreater(len(data.history), 0)

        # Dashboard HTML
        html = self.monitor.generate_html(data)
        self.assertIn(plan.plan_id, html)
        self.assertIn("completed", html)

        # Text status
        text = self.monitor.format_status_text(data)
        self.assertIn("100.0%", text)
        self.assertIn("Wave 1", text)

        # JSON export
        exported = json.loads(self.monitor.data_to_json(data))
        self.assertIn(plan.plan_id, exported["dag_layers"])
        layers = exported["dag_layers"][plan.plan_id]
        self.assertEqual(len(layers), 3)

    def test_parallel_tasks_shown_in_same_wave(self):
        """Tâches parallèles groupées dans la même wave."""
        defn = {
            "description": "Parallel test",
            "dag": {
                "tasks": [
                    {"id": "x", "agent": "dev", "task": "X", "output_key": "ox"},
                    {"id": "y", "agent": "dev", "task": "Y", "output_key": "oy"},
                    {"id": "z", "agent": "qa", "task": "Z",
                     "depends_on": ["x", "y"], "output_key": "oz"},
                ],
            },
        }
        plan = self.runner.build_plan_from_definition(defn)
        executor = self.executors.dry_executor(self.tmp)
        plan, _results = self.runner.run_plan(plan, self.tmp, executor=executor)

        data = self.monitor.load_all(self.tmp)
        exported = json.loads(self.monitor.data_to_json(data))
        layers = exported["dag_layers"][plan.plan_id]
        # Wave 1: x, y (parallel) — Wave 2: z
        self.assertEqual(len(layers), 2)
        self.assertEqual(sorted(layers[0]), ["x", "y"])
        self.assertEqual(layers[1], ["z"])

    def test_backend_stats_tracked(self):
        """Les stats backend sont correctement comptées."""
        defn = {
            "description": "Stats test",
            "dag": {
                "tasks": [
                    {"id": "t1", "agent": "dev", "task": "T1", "output_key": "o1"},
                    {"id": "t2", "agent": "dev", "task": "T2", "output_key": "o2"},
                ],
            },
        }
        plan = self.runner.build_plan_from_definition(defn)
        executor = self.executors.dry_executor(self.tmp)
        self.runner.run_plan(plan, self.tmp, executor=executor)

        data = self.monitor.load_all(self.tmp)
        self.assertEqual(data.backends.get("dry-run", 0), 2)

    def test_sequential_executor_traces(self):
        """Les traces séquentielles apparaissent dans le monitor."""
        defn = {
            "description": "Sequential traces test",
            "dag": {
                "tasks": [
                    {"id": "s1", "agent": "dev", "task": "S1", "output_key": "o1"},
                ],
            },
        }
        plan = self.runner.build_plan_from_definition(defn)
        executor = self.executors.sequential_executor(self.tmp)
        self.runner.run_plan(plan, self.tmp, executor=executor)

        data = self.monitor.load_all(self.tmp)
        backends = [t["backend"] for t in data.traces]
        self.assertIn("sequential-prompt", backends)


# ═════════════════════════════════════════════════════════════════════════════
# CLI Subprocess Smoke Test
# ═════════════════════════════════════════════════════════════════════════════


class TestCLISmoke(unittest.TestCase):
    def test_version(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("hpe-monitor", r.stdout)

    def test_status_subprocess(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _make_project(tmp)
            r = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmp), "status"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("HPE Monitor", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generate_subprocess(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _make_project(tmp)
            _write_plan(tmp, "hpe-smoke")
            r = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmp), "generate"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0)
            self.assertTrue((tmp / "_grimoire-output" / "hpe-dashboard.html").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
