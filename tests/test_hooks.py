"""Tests for grimoire.core.hooks — pluggable lifecycle hook registry."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.hooks import (
    VALID_HOOKS,
    HookContext,
    HookManager,
    failure_capturer,
    quality_gate,
)


class TestHookContext(unittest.TestCase):
    def test_defaults(self) -> None:
        ctx = HookContext()
        self.assertEqual(ctx.hook, "")
        self.assertEqual(ctx.tool, "")
        self.assertEqual(ctx.status, "")
        self.assertEqual(ctx.duration_s, 0.0)
        self.assertIsInstance(ctx.metadata, dict)
        self.assertTrue(ctx.timestamp)

    def test_custom_values(self) -> None:
        ctx = HookContext(hook="post_tool_use", tool="ruff", status="success", duration_s=1.5)
        self.assertEqual(ctx.hook, "post_tool_use")
        self.assertEqual(ctx.tool, "ruff")
        self.assertEqual(ctx.status, "success")
        self.assertAlmostEqual(ctx.duration_s, 1.5)


class TestValidHooks(unittest.TestCase):
    def test_contains_all_expected(self) -> None:
        expected = {"session_start", "pre_tool_use", "post_tool_use", "user_prompt", "session_end"}
        self.assertEqual(VALID_HOOKS, expected)

    def test_is_frozenset(self) -> None:
        self.assertIsInstance(VALID_HOOKS, frozenset)


class TestHookManagerRegister(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = HookManager()

    def test_register_valid_hook(self) -> None:
        self.mgr.register("session_start", lambda ctx: None, name="test")
        self.assertIn("session_start", self.mgr.registered_hooks)
        self.assertIn("test", self.mgr.registered_hooks["session_start"])

    def test_register_invalid_hook_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mgr.register("invalid_hook", lambda ctx: None)

    def test_register_auto_name(self) -> None:
        def my_listener(ctx: HookContext) -> None:
            pass

        self.mgr.register("session_end", my_listener)
        self.assertIn("my_listener", self.mgr.registered_hooks["session_end"])

    def test_multiple_listeners(self) -> None:
        self.mgr.register("post_tool_use", lambda ctx: None, name="a")
        self.mgr.register("post_tool_use", lambda ctx: None, name="b")
        self.assertEqual(len(self.mgr.registered_hooks["post_tool_use"]), 2)


class TestHookManagerUnregister(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = HookManager()
        self.mgr.register("session_start", lambda ctx: None, name="removable")

    def test_unregister_existing(self) -> None:
        removed = self.mgr.unregister("session_start", name="removable")
        self.assertTrue(removed)
        self.assertNotIn("session_start", self.mgr.registered_hooks)

    def test_unregister_nonexistent(self) -> None:
        removed = self.mgr.unregister("session_start", name="ghost")
        self.assertFalse(removed)


class TestHookManagerTrigger(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = HookManager()
        self.call_log: list[str] = []

    def test_trigger_fires_listeners(self) -> None:
        self.mgr.register("session_start", lambda ctx: self.call_log.append("fired"), name="logger")
        executed = self.mgr.trigger("session_start")
        self.assertEqual(executed, ["logger"])
        self.assertEqual(self.call_log, ["fired"])

    def test_trigger_invalid_hook_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mgr.trigger("bogus_hook")

    def test_trigger_no_listeners(self) -> None:
        executed = self.mgr.trigger("session_end")
        self.assertEqual(executed, [])

    def test_trigger_failing_listener_does_not_crash(self) -> None:
        def bad_listener(ctx: HookContext) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        self.mgr.register("session_start", bad_listener, name="bad")
        self.mgr.register("session_start", lambda ctx: self.call_log.append("ok"), name="good")
        executed = self.mgr.trigger("session_start")
        # bad should not appear, good should execute
        self.assertNotIn("bad", executed)
        self.assertIn("good", executed)
        self.assertEqual(self.call_log, ["ok"])

    def test_trigger_sets_hook_on_context(self) -> None:
        captured: list[str] = []
        self.mgr.register("user_prompt", lambda ctx: captured.append(ctx.hook), name="cap")
        self.mgr.trigger("user_prompt")
        self.assertEqual(captured, ["user_prompt"])


class TestHookManagerAudit(unittest.TestCase):
    def test_audit_creates_file(self) -> None:
        with TemporaryDirectory() as tmp:
            audit = Path(tmp) / "hooks-audit.jsonl"
            mgr = HookManager(audit_path=audit)
            mgr.register("session_start", lambda ctx: None, name="noop")
            mgr.trigger("session_start")
            self.assertTrue(audit.exists())
            lines = audit.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["hook"], "session_start")
            self.assertEqual(entry["listener"], "noop")
            self.assertEqual(entry["result"], "ok")

    def test_audit_records_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            audit = Path(tmp) / "hooks-audit.jsonl"
            mgr = HookManager(audit_path=audit)

            def fail(ctx: HookContext) -> None:
                msg = "fail"
                raise RuntimeError(msg)

            mgr.register("session_end", fail, name="crasher")
            mgr.trigger("session_end")
            entry = json.loads(audit.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["result"], "error")


class TestRegisteredHooks(unittest.TestCase):
    def test_empty_manager(self) -> None:
        mgr = HookManager()
        self.assertEqual(mgr.registered_hooks, {})

    def test_only_populated_hooks(self) -> None:
        mgr = HookManager()
        mgr.register("session_start", lambda ctx: None, name="a")
        hooks = mgr.registered_hooks
        self.assertIn("session_start", hooks)
        self.assertNotIn("session_end", hooks)


class TestFailureCapturer(unittest.TestCase):
    def test_ignores_success(self) -> None:
        ctx = HookContext(status="success")
        failure_capturer(ctx)  # should not raise

    def test_captures_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            museum = Path(tmp) / "failures.jsonl"
            ctx = HookContext(
                status="failure",
                tool="ruff",
                message="lint error",
                metadata={"failure_museum_path": str(museum)},
            )
            failure_capturer(ctx)
            self.assertTrue(museum.exists())
            entry = json.loads(museum.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["tool"], "ruff")
            self.assertEqual(entry["message"], "lint error")


class TestQualityGate(unittest.TestCase):
    def test_does_not_crash_on_success(self) -> None:
        ctx = HookContext(status="success", tool="pytest")
        quality_gate(ctx)  # should not raise

    def test_does_not_crash_on_failure(self) -> None:
        ctx = HookContext(status="failure", tool="ruff")
        quality_gate(ctx)  # should not raise


if __name__ == "__main__":
    unittest.main()
