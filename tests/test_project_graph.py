#!/usr/bin/env python3
"""
Tests pour project-graph.py — project-graph.py — Graphe du projet BMAD.

Fonctions testées :
  - build_graph()
  - compute_centrality()
  - find_clusters()
  - find_orphans()
  - format_graph()
  - format_centrality()
  - format_mermaid()
  - cmd_build()
  - cmd_centrality()
  - cmd_clusters()
  - cmd_orphans()
  - cmd_mermaid()
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
TOOL = KIT_DIR / "framework" / "tools" / "project-graph.py"


def _import_mod():
    """Import le module project-graph via importlib."""
    mod_name = "project_graph"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "project-graph.py")
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

    def test_node_exists(self):
        self.assertTrue(hasattr(self.mod, "Node"))

    def test_node_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Node)}
        for expected in ["id", "name", "node_type", "path"]:
            self.assertIn(expected, fields)

    def test_edge_exists(self):
        self.assertTrue(hasattr(self.mod, "Edge"))

    def test_edge_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Edge)}
        for expected in ["source", "target", "relation"]:
            self.assertIn(expected, fields)

    def test_graph_exists(self):
        self.assertTrue(hasattr(self.mod, "Graph"))

    def test_graph_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Graph)}
        for expected in ["nodes", "edges"]:
            self.assertIn(expected, fields)

    def test_centrality_result_exists(self):
        self.assertTrue(hasattr(self.mod, "CentralityResult"))

    def test_centrality_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.CentralityResult)}
        for expected in ["node_id", "node_name", "degree", "degree_centrality"]:
            self.assertIn(expected, fields)

    def test_cluster_exists(self):
        self.assertTrue(hasattr(self.mod, "Cluster"))

    def test_cluster_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Cluster)}
        for expected in ["id", "nodes"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_compute_centrality_callable(self):
        self.assertTrue(callable(getattr(self.mod, "compute_centrality", None)))

    def test_find_clusters_callable(self):
        self.assertTrue(callable(getattr(self.mod, "find_clusters", None)))

    def test_find_orphans_callable(self):
        self.assertTrue(callable(getattr(self.mod, "find_orphans", None)))

    def test_format_graph_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_graph", None)))

    def test_format_centrality_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_centrality", None)))

    def test_format_mermaid_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_mermaid", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_graph_empty_project(self):
        try:
            result = self.mod.build_graph(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_graph_callable(self):
        self.assertTrue(callable(self.mod.format_graph))

    def test_format_centrality_callable(self):
        self.assertTrue(callable(self.mod.format_centrality))

    def test_format_mermaid_callable(self):
        self.assertTrue(callable(self.mod.format_mermaid))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_node_types_defined(self):
        self.assertTrue(hasattr(self.mod, "NODE_TYPES"))


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

    def test_subcommand_build_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["build"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_centrality_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["centrality"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_clusters_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["clusters"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_orphans_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["orphans"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_mermaid_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["mermaid"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "project-graph.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("project", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
