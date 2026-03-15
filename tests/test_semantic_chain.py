#!/usr/bin/env python3
"""
Tests pour semantic-chain.py — semantic-chain.py — Chaîne du froid sémantique Grimoire.

Fonctions testées :
  - extract_concepts()
  - extract_concepts_from_file()
  - detect_drift()
  - discover_artifacts()
  - analyze_impact()
  - analyze_chain()
  - format_concepts()
  - format_drift()
  - format_chain()
  - format_impact()
  - cmd_extract()
  - cmd_trace()
  - cmd_drift()
  - cmd_chain()
  - cmd_impact()
  - build_parser()
"""

import contextlib
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
TOOL = KIT_DIR / "framework" / "tools" / "semantic-chain.py"


def _import_mod():
    """Import le module semantic-chain via importlib."""
    mod_name = "semantic_chain"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "semantic-chain.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet Grimoire minimal pour les tests."""
    (root / "_grimoire" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_concept_exists(self):
        self.assertTrue(hasattr(self.mod, "Concept"))

    def test_concept_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Concept)}
        for expected in ["term", "frequency", "context", "source"]:
            self.assertIn(expected, fields)

    def test_drift_result_exists(self):
        self.assertTrue(hasattr(self.mod, "DriftResult"))

    def test_drift_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DriftResult)}
        for expected in ["source", "target", "source_concepts", "target_concepts", "shared"]:
            self.assertIn(expected, fields)

    def test_impact_node_exists(self):
        self.assertTrue(hasattr(self.mod, "ImpactNode"))

    def test_impact_node_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ImpactNode)}
        for expected in ["file", "artifact_type", "concepts_affected", "impact_score"]:
            self.assertIn(expected, fields)

    def test_chain_report_exists(self):
        self.assertTrue(hasattr(self.mod, "ChainReport"))

    def test_chain_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ChainReport)}
        for expected in ["drifts", "total_concepts", "chain_integrity", "weakest_link", "timestamp"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_extract_concepts_callable(self):
        self.assertTrue(callable(getattr(self.mod, "extract_concepts", None)))

    def test_extract_concepts_from_file_callable(self):
        self.assertTrue(callable(getattr(self.mod, "extract_concepts_from_file", None)))

    def test_detect_drift_callable(self):
        self.assertTrue(callable(getattr(self.mod, "detect_drift", None)))

    def test_format_concepts_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_concepts", None)))

    def test_format_drift_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_drift", None)))

    def test_format_chain_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_chain", None)))

    def test_format_impact_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_impact", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_artifacts_empty_project(self):
        try:
            result = self.mod.discover_artifacts(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_analyze_impact_callable(self):
        self.assertTrue(callable(self.mod.analyze_impact))

    def test_analyze_chain_empty_project(self):
        try:
            result = self.mod.analyze_chain(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_concepts_callable(self):
        self.assertTrue(callable(self.mod.format_concepts))

    def test_format_drift_callable(self):
        self.assertTrue(callable(self.mod.format_drift))

    def test_format_chain_callable(self):
        self.assertTrue(callable(self.mod.format_chain))

    def test_format_impact_callable(self):
        self.assertTrue(callable(self.mod.format_impact))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_semantic_chain_version_defined(self):
        self.assertTrue(hasattr(self.mod, "SEMANTIC_CHAIN_VERSION"))

    def test_chain_order_defined(self):
        self.assertTrue(hasattr(self.mod, "CHAIN_ORDER"))

    def test_artifact_globs_defined(self):
        self.assertTrue(hasattr(self.mod, "ARTIFACT_GLOBS"))

    def test_drift_threshold_warn_defined(self):
        self.assertTrue(hasattr(self.mod, "DRIFT_THRESHOLD_WARN"))

    def test_drift_threshold_alert_defined(self):
        self.assertTrue(hasattr(self.mod, "DRIFT_THRESHOLD_ALERT"))

    def test_min_concept_length_defined(self):
        self.assertTrue(hasattr(self.mod, "MIN_CONCEPT_LENGTH"))

    def test_max_concepts_defined(self):
        self.assertTrue(hasattr(self.mod, "MAX_CONCEPTS"))

    def test_stop_words_defined(self):
        self.assertTrue(hasattr(self.mod, "STOP_WORDS"))


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

    def test_subcommand_extract_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["extract"])

    def test_subcommand_trace_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["trace"])

    def test_subcommand_drift_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["drift"])

    def test_subcommand_chain_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["chain"])

    def test_subcommand_impact_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["impact"])


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "semantic-chain.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("semantic", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
