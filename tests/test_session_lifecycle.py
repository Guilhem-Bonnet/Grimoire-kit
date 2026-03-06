"""Tests for session-lifecycle.py — Story 5.2."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "session-lifecycle.py"


def _load():
    mod_name = "session_lifecycle"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


sl = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(sl.SESSION_LIFECYCLE_VERSION)

    def test_lifecycle_dir(self):
        self.assertIn("session-lifecycle", sl.LIFECYCLE_DIR)


class TestHookResult(unittest.TestCase):
    def test_defaults(self):
        h = sl.HookResult(name="test")
        self.assertEqual(h.name, "test")
        self.assertEqual(h.status, "pending")

    def test_completed(self):
        h = sl.HookResult(name="test", status="completed", message="OK")
        self.assertEqual(h.status, "completed")


class TestLifecycleResult(unittest.TestCase):
    def test_defaults(self):
        r = sl.LifecycleResult(phase="pre")
        self.assertEqual(r.phase, "pre")
        self.assertEqual(r.status, "pending")
        self.assertIsInstance(r.hooks, list)

    def test_to_dict(self):
        r = sl.LifecycleResult(phase="post", status="completed")
        d = r.to_dict()
        self.assertEqual(d["phase"], "post")
        self.assertIn("hooks", d)


class TestPreSession(unittest.TestCase):
    def test_pre_session_no_memory(self):
        """Pre-session sur un dossier vide — hooks skipped/completed."""
        with tempfile.TemporaryDirectory() as tmp:
            result = sl.run_pre_session(Path(tmp))
            self.assertIn(result.status, ("completed", "partial"))
            self.assertEqual(len(result.hooks), 2)
            # health-check hook
            self.assertEqual(result.hooks[0].name, "health-check")
            # memory-integrity hook
            self.assertEqual(result.hooks[1].name, "memory-integrity")
            self.assertEqual(result.hooks[1].status, "skipped")

    def test_pre_session_with_memory(self):
        """Pre-session avec dossier _memory/."""
        with tempfile.TemporaryDirectory() as tmp:
            mem_dir = Path(tmp) / "_memory"
            mem_dir.mkdir()
            (mem_dir / "shared-context.md").write_text("# Test\n", encoding="utf-8")
            result = sl.run_pre_session(Path(tmp))
            self.assertIn(result.status, ("completed", "partial"))
            integrity_hook = result.hooks[1]
            self.assertEqual(integrity_hook.status, "completed")
            self.assertIn("OK", integrity_hook.message)

    def test_pre_session_corrupt_json(self):
        """Pre-session avec memories.json corrompu."""
        with tempfile.TemporaryDirectory() as tmp:
            mem_dir = Path(tmp) / "_memory"
            mem_dir.mkdir()
            (mem_dir / "memories.json").write_text("{invalid", encoding="utf-8")
            result = sl.run_pre_session(Path(tmp))
            integrity_hook = result.hooks[1]
            self.assertIn("Problèmes", integrity_hook.message)


class TestPostSession(unittest.TestCase):
    def test_post_session_no_tools(self):
        """Post-session sans outils — hooks skipped/failed gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            result = sl.run_post_session(Path(tmp))
            self.assertIn(result.status, ("completed", "partial"))
            self.assertEqual(len(result.hooks), 3)
            self.assertEqual(result.hooks[0].name, "dream-quick")
            self.assertEqual(result.hooks[1].name, "stigmergy-evaporate")
            self.assertEqual(result.hooks[2].name, "session-save")


class TestStatus(unittest.TestCase):
    def test_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = sl.get_status(Path(tmp))
            self.assertEqual(status["status"], "no-session")

    def test_after_pre(self):
        with tempfile.TemporaryDirectory() as tmp:
            sl.run_pre_session(Path(tmp))
            status = sl.get_status(Path(tmp))
            self.assertEqual(status["phase"], "pre")


class TestSaveState(unittest.TestCase):
    def test_state_file_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = sl.LifecycleResult(phase="test", status="completed")
            sl._save_state(Path(tmp), result)
            state_file = Path(tmp) / sl.LIFECYCLE_DIR / sl.STATE_FILE
            self.assertTrue(state_file.exists())
            data = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(data["phase"], "test")


class TestCLI(unittest.TestCase):
    def test_parser_commands(self):
        parser = sl.build_parser()
        args = parser.parse_args(["--project-root", "/tmp", "pre"])
        self.assertEqual(args.command, "pre")

    def test_parser_post(self):
        parser = sl.build_parser()
        args = parser.parse_args(["--json", "post"])
        self.assertEqual(args.command, "post")
        self.assertTrue(args.as_json)

    def test_parser_status(self):
        parser = sl.build_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")


if __name__ == "__main__":
    unittest.main()
