"""Tests for rag-auto-inject.py — D11 RAG Auto-Injection."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rag-auto-inject.py"


def _load():
    mod_name = "rag_auto_inject_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


rai = _load()


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertTrue(rai.RAG_AUTO_INJECT_VERSION)


class TestKeywordExtraction(unittest.TestCase):
    def test_basic(self):
        kw = rai._extract_keywords("Comment créer un agent BMAD ?")
        self.assertIn("créer", kw)
        self.assertIn("agent", kw)
        self.assertIn("bmad", kw)
        self.assertNotIn("comment", kw)  # stop word

    def test_english_stop_words(self):
        kw = rai._extract_keywords("How to create an agent for the project")
        self.assertNotIn("how", kw)
        self.assertNotIn("the", kw)
        self.assertIn("create", kw)

    def test_empty_query(self):
        kw = rai._extract_keywords("")
        self.assertEqual(kw, [])

    def test_short_words_filtered(self):
        kw = rai._extract_keywords("A is ok but not enough")
        self.assertNotIn("is", kw)  # too short (2 chars)


class TestChunking(unittest.TestCase):
    def test_chunk_markdown(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Title\n\nContent here.\n\n## Section\n\nMore content.\n")
            f.flush()
            chunks = rai._chunk_file(Path(f.name))

        self.assertTrue(len(chunks) > 0)
        Path(f.name).unlink()

    def test_chunk_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            f.flush()
            chunks = rai._chunk_file(Path(f.name))

        self.assertEqual(chunks, [])
        Path(f.name).unlink()

    def test_nonexistent_file(self):
        chunks = rai._chunk_file(Path("/nonexistent/file.md"))
        self.assertEqual(chunks, [])


class TestScoring(unittest.TestCase):
    def test_full_match(self):
        chunk = {"text": "This text mentions agent and workflow", "source": "test.md"}
        score = rai._score_chunk(chunk, ["agent", "workflow"])
        self.assertEqual(score, 1.0)

    def test_partial_match(self):
        chunk = {"text": "This text mentions agent only", "source": "test.md"}
        score = rai._score_chunk(chunk, ["agent", "workflow"])
        self.assertEqual(score, 0.5)

    def test_no_match(self):
        chunk = {"text": "Nothing relevant here", "source": "test.md"}
        score = rai._score_chunk(chunk, ["agent", "workflow"])
        self.assertEqual(score, 0.0)

    def test_empty_keywords(self):
        chunk = {"text": "Any text", "source": "test.md"}
        score = rai._score_chunk(chunk, [])
        self.assertEqual(score, 0.0)


class TestFileBasedInject(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        docs = Path(self.tmpdir) / "docs"
        docs.mkdir()
        (docs / "agents.md").write_text(
            "# Agents\n\nLes agents BMAD sont des personas spécialisées.\n\n"
            "## Agent Architecture\n\nChaque agent a un rôle unique et des compétences.\n"
        )
        (docs / "workflows.md").write_text(
            "# Workflows\n\nLes workflows orchestrent les tâches.\n\n"
            "## Pipeline\n\nUn pipeline est une séquence de tâches.\n"
        )

    def test_finds_matching_chunks(self):
        results = rai.file_based_inject(Path(self.tmpdir), "agent persona", max_chunks=3)
        self.assertTrue(len(results) > 0)
        self.assertGreater(results[0]["score"], 0)

    def test_no_match(self):
        results = rai.file_based_inject(Path(self.tmpdir), "xyzzy foobar", max_chunks=3)
        self.assertEqual(results, [])

    def test_respects_max_chunks(self):
        results = rai.file_based_inject(Path(self.tmpdir), "agent", max_chunks=1)
        self.assertLessEqual(len(results), 1)


class TestAutoInject(unittest.TestCase):
    def test_returns_expected_keys(self):
        tmpdir = tempfile.mkdtemp()
        result = rai.auto_inject(Path(tmpdir), "test query")
        self.assertIn("context", result)
        self.assertIn("chunks", result)
        self.assertIn("count", result)
        self.assertIn("backend", result)
        self.assertEqual(result["backend"], "file")


class TestMcpInterface(unittest.TestCase):
    def test_mcp_returns_dict(self):
        tmpdir = tempfile.mkdtemp()
        result = rai.mcp_rag_auto_inject("test query", project_root=tmpdir)
        self.assertIn("context", result)
        self.assertIn("count", result)

    def test_max_chunks_clamped(self):
        tmpdir = tempfile.mkdtemp()
        result = rai.mcp_rag_auto_inject("test", max_chunks=100, project_root=tmpdir)
        # Should not crash
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
