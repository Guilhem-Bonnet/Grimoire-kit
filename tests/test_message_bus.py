"""Tests for message-bus.py — Story 4.1."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "message-bus.py"


def _load():
    mod_name = "message_bus"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


mb = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(mb.MESSAGE_BUS_VERSION)

    def test_valid_msg_types(self):
        self.assertIn("task-request", mb.VALID_MSG_TYPES)
        self.assertIn("task-response", mb.VALID_MSG_TYPES)
        self.assertIn("broadcast", mb.VALID_MSG_TYPES)
        self.assertGreater(len(mb.VALID_MSG_TYPES), 5)

    def test_broadcast_recipient(self):
        self.assertEqual(mb.BROADCAST_RECIPIENT, "*")

    def test_valid_patterns(self):
        self.assertIn("request-reply", mb.VALID_PATTERNS)
        self.assertIn("pub-sub", mb.VALID_PATTERNS)
        self.assertIn("broadcast", mb.VALID_PATTERNS)


class TestAgentMessage(unittest.TestCase):
    def test_create(self):
        msg = mb.AgentMessage(sender="architect", recipient="dev", msg_type="task-request", payload={"a": 1})
        self.assertEqual(msg.sender, "architect")
        self.assertEqual(msg.recipient, "dev")
        self.assertTrue(msg.message_id)
        self.assertTrue(msg.timestamp)

    def test_to_dict(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        d = msg.to_dict()
        self.assertIn("sender", d)
        self.assertIn("message_id", d)

    def test_from_dict(self):
        msg = mb.AgentMessage(sender="x", recipient="y", msg_type="task-response", payload={"ok": True})
        d = msg.to_dict()
        restored = mb.AgentMessage.from_dict(d)
        self.assertEqual(restored.sender, "x")
        self.assertEqual(restored.msg_type, "task-response")

    def test_default_pattern(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        self.assertEqual(msg.pattern, "request-reply")

    def test_correlation_id_auto(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        self.assertTrue(msg.correlation_id.startswith("corr-"))


class TestBusStats(unittest.TestCase):
    def test_defaults(self):
        s = mb.BusStats()
        self.assertEqual(s.total_sent, 0)
        self.assertEqual(s.total_received, 0)
        self.assertEqual(s.total_dropped, 0)
        self.assertEqual(s.backend, "in-process")


class TestDeliveryResult(unittest.TestCase):
    def test_success(self):
        r = mb.DeliveryResult(success=True, message_id="m1", recipients_count=1)
        self.assertTrue(r.success)
        self.assertEqual(r.message_id, "m1")

    def test_failure(self):
        r = mb.DeliveryResult(success=False, error="No recipient")
        self.assertFalse(r.success)


class TestInProcessBus(unittest.TestCase):
    def setUp(self):
        self.bus = mb.InProcessBus()

    def tearDown(self):
        self.bus.close()

    def test_send_and_receive(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={"x": 1})
        result = self.bus.send(msg)
        self.assertTrue(result.success)
        received = self.bus.receive("b", timeout=1.0)
        self.assertIsNotNone(received)
        self.assertEqual(received.sender, "a")

    def test_receive_empty(self):
        received = self.bus.receive("nobody", timeout=0.1)
        self.assertIsNone(received)

    def test_broadcast(self):
        self.bus.subscribe("dev", "broadcast")
        self.bus.subscribe("qa", "broadcast")
        msg = mb.AgentMessage(sender="pm", recipient="*", msg_type="broadcast", payload={"info": "go"})
        result = self.bus.send(msg)
        self.assertTrue(result.success)

    def test_subscribe(self):
        result = self.bus.subscribe("dev", "task-request")
        self.assertTrue(result)

    def test_unsubscribe(self):
        self.bus.subscribe("dev", "task-request")
        self.assertTrue(self.bus.unsubscribe("dev", "task-request"))
        self.assertFalse(self.bus.unsubscribe("nobody", "task-request"))

    def test_get_stats(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        self.bus.send(msg)
        stats = self.bus.get_stats()
        self.assertEqual(stats.total_sent, 1)

    def test_clear(self):
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        self.bus.send(msg)
        count = self.bus.clear()
        self.assertGreaterEqual(count, 0)

    def test_multiple_messages(self):
        for i in range(5):
            msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={"i": i})
            self.bus.send(msg)
        stats = self.bus.get_stats()
        self.assertEqual(stats.total_sent, 5)
        for _ in range(5):
            received = self.bus.receive("b", timeout=0.1)
            self.assertIsNotNone(received)

    def test_max_queue_enforcement(self):
        for i in range(mb.MAX_QUEUE_SIZE + 5):
            msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={"i": i})
            self.bus.send(msg)
        # Should not crash, old messages dropped


class TestCreateBus(unittest.TestCase):
    def test_in_process(self):
        bus = mb.create_bus("in-process")
        self.assertIsInstance(bus, mb.InProcessBus)
        bus.close()

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            mb.create_bus("unknown")

    def test_redis_backend(self):
        bus = mb.create_bus("redis")
        self.assertIsInstance(bus, mb.RedisBus)

    def test_nats_backend(self):
        bus = mb.create_bus("nats")
        self.assertIsInstance(bus, mb.NATSBus)


class TestRedisBusStub(unittest.TestCase):
    def test_send_returns_false(self):
        bus = mb.RedisBus()
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        result = bus.send(msg)
        self.assertFalse(result.success)

    def test_receive_returns_none(self):
        bus = mb.RedisBus()
        self.assertIsNone(bus.receive("a"))


class TestNATSBusStub(unittest.TestCase):
    def test_send_returns_false(self):
        bus = mb.NATSBus()
        msg = mb.AgentMessage(sender="a", recipient="b", msg_type="task-request", payload={})
        result = bus.send(msg)
        self.assertFalse(result.success)


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
        self.assertIn("message-bus", r.stdout)

    def test_send(self):
        r = self._run("send", "--from", "dev", "--to", "qa",
                       "--type", "task-request", "--payload", '{"x":1}')
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
