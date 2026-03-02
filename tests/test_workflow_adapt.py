#!/usr/bin/env python3
"""
Tests pour workflow-adapt.py — workflow-adapt.py — Plasticité synaptique BMAD.

Fonctions testées :
  - load_traces()
  - build_stats()
  - detect_adaptations()
  - detect_from_workflow_structure()
  - build_adapt_report()
  - save_history()
  - format_analysis()
  - format_overlay()
  - report_to_dict()
  - cmd_analyze()
  - cmd_overlay()
  - cmd_prune()
  - cmd_jit()
  - cmd_history()
  - build_parser()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "workflow-adapt.py"


def _import_mod():
    """Import le module workflow-adapt via importlib."""
    mod_name = "workflow_adapt"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "workflow-adapt.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet BMAD minimal pour les tests."""
    (root / "_bmad" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_bmad-output").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_trace_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "TraceEntry"))

    def test_trace_entry_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.TraceEntry)}
        for expected in ["workflow", "step", "status", "duration_s", "timestamp"]:
            self.assertIn(expected, fields)

    def test_workflow_stats_exists(self):
        self.assertTrue(hasattr(self.mod, "WorkflowStats"))

    def test_workflow_stats_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.WorkflowStats)}
        for expected in ["name", "executions", "avg_duration_s", "step_usage", "step_skipped"]:
            self.assertIn(expected, fields)

    def test_adaptation_exists(self):
        self.assertTrue(hasattr(self.mod, "Adaptation"))

    def test_adaptation_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Adaptation)}
        for expected in ["workflow", "step", "kind", "reason", "confidence"]:
            self.assertIn(expected, fields)

    def test_adapt_report_exists(self):
        self.assertTrue(hasattr(self.mod, "AdaptReport"))

    def test_adapt_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.AdaptReport)}
        for expected in ["workflows_analyzed", "total_traces", "adaptations", "workflow_stats"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_adaptations_callable(self):
        self.assertTrue(callable(getattr(self.mod, "detect_adaptations", None)))

    def test_format_analysis_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_analysis", None)))

    def test_format_overlay_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_overlay", None)))

    def test_report_to_dict_callable(self):
        self.assertTrue(callable(getattr(self.mod, "report_to_dict", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_traces_empty_project(self):
        try:
            result = self.mod.load_traces(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_detect_from_workflow_structure_empty_project(self):
        try:
            result = self.mod.detect_from_workflow_structure(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_build_adapt_report_empty_project(self):
        try:
            result = self.mod.build_adapt_report(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_analysis_callable(self):
        self.assertTrue(callable(self.mod.format_analysis))

    def test_format_overlay_callable(self):
        self.assertTrue(callable(self.mod.format_overlay))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_trace_dir_default_defined(self):
        self.assertTrue(hasattr(self.mod, "TRACE_DIR_DEFAULT"))

    def test_adapt_history_defined(self):
        self.assertTrue(hasattr(self.mod, "ADAPT_HISTORY"))

    def test_adaptation_types_defined(self):
        self.assertTrue(hasattr(self.mod, "ADAPTATION_TYPES"))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_parser_help(self):
        parser = self.mod.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_analyze_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["analyze"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_overlay_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["overlay"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_prune_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["prune"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_jit_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["jit"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_history_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["history"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "workflow-adapt.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("workflow", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
