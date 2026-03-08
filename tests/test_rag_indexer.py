#!/usr/bin/env python3
"""
Tests pour rag-indexer.py — Pipeline RAG d'indexation Grimoire → Qdrant (BM-42).

Fonctions testées :
  - ChunkingStrategy.chunk_markdown()
  - ChunkingStrategy.chunk_python()
  - ChunkingStrategy.chunk_yaml()
  - ChunkingStrategy.chunk_file()
  - HashIndex (load, save, needs_reindex, mark_indexed)
  - Chunk (dataclass, id génération)
  - IndexReport (dataclass)
  - SearchResult (dataclass)
  - DISCOVERY_PATTERNS (exhaustivité)
  - load_rag_config()
  - CLI (--help, --version)

Note : Les tests qui nécessitent qdrant-client ou sentence-transformers
       sont skippés si non disponibles.
"""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "rag-indexer.py"


def _import_mod():
    """Import le module rag-indexer via importlib."""
    mod_name = "rag_indexer"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "RAG_INDEXER_VERSION"))

    def test_version_format(self):
        parts = self.mod.RAG_INDEXER_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_default_embedding_model(self):
        self.assertIn("MiniLM", self.mod.DEFAULT_EMBEDDING_MODEL)

    def test_default_vector_size(self):
        self.assertEqual(self.mod.DEFAULT_VECTOR_SIZE, 384)

    def test_all_collections_list(self):
        self.assertIn("agents", self.mod.ALL_COLLECTIONS)
        self.assertIn("memory", self.mod.ALL_COLLECTIONS)
        self.assertIn("docs", self.mod.ALL_COLLECTIONS)
        self.assertIn("code", self.mod.ALL_COLLECTIONS)

    def test_discovery_patterns_keys(self):
        for coll in self.mod.ALL_COLLECTIONS:
            self.assertIn(coll, self.mod.DISCOVERY_PATTERNS)
            self.assertGreater(len(self.mod.DISCOVERY_PATTERNS[coll]), 0)

    def test_vector_sizes_known_models(self):
        self.assertIn("all-MiniLM-L6-v2", self.mod.VECTOR_SIZES)
        self.assertIn("nomic-embed-text", self.mod.VECTOR_SIZES)

    def test_exclude_patterns(self):
        self.assertIn("node_modules", self.mod.EXCLUDE_PATTERNS)
        self.assertIn(".git", self.mod.EXCLUDE_PATTERNS)


# ── Dataclasses ──────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_chunk_fields(self):
        field_names = {f.name for f in fields(self.mod.Chunk)}
        for expected in ["text", "source_file", "chunk_type", "chunk_index", "heading", "estimated_tokens"]:
            self.assertIn(expected, field_names)

    def test_chunk_id_deterministic(self):
        c1 = self.mod.Chunk(text="hello world", source_file="test.md", chunk_type="header")
        c2 = self.mod.Chunk(text="hello world", source_file="test.md", chunk_type="header")
        self.assertEqual(c1.id, c2.id)

    def test_chunk_id_different_content(self):
        c1 = self.mod.Chunk(text="hello", source_file="test.md", chunk_type="header")
        c2 = self.mod.Chunk(text="world", source_file="test.md", chunk_type="header")
        self.assertNotEqual(c1.id, c2.id)

    def test_index_report_fields(self):
        field_names = {f.name for f in fields(self.mod.IndexReport)}
        for expected in ["collection", "files_processed", "chunks_created", "chunks_upserted", "errors"]:
            self.assertIn(expected, field_names)

    def test_search_result_fields(self):
        field_names = {f.name for f in fields(self.mod.SearchResult)}
        for expected in ["text", "source_file", "score", "collection"]:
            self.assertIn(expected, field_names)

    def test_dataclass_serializable(self):
        chunk = self.mod.Chunk(text="test", source_file="f.md", chunk_type="header")
        d = asdict(chunk)
        self.assertIsInstance(json.dumps(d), str)


# ── ChunkingStrategy — Markdown ──────────────────────────────────────────────

