"""Tests for context-merge.py — Story 5.2."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "context-merge.py"


def _load():
    mod_name = "context_merge"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


cm = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(cm.CONTEXT_MERGE_VERSION)

    def test_default_branch(self):
        self.assertEqual(cm.DEFAULT_BRANCH, "main")


class TestArtifactDiff(unittest.TestCase):
    def test_create(self):
        ad = cm.ArtifactDiff(path="test.md", status="added", branch="feature")
        self.assertEqual(ad.path, "test.md")
        self.assertEqual(ad.status, "added")


class TestDecisionDiff(unittest.TestCase):
    def test_create(self):
        dd = cm.DecisionDiff(decision="Use Python")
        self.assertEqual(dd.decision, "Use Python")
        self.assertFalse(dd.conflict)


class TestBranchDiff(unittest.TestCase):
    def test_defaults(self):
        bd = cm.BranchDiff(branch_a="main", branch_b="feature")
        self.assertEqual(bd.total_differences, 0)
        self.assertFalse(bd.has_conflicts)

    def test_to_dict(self):
        bd = cm.BranchDiff(branch_a="a", branch_b="b")
        d = bd.to_dict()
        self.assertIn("branch_a", d)


class TestMergeAction(unittest.TestCase):
    def test_create(self):
        ma = cm.MergeAction(action_type="copy", source_path="/a", target_path="/b")
        self.assertEqual(ma.action_type, "copy")


class TestMergeResult(unittest.TestCase):
    def test_defaults(self):
        mr = cm.MergeResult()
        self.assertTrue(mr.merge_id.startswith("merge-"))
        self.assertEqual(mr.status, "pending")

    def test_to_dict(self):
        mr = cm.MergeResult(source_branch="feat", target_branch="main")
        d = mr.to_dict()
        self.assertEqual(d["source_branch"], "feat")


class TestContextMerger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.merger = cm.ContextMerger(Path(self.tmpdir))
        self.runs_dir = Path(self.tmpdir) / cm.RUNS_DIR
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _create_branch(self, name: str, files: dict | None = None, decisions: list | None = None):
        """Helper to create a branch directory with files."""
        branch_dir = self.runs_dir / name
        branch_dir.mkdir(parents=True, exist_ok=True)
        if files:
            for fname, content in files.items():
                fpath = branch_dir / fname
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")
        if decisions:
            manifest = branch_dir / "branch.json"
            manifest.write_text(json.dumps({
                "context_snapshot": {"decisions_in_branch": decisions}
            }, ensure_ascii=False), encoding="utf-8")

    def test_diff_empty_branches(self):
        self._create_branch("main")
        self._create_branch("feature")
        diff = self.merger.diff("main", "feature")
        self.assertEqual(diff.total_differences, 0)

    def test_diff_added_files(self):
        self._create_branch("main")
        self._create_branch("feature", files={"new-file.md": "Hello"})
        diff = self.merger.diff("main", "feature")
        self.assertEqual(len(diff.artifacts_only_b), 1)
        self.assertGreater(diff.total_differences, 0)

    def test_diff_modified_files(self):
        self._create_branch("main", files={"doc.md": "abc"})
        self._create_branch("feature", files={"doc.md": "abcdef"})
        diff = self.merger.diff("main", "feature")
        self.assertEqual(len(diff.artifacts_modified), 1)

    def test_diff_same_files(self):
        self._create_branch("main", files={"doc.md": "same"})
        self._create_branch("feature", files={"doc.md": "same"})
        diff = self.merger.diff("main", "feature")
        self.assertEqual(len(diff.artifacts_same), 1)

    def test_diff_decisions(self):
        self._create_branch("main", decisions=["Use Python"])
        self._create_branch("feature", decisions=["Use Rust"])
        diff = self.merger.diff("main", "feature")
        self.assertEqual(len(diff.decisions_a), 1)
        self.assertEqual(len(diff.decisions_b), 1)

    def test_preview(self):
        self._create_branch("main")
        self._create_branch("feature", files={"new.md": "content"})
        actions = self.merger.preview("feature", "main")
        self.assertGreater(len(actions), 0)
        self.assertEqual(actions[0].action_type, "copy")

    def test_merge_simple(self):
        self._create_branch("main")
        self._create_branch("feature", files={"new.md": "content"})
        result = self.merger.merge("feature", "main")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.files_copied, 1)

    def test_merge_nonexistent_source(self):
        result = self.merger.merge("nonexistent", "main")
        self.assertEqual(result.status, "failed")
        self.assertGreater(len(result.errors), 0)

    def test_merge_force(self):
        self._create_branch("main", files={"doc.md": "old"})
        self._create_branch("feature", files={"doc.md": "new content"})
        result = self.merger.merge("feature", "main", force=True)
        self.assertIn(result.status, ("completed", "partial"))

    def test_get_merge_log(self):
        log = self.merger.get_merge_log()
        self.assertIsInstance(log, str)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        runs_dir = Path(self.tmpdir) / cm.RUNS_DIR
        runs_dir.mkdir(parents=True, exist_ok=True)
        # Create test branches
        for name in ("main", "feature"):
            (runs_dir / name).mkdir(exist_ok=True)

    def test_mcp_diff(self):
        result = cm.mcp_context_merge(
            self.tmpdir, action="diff",
            branch_a="main", branch_b="feature",
        )
        self.assertIn("branch_a", result)

    def test_mcp_diff_missing_param(self):
        result = cm.mcp_context_merge(self.tmpdir, action="diff")
        self.assertIn("error", result)

    def test_mcp_merge_missing_source(self):
        result = cm.mcp_context_merge(self.tmpdir, action="merge")
        self.assertIn("error", result)

    def test_mcp_log(self):
        result = cm.mcp_context_merge(self.tmpdir, action="log")
        self.assertIn("log", result)

    def test_mcp_preview(self):
        result = cm.mcp_context_merge(
            self.tmpdir, action="preview",
            source="feature", target="main",
        )
        self.assertIn("actions", result)


class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *list(args)],
            capture_output=True, text=True, timeout=15,
        )

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("context-merge", r.stdout)

    def test_diff(self):
        tmpdir = tempfile.mkdtemp()
        runs_dir = Path(tmpdir) / cm.RUNS_DIR
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "main").mkdir()
        (runs_dir / "feature").mkdir()
        r = self._run("--project-root", tmpdir, "diff",
                       "--branch-a", "main", "--branch-b", "feature")
        self.assertEqual(r.returncode, 0)

    def test_log(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "log")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
