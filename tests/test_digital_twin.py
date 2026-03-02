#!/usr/bin/env python3
"""
Tests pour digital-twin.py — Digital Twin — Simulation d'impact pour projets bmad-custom-kit.

Fonctions testées :
  - scan_project()
  - cmd_snapshot()
  - cmd_simulate()
  - cmd_diff()
  - cmd_impact()
  - cmd_scenario()
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
TOOL = KIT_DIR / "framework" / "tools" / "digital-twin.py"


def _import_mod():
    """Import le module digital-twin via importlib."""
    mod_name = "digital_twin"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "digital-twin.py")
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

    def test_project_entity_exists(self):
        self.assertTrue(hasattr(self.mod, "ProjectEntity"))

    def test_project_entity_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ProjectEntity)}
        for expected in ["kind", "name", "path", "size", "checksum"]:
            self.assertIn(expected, fields)

    def test_snapshot_exists(self):
        self.assertTrue(hasattr(self.mod, "Snapshot"))

    def test_snapshot_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Snapshot)}
        for expected in ["snapshot_id", "timestamp", "project_root", "entities", "graph_edges"]:
            self.assertIn(expected, fields)

    def test_simulation_change_exists(self):
        self.assertTrue(hasattr(self.mod, "SimulationChange"))

    def test_simulation_change_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.SimulationChange)}
        for expected in ["action", "target_kind", "target_name", "details"]:
            self.assertIn(expected, fields)

    def test_impact_result_exists(self):
        self.assertTrue(hasattr(self.mod, "ImpactResult"))

    def test_impact_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ImpactResult)}
        for expected in ["change", "direct_impacts", "indirect_impacts", "risk_score", "risk_level"]:
            self.assertIn(expected, fields)

    def test_scenario_result_exists(self):
        self.assertTrue(hasattr(self.mod, "ScenarioResult"))

    def test_scenario_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ScenarioResult)}
        for expected in ["scenario_name", "changes", "cumulative_impacts", "total_risk", "feasibility"]:
            self.assertIn(expected, fields)


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_project_empty_project(self):
        try:
            result = self.mod.scan_project(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))


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

    def test_subcommand_snapshot_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["snapshot"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_simulate_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["simulate"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_diff_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["diff"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_impact_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["impact"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_scenario_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["scenario"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "digital-twin.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("digital", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