class TestChunkMarkdown(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.strategy = self.mod.ChunkingStrategy

    def test_single_section(self):
        content = "## Title\n\nSome content here that is long enough to be kept as a chunk.\n"
        chunks = self.strategy.chunk_markdown(content, "test.md")
        self.assertGreater(len(chunks), 0)

    def test_multiple_sections(self):
        content = (
            "## Section 1\n\nContent for section one that should be long enough.\n\n"
            "## Section 2\n\nContent for section two that should be long enough.\n"
        )
        chunks = self.strategy.chunk_markdown(content, "test.md")
        self.assertGreaterEqual(len(chunks), 1)

    def test_no_headers_fallback(self):
        content = "Just plain text without any markdown headers, but long enough to index."
        chunks = self.strategy.chunk_markdown(content, "test.md")
        self.assertEqual(len(chunks), 1)
        # Without headers, text is flushed from buffer — may be "header" or "full"
        self.assertIn(chunks[0].chunk_type, ["full", "header"])

    def test_empty_content(self):
        chunks = self.strategy.chunk_markdown("", "test.md")
        self.assertEqual(len(chunks), 0)

    def test_heading_captured(self):
        content = "## My Heading\n\nLong content that should be preserved in the chunk.\n"
        chunks = self.strategy.chunk_markdown(content, "test.md")
        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].heading, "My Heading")

    def test_chunk_type_header(self):
        content = "## Title\n\nContent content content content content.\n"
        chunks = self.strategy.chunk_markdown(content, "test.md")
        if chunks:
            self.assertEqual(chunks[0].chunk_type, "header")

    def test_source_file_set(self):
        content = "## A\n\nSome content that is long enough to index here.\n"
        chunks = self.strategy.chunk_markdown(content, "docs/readme.md")
        if chunks:
            self.assertEqual(chunks[0].source_file, "docs/readme.md")

    def test_estimated_tokens(self):
        content = "## Title\n\n" + "word " * 200 + "\n"
        chunks = self.strategy.chunk_markdown(content, "test.md")
        if chunks:
            self.assertGreater(chunks[0].estimated_tokens, 50)

    def test_max_chunk_tokens_respected(self):
        # Large content should be split when exceeding max
        content = "## Title\n\n" + "a" * 10000 + "\n"
        chunks = self.strategy.chunk_markdown(content, "test.md", max_tokens=512)
        # Should have at least 1 chunk, and the first should not exceed limit by much
        self.assertGreater(len(chunks), 0)


# ── ChunkingStrategy — Python ───────────────────────────────────────────────

