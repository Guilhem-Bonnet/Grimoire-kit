"""Tests for conversation-branch.py — Story 5.1."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "conversation-branch.py"


def _load():
    mod_name = "conversation_branch"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


cb = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(cb.CONVERSATION_BRANCH_VERSION)

    def test_default_branch(self):
        self.assertEqual(cb.DEFAULT_BRANCH, "main")

    def test_max_branches(self):
        self.assertGreater(cb.MAX_BRANCHES, 5)


class TestContextSnapshot(unittest.TestCase):
    def test_defaults(self):
        cs = cb.ContextSnapshot()
        self.assertEqual(cs.loaded_files_hash, "")
        self.assertEqual(cs.tokens_used, 0)
        self.assertEqual(cs.loaded_files, [])

    def test_with_data(self):
        cs = cb.ContextSnapshot(
            loaded_files=["a.py", "b.py"],
            conversation_summary="Test summary",
            decisions_in_branch=["Decided X"],
            tokens_used=5000,
        )
        self.assertEqual(len(cs.loaded_files), 2)
        self.assertIn("Decided X", cs.decisions_in_branch)


class TestBranchInfo(unittest.TestCase):
    def test_create(self):
        b = cb.BranchInfo(name="feature-x", purpose="Explore option X")
        self.assertEqual(b.name, "feature-x")
        self.assertEqual(b.status, "active")

    def test_to_dict(self):
        b = cb.BranchInfo(name="test", purpose="test")
        d = b.to_dict()
        self.assertIn("name", d)
        self.assertIn("context_snapshot", d)

    def test_from_dict(self):
        b = cb.BranchInfo(name="foo", created_by="architect", purpose="test purpose")
        d = b.to_dict()
        restored = cb.BranchInfo.from_dict(d)
        self.assertEqual(restored.name, "foo")
        self.assertEqual(restored.created_by, "architect")


class TestBranchTree(unittest.TestCase):
    def test_defaults(self):
        tree = cb.BranchTree()
        self.assertEqual(tree.total_branches, 0)
        self.assertEqual(tree.active_count, 0)
        self.assertEqual(tree.active_branch, cb.DEFAULT_BRANCH)

    def test_with_branches(self):
        tree = cb.BranchTree(
            branches=[cb.BranchInfo(name="a"), cb.BranchInfo(name="b")],
            total_branches=2,
            active_count=2,
        )
        self.assertEqual(len(tree.branches), 2)
        self.assertEqual(tree.total_branches, 2)


class TestBranchManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = cb.BranchManager(Path(self.tmpdir))

    def test_create_branch(self):
        info = self.mgr.branch("feature-x", purpose="Explore X", agent="architect")
        self.assertEqual(info.name, "feature-x")
        self.assertEqual(info.purpose, "Explore X")
        self.assertEqual(info.status, "active")

    def test_create_duplicate_branch(self):
        self.mgr.branch("dup")
        with self.assertRaises(ValueError):
            self.mgr.branch("dup")

    def test_switch_branch(self):
        self.mgr.branch("b1")
        self.mgr.branch("b2")
        self.mgr.switch("b1")

    def test_switch_nonexistent(self):
        with self.assertRaises(ValueError):
            self.mgr.switch("nope")

    def test_get_info(self):
        self.mgr.branch("info-test", purpose="Testing")
        info = self.mgr.get_info("info-test")
        self.assertIsNotNone(info)
        self.assertEqual(info.purpose, "Testing")

    def test_list_branches(self):
        self.mgr.branch("a")
        self.mgr.branch("b")
        tree = self.mgr.list_branches()
        self.assertGreaterEqual(tree.total_branches, 3)

    def test_archive_branch(self):
        self.mgr.branch("to-archive")
        result = self.mgr.archive("to-archive")
        self.assertEqual(result.status, "archived")

    def test_delete_requires_archive(self):
        self.mgr.branch("to-delete")
        self.mgr.archive("to-delete")
        result = self.mgr.delete("to-delete")
        self.assertTrue(result)
        info = self.mgr.get_info("to-delete")
        self.assertIsNone(info)

    def test_update_snapshot(self):
        self.mgr.branch("snap-test")
        result = self.mgr.update_snapshot(
            "snap-test",
            loaded_files=["file1.py"],
            conversation_summary="Updated",
            tokens_used=1000,
        )
        self.assertIsNotNone(result)
        info = self.mgr.get_info("snap-test")
        self.assertEqual(info.context_snapshot.tokens_used, 1000)

    def test_max_branches_limit(self):
        for i in range(cb.MAX_BRANCHES - 1):
            self.mgr.branch(f"branch-{i}")
        with self.assertRaises(ValueError):
            self.mgr.branch("one-too-many")

    def test_ensure_main(self):
        info = self.mgr.get_info("main")
        self.assertIsNotNone(info)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_branch(self):
        result = cb.mcp_conversation_branch(self.tmpdir, action="branch", name="test-mcp")
        self.assertTrue(result.get("success"))

    def test_mcp_list(self):
        cb.mcp_conversation_branch(self.tmpdir, action="branch", name="a")
        result = cb.mcp_conversation_branch(self.tmpdir, action="list")
        self.assertIn("branches", result)

    def test_mcp_info(self):
        cb.mcp_conversation_branch(self.tmpdir, action="branch", name="info-mcp")
        result = cb.mcp_conversation_branch(self.tmpdir, action="info", name="info-mcp")
        self.assertIn("name", result)
        self.assertEqual(result["name"], "info-mcp")

    def test_mcp_switch(self):
        cb.mcp_conversation_branch(self.tmpdir, action="branch", name="sw")
        result = cb.mcp_conversation_branch(self.tmpdir, action="switch", name="sw")
        self.assertTrue(result.get("success"))


class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL)] + list(args),
            capture_output=True, text=True, timeout=15,
        )

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("conversation-branch", r.stdout)

    def test_list(self):
        r = self._run("--project-root", "/tmp/test-cb", "list")
        self.assertEqual(r.returncode, 0)

    def test_branch_and_info(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "branch", "--name", "test-cli")
        self.assertEqual(r.returncode, 0)
        r2 = self._run("--project-root", tmpdir, "info", "--name", "test-cli")
        self.assertEqual(r2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
