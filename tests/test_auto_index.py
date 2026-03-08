#!/usr/bin/env python3
"""
Tests pour auto-index.py — Auto-indexation RAG Grimoire.

Fonctions testées :
  - _hash_file()
  - load_index() / save_index()
  - discover_files()
  - detect_changes()
  - run_indexation()
  - install_hook() / uninstall_hook()
  - show_status()
  - build_parser()
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
TOOL = KIT_DIR / "framework" / "tools" / "auto-index.py"


def _import_mod():
    """Import le module auto-index via importlib."""
    mod_name = "auto_index"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "auto-index.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet Grimoire minimal."""
    (root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    return root


def _write_file(root: Path, rel_path: str, content: str = "x = 1\n") -> Path:
    filepath = root / rel_path
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ── Tests Dataclasses ─────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_file_state_exists(self):
        self.assertTrue(hasattr(self.mod, "FileState"))

    def test_index_state_exists(self):
        self.assertTrue(hasattr(self.mod, "IndexState"))

    def test_auto_index_report_exists(self):
        self.assertTrue(hasattr(self.mod, "AutoIndexReport"))

    def test_report_to_dict(self):
        report = self.mod.AutoIndexReport(files_checked=5, files_indexed=2)
        d = report.to_dict()
        self.assertEqual(d["files_checked"], 5)
        self.assertEqual(d["files_indexed"], 2)


# ── Tests Hash ────────────────────────────────────────────────────────────────

class TestHash(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_hash_file(self):
        fp = self.tmpdir / "test.txt"
        fp.write_text("hello world", encoding="utf-8")
        h = self.mod._hash_file(fp)
        self.assertEqual(len(h), 32)

    def test_hash_changes_with_content(self):
        fp = self.tmpdir / "test.txt"
        fp.write_text("hello", encoding="utf-8")
        h1 = self.mod._hash_file(fp)
        fp.write_text("world", encoding="utf-8")
        h2 = self.mod._hash_file(fp)
        self.assertNotEqual(h1, h2)

    def test_hash_nonexistent(self):
        h = self.mod._hash_file(self.tmpdir / "nope.txt")
        self.assertEqual(h, "")


# ── Tests Index Persistence ──────────────────────────────────────────────────

class TestIndexPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty_index(self):
        state = self.mod.load_index(self.tmpdir)
        self.assertEqual(len(state.files), 0)

    def test_save_and_load(self):
        state = self.mod.IndexState()
        state.files["test.py"] = self.mod.FileState(
            path="test.py", hash="abc123", indexed_at="2026-01-01", size=100)
        state.last_run = "2026-01-01"
        state.total_indexed = 1

        self.mod.save_index(self.tmpdir, state)
        loaded = self.mod.load_index(self.tmpdir)

        self.assertEqual(len(loaded.files), 1)
        self.assertIn("test.py", loaded.files)
        self.assertEqual(loaded.files["test.py"].hash, "abc123")
        self.assertEqual(loaded.total_indexed, 1)

    def test_corrupted_index_returns_empty(self):
        index_path = self.tmpdir / "_grimoire-output" / ".auto-index-hashes.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("not json", encoding="utf-8")
        state = self.mod.load_index(self.tmpdir)
        self.assertEqual(len(state.files), 0)


# ── Tests File Discovery ─────────────────────────────────────────────────────

class TestDiscoverFiles(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_python_files(self):
        _write_file(self.tmpdir, "src/app.py")
        _write_file(self.tmpdir, "src/utils.py")
        files = self.mod.discover_files(self.tmpdir)
        py_files = [f for f in files if f.suffix == ".py"]
        self.assertGreaterEqual(len(py_files), 2)

    def test_discover_excludes_pycache(self):
        _write_file(self.tmpdir, "src/__pycache__/cache.py")
        _write_file(self.tmpdir, "src/app.py")
        files = self.mod.discover_files(self.tmpdir)
        pycache = [f for f in files if "__pycache__" in str(f)]
        self.assertEqual(len(pycache), 0)

    def test_discover_markdown(self):
        _write_file(self.tmpdir, "docs/readme.md", "# Hello")
        files = self.mod.discover_files(self.tmpdir)
        md_files = [f for f in files if f.suffix == ".md"]
        self.assertGreaterEqual(len(md_files), 1)


# ── Tests Change Detection ───────────────────────────────────────────────────

class TestDetectChanges(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_new_files(self):
        _write_file(self.tmpdir, "src/app.py")
        state = self.mod.IndexState()
        files = self.mod.discover_files(self.tmpdir)
        new_or_mod, unchanged, deleted = self.mod.detect_changes(
            self.tmpdir, state, files)
        self.assertGreater(len(new_or_mod), 0)
        self.assertEqual(len(unchanged), 0)

    def test_unchanged_files(self):
        fp = _write_file(self.tmpdir, "src/app.py")
        state = self.mod.IndexState()
        state.files["src/app.py"] = self.mod.FileState(
            path="src/app.py",
            hash=self.mod._hash_file(fp),
            indexed_at="2026-01-01",
        )
        files = [fp]
        new_or_mod, unchanged, deleted = self.mod.detect_changes(
            self.tmpdir, state, files)
        self.assertEqual(len(new_or_mod), 0)
        self.assertEqual(len(unchanged), 1)

    def test_modified_files(self):
        fp = _write_file(self.tmpdir, "src/app.py", "v1")
        state = self.mod.IndexState()
        state.files["src/app.py"] = self.mod.FileState(
            path="src/app.py", hash="old_hash", indexed_at="2026-01-01")
        files = [fp]
        new_or_mod, unchanged, deleted = self.mod.detect_changes(
            self.tmpdir, state, files)
        self.assertEqual(len(new_or_mod), 1)

    def test_deleted_files(self):
        state = self.mod.IndexState()
        state.files["gone.py"] = self.mod.FileState(
            path="gone.py", hash="abc", indexed_at="2026-01-01")
        new_or_mod, unchanged, deleted = self.mod.detect_changes(
            self.tmpdir, state, [])
        self.assertEqual(len(deleted), 1)


# ── Tests Indexation ─────────────────────────────────────────────────────────

class TestRunIndexation(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_index_new_files(self):
        _write_file(self.tmpdir, "src/app.py")
        state = self.mod.IndexState()
        files = self.mod.discover_files(self.tmpdir)
        report = self.mod.run_indexation(self.tmpdir, files, state, quiet=True)
        self.assertGreater(report.files_indexed, 0)
        self.assertGreater(report.files_new, 0)

    def test_index_unchanged_skipped(self):
        fp = _write_file(self.tmpdir, "src/app.py")
        state = self.mod.IndexState()
        state.files["src/app.py"] = self.mod.FileState(
            path="src/app.py",
            hash=self.mod._hash_file(fp),
            indexed_at="2026-01-01",
        )
        report = self.mod.run_indexation(self.tmpdir, [fp], state, quiet=True)
        self.assertEqual(report.files_indexed, 0)
        self.assertEqual(report.files_unchanged, 1)


# ── Tests Git Hook ────────────────────────────────────────────────────────────

class TestGitHook(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)
        # Init a git repo
        subprocess.run(["git", "init"], cwd=self.tmpdir,
                       capture_output=True, timeout=5)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_hook(self):
        ok, msg = self.mod.install_hook(self.tmpdir)
        self.assertTrue(ok)
        hook = self.tmpdir / ".git" / "hooks" / "post-commit"
        self.assertTrue(hook.exists())
        content = hook.read_text()
        self.assertIn("Grimoire-AUTO-INDEX", content)

    def test_install_idempotent(self):
        self.mod.install_hook(self.tmpdir)
        ok, msg = self.mod.install_hook(self.tmpdir)
        self.assertTrue(ok)
        self.assertIn("déjà", msg)

    def test_uninstall_hook(self):
        self.mod.install_hook(self.tmpdir)
        ok, msg = self.mod.uninstall_hook(self.tmpdir)
        self.assertTrue(ok)

    def test_uninstall_no_hook(self):
        ok, msg = self.mod.uninstall_hook(self.tmpdir)
        self.assertTrue(ok)


# ── Tests Status ──────────────────────────────────────────────────────────────

class TestStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_show_status_empty(self):
        output = self.mod.show_status(self.tmpdir)
        self.assertIn("Auto-Index Status", output)
        self.assertIn("jamais", output)

    def test_show_status_json(self):
        output = self.mod.show_status(self.tmpdir, as_json=True)
        data = json.loads(output)
        self.assertIn("files_tracked", data)


# ── Tests CLI ─────────────────────────────────────────────────────────────────

class TestCli(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_version(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("auto-index", result.stdout)

    def test_no_command(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