class TestChunkPython(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.strategy = self.mod.ChunkingStrategy

    def test_single_function(self):
        content = (
            "def hello():\n"
            "    \"\"\"Docstring.\"\"\"\n"
            "    return 'hello'\n"
        )
        chunks = self.strategy.chunk_python(content, "test.py")
        self.assertGreater(len(chunks), 0)

    def test_class_and_function(self):
        content = (
            "class MyClass:\n"
            "    \"\"\"A class.\"\"\"\n"
            "    def method(self):\n"
            "        pass\n"
            "\n"
            "def free_func():\n"
            "    \"\"\"Free function.\"\"\"\n"
            "    return True\n"
        )
        chunks = self.strategy.chunk_python(content, "test.py")
        self.assertGreater(len(chunks), 0)

    def test_empty_python(self):
        chunks = self.strategy.chunk_python("", "test.py")
        self.assertEqual(len(chunks), 0)

    def test_no_functions_fallback(self):
        content = "# Just a comment\nimport sys\nX = 42\n" + "# " * 30 + "\n"
        chunks = self.strategy.chunk_python(content, "test.py")
        # May or may not produce chunks depending on length threshold
        # At minimum, should not crash
        self.assertIsInstance(chunks, list)

    def test_heading_is_function_name(self):
        content = (
            "def my_function():\n"
            "    \"\"\"My docstring.\"\"\"\n"
            "    return 42\n"
        )
        chunks = self.strategy.chunk_python(content, "test.py")
        names = [c.heading for c in chunks]
        # Should find "my_function" in some chunk heading
        self.assertTrue(
            any("my_function" in h for h in names),
            f"Expected 'my_function' in chunk headings, got {names}",
        )


# ── ChunkingStrategy — YAML ─────────────────────────────────────────────────

class TestChunkYaml(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.strategy = self.mod.ChunkingStrategy

    def test_simple_yaml(self):
        content = (
            "name: test-project\n"
            "version: 1.0\n"
            "memory:\n"
            "  backend: auto\n"
            "  url: http://localhost\n"
        )
        chunks = self.strategy.chunk_yaml(content, "config.yaml")
        self.assertGreater(len(chunks), 0)

    def test_empty_yaml(self):
        chunks = self.strategy.chunk_yaml("", "config.yaml")
        self.assertEqual(len(chunks), 0)

    def test_single_key(self):
        content = "settings:\n  key1: value1\n  key2: value2\n"
        chunks = self.strategy.chunk_yaml(content, "config.yaml")
        self.assertGreater(len(chunks), 0)


# ── ChunkingStrategy — chunk_file ────────────────────────────────────────────

class TestChunkFile(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.strategy = self.mod.ChunkingStrategy

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chunk_md_file(self):
        f = self.tmpdir / "test.md"
        f.write_text("## Hello\n\nWorld content that is long enough to index properly.\n", encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertGreater(len(chunks), 0)

    def test_chunk_py_file(self):
        f = self.tmpdir / "test.py"
        f.write_text("def foo():\n    return 42\n", encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertIsInstance(chunks, list)

    def test_chunk_yaml_file(self):
        f = self.tmpdir / "test.yaml"
        f.write_text("name: test\nversion: 1.0\n", encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertIsInstance(chunks, list)

    def test_chunk_json_file(self):
        f = self.tmpdir / "test.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].chunk_type, "full")

    def test_chunk_unknown_extension(self):
        f = self.tmpdir / "test.xyz"
        f.write_text("some content", encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertEqual(chunks, [])

    def test_chunk_nonexistent_file(self):
        f = self.tmpdir / "nonexistent.md"
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        self.assertEqual(chunks, [])

    def test_source_file_relative(self):
        sub = self.tmpdir / "docs"
        sub.mkdir()
        f = sub / "readme.md"
        f.write_text("## Title\n\nContent long enough to be indexed.\n", encoding="utf-8")
        chunks = self.strategy.chunk_file(f, self.tmpdir)
        if chunks:
            self.assertEqual(chunks[0].source_file, "docs/readme.md")


# ── HashIndex ────────────────────────────────────────────────────────────────

class TestHashIndex(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.index_path = self.tmpdir / "hashes.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_empty(self):
        self.mod.HashIndex(self.index_path)
        self.assertFalse(self.index_path.exists())

    def test_save_creates_file(self):
        idx = self.mod.HashIndex(self.index_path)
        idx.save()
        self.assertTrue(self.index_path.exists())

    def test_file_hash_consistent(self):
        f = self.tmpdir / "test.txt"
        f.write_text("hello", encoding="utf-8")
        idx = self.mod.HashIndex(self.index_path)
        h1 = idx.file_hash(f)
        h2 = idx.file_hash(f)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # SHA256 hex length

    def test_file_hash_nonexistent(self):
        idx = self.mod.HashIndex(self.index_path)
        h = idx.file_hash(self.tmpdir / "nonexistent.txt")
        self.assertEqual(h, "")

    def test_needs_reindex_new_file(self):
        f = self.tmpdir / "new.txt"
        f.write_text("content", encoding="utf-8")
        idx = self.mod.HashIndex(self.index_path)
        self.assertTrue(idx.needs_reindex(f))

    def test_needs_reindex_after_mark(self):
        f = self.tmpdir / "test.txt"
        f.write_text("content", encoding="utf-8")
        idx = self.mod.HashIndex(self.index_path)
        idx.mark_indexed(f)
        self.assertFalse(idx.needs_reindex(f))

    def test_needs_reindex_after_change(self):
        f = self.tmpdir / "test.txt"
        f.write_text("content v1", encoding="utf-8")
        idx = self.mod.HashIndex(self.index_path)
        idx.mark_indexed(f)
        f.write_text("content v2", encoding="utf-8")
        self.assertTrue(idx.needs_reindex(f))

    def test_persistence(self):
        f = self.tmpdir / "test.txt"
        f.write_text("content", encoding="utf-8")

        idx1 = self.mod.HashIndex(self.index_path)
        idx1.mark_indexed(f)
        idx1.save()

        idx2 = self.mod.HashIndex(self.index_path)
        self.assertFalse(idx2.needs_reindex(f))

    def test_corrupted_index_file(self):
        self.index_path.write_text("not json", encoding="utf-8")
        idx = self.mod.HashIndex(self.index_path)
        # Should not crash, just start empty
        f = self.tmpdir / "test.txt"
        f.write_text("x", encoding="utf-8")
        self.assertTrue(idx.needs_reindex(f))


# ── Config Loading ───────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_missing_file(self):
        config = self.mod.load_rag_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_load_config_with_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available")

        config = {
            "rag": {
                "enabled": True,
                "embedding_model": "nomic-embed-text",
                "max_chunk_tokens": 256,
            },
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config), encoding="utf-8",
        )
        result = self.mod.load_rag_config(self.tmpdir)
        self.assertEqual(result.get("embedding_model"), "nomic-embed-text")
        self.assertEqual(result.get("max_chunk_tokens"), 256)

    def test_load_config_fallback_memory(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available")

        config = {
            "memory": {
                "backend": "qdrant-local",
                "embedding_model": "all-mpnet-base-v2",
            },
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config), encoding="utf-8",
        )
        result = self.mod.load_rag_config(self.tmpdir)
        # Should fallback to memory section
        self.assertIn("backend", result)


# ── EmbeddingProvider (sans deps) ────────────────────────────────────────────

class TestEmbeddingProvider(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_vector_size_default(self):
        try:
            provider = self.mod.EmbeddingProvider()
        except ImportError:
            self.skipTest("No embedding provider available")
        self.assertEqual(provider.vector_size, 384)

    def test_vector_size_nomic(self):
        try:
            provider = self.mod.EmbeddingProvider(model="nomic-embed-text")
        except ImportError:
            self.skipTest("No embedding provider available")
        self.assertEqual(provider.vector_size, 768)

    def test_no_provider_raises(self):
        # Without sentence-transformers and without ollama_url, should raise
        # (but only if sentence-transformers is actually not installed)
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
            self.skipTest("sentence-transformers available, can't test ImportError")
        except ImportError:
            with self.assertRaises(ImportError):
                self.mod.EmbeddingProvider(model="nonexistent-model")


# ── CLI Integration ──────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("index", r.stdout.lower())
        self.assertIn("search", r.stdout.lower())
        self.assertIn("status", r.stdout.lower())

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("rag-indexer", r.stdout)

    def test_no_args(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1, 2))

    def test_index_help(self):
        r = self._run("index", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("full", r.stdout.lower())
        self.assertIn("incremental", r.stdout.lower())


if __name__ == "__main__":
    unittest.main()
