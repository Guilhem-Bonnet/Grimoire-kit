#!/usr/bin/env python3
"""
Tests pour failure-museum.py — Catalogue structuré des échecs du projet.

Fonctions testées :
  - Failure (dataclass)
  - load_failures(), save_failure(), next_failure_id()
  - render_markdown(), sync_markdown()
  - cmd_add(), cmd_list(), cmd_search(), cmd_stats()
  - cmd_export(), cmd_lessons(), cmd_check()
  - build_parser(), main()
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
TOOL = KIT_DIR / "framework" / "tools" / "failure-museum.py"


def _import_mod():
    mod_name = "failure_museum"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "failure-museum.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    (root / ".bmad-memory").mkdir(parents=True, exist_ok=True)
    return root


# ── Dataclass tests ──────────────────────────────────────────────────────────


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_failure_exists(self):
        self.assertTrue(hasattr(self.mod, "Failure"))

    def test_failure_defaults(self):
        f = self.mod.Failure()
        self.assertEqual(f.failure_id, "")
        self.assertEqual(f.severity, "medium")
        self.assertEqual(f.status, "resolved")
        self.assertIsInstance(f.agents, list)
        self.assertIsInstance(f.tags, list)

    def test_failure_with_values(self):
        f = self.mod.Failure(
            failure_id="FM-001", title="test", severity="high",
            agents=["dev"], description="broke", root_cause="bug",
            fix="fixed", rule_added="don't", status="resolved"
        )
        self.assertEqual(f.failure_id, "FM-001")
        self.assertEqual(f.severity, "high")


# ── Persistence tests ────────────────────────────────────────────────────────


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty(self):
        entries = self.mod.load_failures(self.tmpdir)
        self.assertEqual(entries, [])

    def test_save_and_load(self):
        f = self.mod.Failure(failure_id="FM-001", sequence=1, title="test",
                             severity="low", description="d", root_cause="r",
                             fix="f")
        self.mod.save_failure(self.tmpdir, f)
        loaded = self.mod.load_failures(self.tmpdir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].failure_id, "FM-001")

    def test_save_multiple(self):
        for i in range(3):
            f = self.mod.Failure(failure_id=f"FM-{i+1:03d}", sequence=i+1,
                                 title=f"test-{i}", severity="low",
                                 description="d", root_cause="r", fix="f")
            self.mod.save_failure(self.tmpdir, f)
        loaded = self.mod.load_failures(self.tmpdir)
        self.assertEqual(len(loaded), 3)

    def test_next_failure_id_empty(self):
        fid, seq = self.mod.next_failure_id([])
        self.assertEqual(fid, "FM-001")
        self.assertEqual(seq, 1)

    def test_next_failure_id_increments(self):
        entries = [self.mod.Failure(sequence=3), self.mod.Failure(sequence=5)]
        fid, seq = self.mod.next_failure_id(entries)
        self.assertEqual(fid, "FM-006")
        self.assertEqual(seq, 6)


# ── Markdown rendering tests ─────────────────────────────────────────────────


class TestMarkdown(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_render_empty(self):
        md = self.mod.render_markdown([])
        self.assertIn("# Failure Museum", md)

    def test_render_with_entry(self):
        f = self.mod.Failure(
            failure_id="FM-001", title="crash", severity="high",
            agents=["dev"], description="boom", root_cause="bug",
            fix="patched", rule_added="no boom", status="resolved"
        )
        md = self.mod.render_markdown([f])
        self.assertIn("FM-001", md)
        self.assertIn("crash", md)
        self.assertIn("high", md)
        self.assertIn("no boom", md)

    def test_sync_markdown(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            _make_project(tmpdir)
            f = self.mod.Failure(failure_id="FM-001", sequence=1, title="test",
                                 severity="low", description="d", root_cause="r",
                                 fix="f", rule_added="rule")
            self.mod.save_failure(tmpdir, f)
            self.mod.sync_markdown(tmpdir)
            md_path = tmpdir / ".bmad-memory" / "failure-museum.md"
            self.assertTrue(md_path.exists())
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("FM-001", content)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Command tests ─────────────────────────────────────────────────────────────


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _add_sample(self, title="test-fail", severity="medium"):
        import argparse
        args = argparse.Namespace(
            title=title, severity=severity, agents="dev,qa",
            description="something broke", root_cause="bug in code",
            fix="patched it", rule="always test", tags="test,ci",
            status="resolved"
        )
        return self.mod.cmd_add(self.tmpdir, args)

    def test_cmd_add(self):
        rc = self._add_sample()
        self.assertEqual(rc, 0)
        entries = self.mod.load_failures(self.tmpdir)
        self.assertEqual(len(entries), 1)

    def test_cmd_list_empty(self):
        import argparse
        args = argparse.Namespace(severity=None, status=None)
        rc = self.mod.cmd_list(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_list_with_entries(self):
        import argparse
        self._add_sample()
        self._add_sample("other-fail", "high")
        args = argparse.Namespace(severity=None, status=None)
        rc = self.mod.cmd_list(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_list_filter_severity(self):
        import argparse
        self._add_sample("low-one", "low")
        self._add_sample("high-one", "high")
        args = argparse.Namespace(severity="high", status=None)
        rc = self.mod.cmd_list(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_search_match(self):
        import argparse
        self._add_sample("dataclass crash")
        args = argparse.Namespace(query="dataclass")
        rc = self.mod.cmd_search(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_search_no_match(self):
        import argparse
        self._add_sample("test")
        args = argparse.Namespace(query="nonexistent_xyzzy")
        rc = self.mod.cmd_search(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_stats_empty(self):
        import argparse
        rc = self.mod.cmd_stats(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_stats_with_data(self):
        import argparse
        self._add_sample("a", "low")
        self._add_sample("b", "high")
        self._add_sample("c", "medium")
        rc = self.mod.cmd_stats(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_export_json(self):
        import argparse
        self._add_sample()
        args = argparse.Namespace(format="json")
        rc = self.mod.cmd_export(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_export_markdown(self):
        import argparse
        self._add_sample()
        args = argparse.Namespace(format="markdown")
        rc = self.mod.cmd_export(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_lessons_empty(self):
        import argparse
        rc = self.mod.cmd_lessons(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_lessons_with_rules(self):
        import argparse
        self._add_sample()
        rc = self.mod.cmd_lessons(self.tmpdir, argparse.Namespace())
        self.assertEqual(rc, 0)

    def test_cmd_check_no_risk(self):
        import argparse
        args = argparse.Namespace(description="harmless change with no overlap")
        rc = self.mod.cmd_check(self.tmpdir, args)
        self.assertEqual(rc, 0)

    def test_cmd_check_with_risk(self):
        import argparse
        self._add_sample("code bug in module")
        args = argparse.Namespace(description="new code in module with possible bug")
        rc = self.mod.cmd_check(self.tmpdir, args)
        # May return 0 or 1 depending on overlap score
        self.assertIn(rc, (0, 1))


# ── CLI parser tests ─────────────────────────────────────────────────────────


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        p = self.mod.build_parser()
        self.assertIsNotNone(p)

    def test_parse_add(self):
        p = self.mod.build_parser()
        args = p.parse_args([
            "--project-root", "/tmp", "add",
            "--title", "test", "--severity", "high",
            "--description", "d", "--root-cause", "r", "--fix", "f"
        ])
        self.assertEqual(args.command, "add")
        self.assertEqual(args.title, "test")

    def test_parse_list(self):
        p = self.mod.build_parser()
        args = p.parse_args(["list"])
        self.assertEqual(args.command, "list")

    def test_parse_search(self):
        p = self.mod.build_parser()
        args = p.parse_args(["search", "--query", "crash"])
        self.assertEqual(args.command, "search")


# ── CLI integration test ─────────────────────────────────────────────────────


class TestCLIIntegration(unittest.TestCase):
    def test_version(self):
        r = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("failure-museum", r.stdout.lower() + r.stderr.lower())

    def test_add_and_list(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            _make_project(tmpdir)
            r = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmpdir),
                 "add", "--title", "test-crash", "--severity", "high",
                 "--description", "it broke", "--root-cause", "bug",
                 "--fix", "fixed it"],
                capture_output=True, text=True, timeout=30
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("FM-001", r.stdout)

            r2 = subprocess.run(
                [sys.executable, str(TOOL), "--project-root", str(tmpdir),
                 "list"],
                capture_output=True, text=True, timeout=30
            )
            self.assertEqual(r2.returncode, 0)
            self.assertIn("test-crash", r2.stdout)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Constants tests ──────────────────────────────────────────────────────────


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version(self):
        self.assertIsInstance(self.mod.VERSION, str)

    def test_severities(self):
        self.assertIn("low", self.mod.SEVERITIES)
        self.assertIn("medium", self.mod.SEVERITIES)
        self.assertIn("high", self.mod.SEVERITIES)

    def test_museum_dir(self):
        self.assertIsInstance(self.mod.MUSEUM_DIR, str)

    def test_museum_file(self):
        self.assertIsInstance(self.mod.MUSEUM_FILE, str)


if __name__ == "__main__":
    unittest.main()
