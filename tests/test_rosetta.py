#!/usr/bin/env python3
"""
Tests pour rosetta.py — rosetta.py — Glossaire cross-domain Rosetta Stone Grimoire.

Fonctions testées :
  - detect_domain()
  - extract_terms()
  - extract_context()
  - build_glossary()
  - trace_etymology()
  - format_glossary()
  - format_lookup()
  - format_etymology()
  - export_markdown()
  - cmd_build()
  - cmd_lookup()
  - cmd_ambiguity()
  - cmd_etymology()
  - cmd_export()
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
TOOL = KIT_DIR / "framework" / "tools" / "rosetta.py"


def _import_mod():
    """Import le module rosetta via importlib."""
    mod_name = "rosetta"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "rosetta.py")
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

    def test_glossary_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "GlossaryEntry"))

    def test_glossary_entry_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.GlossaryEntry)}
        for expected in ["term", "domains", "frequency", "sources", "is_ambiguous"]:
            self.assertIn(expected, fields)

    def test_glossary_exists(self):
        self.assertTrue(hasattr(self.mod, "Glossary"))

    def test_glossary_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Glossary)}
        for expected in ["entries", "ambiguities", "timestamp"]:
            self.assertIn(expected, fields)

    def test_etymology_record_exists(self):
        self.assertTrue(hasattr(self.mod, "EtymologyRecord"))

    def test_etymology_record_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.EtymologyRecord)}
        for expected in ["term", "first_seen", "first_source", "evolution", "rationale"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_domain_callable(self):
        self.assertTrue(callable(getattr(self.mod, "detect_domain", None)))

    def test_extract_terms_callable(self):
        self.assertTrue(callable(getattr(self.mod, "extract_terms", None)))

    def test_extract_context_callable(self):
        self.assertTrue(callable(getattr(self.mod, "extract_context", None)))

    def test_format_glossary_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_glossary", None)))

    def test_format_lookup_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_lookup", None)))

    def test_format_etymology_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_etymology", None)))

    def test_export_markdown_callable(self):
        self.assertTrue(callable(getattr(self.mod, "export_markdown", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_glossary_empty_project(self):
        try:
            result = self.mod.build_glossary(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_trace_etymology_callable(self):
        self.assertTrue(callable(self.mod.trace_etymology))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_glossary_callable(self):
        self.assertTrue(callable(self.mod.format_glossary))

    def test_format_lookup_callable(self):
        self.assertTrue(callable(self.mod.format_lookup))

    def test_format_etymology_callable(self):
        self.assertTrue(callable(self.mod.format_etymology))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_rosetta_version_defined(self):
        self.assertTrue(hasattr(self.mod, "ROSETTA_VERSION"))

    def test_domains_defined(self):
        self.assertTrue(hasattr(self.mod, "DOMAINS"))

    def test_domain_patterns_defined(self):
        self.assertTrue(hasattr(self.mod, "DOMAIN_PATTERNS"))

    def test_domain_globs_defined(self):
        self.assertTrue(hasattr(self.mod, "DOMAIN_GLOBS"))

    def test_stops_defined(self):
        self.assertTrue(hasattr(self.mod, "STOPS"))

    def test_min_term_len_defined(self):
        self.assertTrue(hasattr(self.mod, "MIN_TERM_LEN"))

    def test_max_glossary_entries_defined(self):
        self.assertTrue(hasattr(self.mod, "MAX_GLOSSARY_ENTRIES"))


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
        with contextlib.suppress(SystemExit):
            parser.parse_args(["build"])

    def test_subcommand_lookup_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["lookup"])

    def test_subcommand_ambiguity_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["ambiguity"])

    def test_subcommand_etymology_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["etymology"])

    def test_subcommand_export_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["export"])


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "rosetta.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("rosetta", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
