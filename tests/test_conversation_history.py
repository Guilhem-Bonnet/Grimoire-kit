"""Tests for conversation-history.py — Story 5.4."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "conversation-history.py"


def _load():
    mod_name = "conversation_history"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


ch = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(ch.CONVERSATION_HISTORY_VERSION)

    def test_max_conversations(self):
        self.assertEqual(ch.MAX_CONVERSATIONS, 50)

    def test_embedding_dim(self):
        self.assertEqual(ch.EMBEDDING_DIM, 384)


class TestConversationEntry(unittest.TestCase):
    def test_create(self):
        e = ch.ConversationEntry(summary="Test session", agents=["dev", "qa"])
        self.assertTrue(e.conversation_id.startswith("conv-"))
        self.assertTrue(e.timestamp)
        self.assertEqual(len(e.agents), 2)

    def test_to_dict(self):
        e = ch.ConversationEntry(summary="Test")
        d = e.to_dict()
        self.assertIn("conversation_id", d)
        self.assertIn("summary", d)

    def test_from_dict(self):
        e = ch.ConversationEntry(summary="Test", agents=["dev"], topics=["architecture"])
        d = e.to_dict()
        restored = ch.ConversationEntry.from_dict(d)
        self.assertEqual(restored.summary, "Test")
        self.assertEqual(restored.agents, ["dev"])


class TestSearchResult(unittest.TestCase):
    def test_create(self):
        entry = ch.ConversationEntry(summary="X")
        sr = ch.SearchResult(conversation=entry, score=0.85, match_method="keyword")
        self.assertEqual(sr.score, 0.85)


class TestHistoryStats(unittest.TestCase):
    def test_defaults(self):
        s = ch.HistoryStats()
        self.assertEqual(s.total_conversations, 0)
        self.assertEqual(s.total_tokens, 0)
        self.assertFalse(s.qdrant_available)


class TestJSONHistoryBackend(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.backend = ch.JSONHistoryBackend(self.tmpdir)

    def test_store_and_get(self):
        e = ch.ConversationEntry(summary="Test store")
        self.backend.store(e)
        entries = self.backend.get_all()
        self.assertEqual(len(entries), 1)

    def test_search_keyword(self):
        e = ch.ConversationEntry(summary="architecture review discussion", topics=["architecture"])
        self.backend.store(e)
        results = self.backend.search("architecture")
        self.assertGreater(len(results), 0)

    def test_search_no_match(self):
        e = ch.ConversationEntry(summary="python code")
        self.backend.store(e)
        results = self.backend.search("nonexistent-xyz")
        self.assertEqual(len(results), 0)

    def test_forget(self):
        e = ch.ConversationEntry(summary="secret topic data", topics=["secret"])
        self.backend.store(e)
        count = self.backend.forget("secret")
        self.assertEqual(count, 1)
        entries = self.backend.get_all()
        self.assertEqual(len(entries), 0)

    def test_max_cap(self):
        for i in range(ch.MAX_CONVERSATIONS + 10):
            self.backend.store(ch.ConversationEntry(summary=f"Entry {i}"))
        entries = self.backend.get_all()
        self.assertLessEqual(len(entries), ch.MAX_CONVERSATIONS)


class TestConversationHistoryManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = ch.ConversationHistoryManager(Path(self.tmpdir))

    def test_index(self):
        entry = ch.ConversationEntry(
            summary="Test conversation",
            agents=["dev"],
            topics=["testing"],
            token_count=500,
        )
        result = self.mgr.index(entry)
        self.assertTrue(result)

    def test_search(self):
        self.mgr.index(ch.ConversationEntry(
            summary="architecture review for microservices",
            topics=["architecture"],
        ))
        results = self.mgr.search("architecture")
        self.assertGreater(len(results), 0)

    def test_search_empty(self):
        results = self.mgr.search("nonexistent-keyword")
        self.assertEqual(len(results), 0)

    def test_forget(self):
        self.mgr.index(ch.ConversationEntry(
            summary="secret discussion",
            topics=["secret"],
        ))
        count = self.mgr.forget("secret")
        self.assertGreater(count, 0)

    def test_stats(self):
        self.mgr.index(ch.ConversationEntry(
            summary="t1", agents=["dev"], token_count=100,
        ))
        self.mgr.index(ch.ConversationEntry(
            summary="t2", agents=["qa"], token_count=200,
        ))
        s = self.mgr.stats()
        self.assertEqual(s.total_conversations, 2)
        self.assertEqual(s.total_tokens, 300)

    def test_export(self):
        self.mgr.index(ch.ConversationEntry(summary="export test"))
        data = self.mgr.export()
        self.assertEqual(len(data), 1)
        self.assertIn("summary", data[0])


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_index(self):
        result = ch.mcp_conversation_history(
            self.tmpdir, action="index",
            summary="MCP test conversation",
            agents="dev,qa",
        )
        self.assertTrue(result.get("success"))
        self.assertIn("conversation_id", result)

    def test_mcp_search(self):
        ch.mcp_conversation_history(
            self.tmpdir, action="index",
            summary="architecture review",
        )
        result = ch.mcp_conversation_history(
            self.tmpdir, action="search",
            query="architecture",
        )
        self.assertIn("results", result)

    def test_mcp_search_missing_query(self):
        result = ch.mcp_conversation_history(
            self.tmpdir, action="search",
        )
        self.assertIn("error", result)

    def test_mcp_forget(self):
        ch.mcp_conversation_history(
            self.tmpdir, action="index",
            summary="forget test", agents="dev",
        )
        result = ch.mcp_conversation_history(
            self.tmpdir, action="forget",
            topic="forget",
        )
        self.assertIn("forgotten", result)

    def test_mcp_stats(self):
        result = ch.mcp_conversation_history(
            self.tmpdir, action="stats",
        )
        self.assertIn("total_conversations", result)

    def test_mcp_unknown_action(self):
        result = ch.mcp_conversation_history(
            self.tmpdir, action="unknown",
        )
        self.assertIn("error", result)


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
        self.assertIn("conversation-history", r.stdout)

    def test_index(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "index",
                       "--summary", "CLI test conversation",
                       "--agents", "dev",
                       "--topics", "testing",
                       "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("conversation_id", data)

    def test_search(self):
        tmpdir = tempfile.mkdtemp()
        # Index first
        self._run("--project-root", tmpdir, "index",
                   "--summary", "architecture review",
                   "--topics", "architecture")
        r = self._run("--project-root", tmpdir, "search",
                       "--query", "architecture", "--json")
        self.assertEqual(r.returncode, 0)

    def test_stats(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "stats")
        self.assertEqual(r.returncode, 0)

    def test_export(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "export")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
