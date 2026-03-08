#!/usr/bin/env python3
"""
Tests pour rag-retriever.py — Retrieval sémantique Grimoire (BM-42 Story 2.3).

Fonctions testées :
  - RetrievedChunk (final_score, estimated_tokens)
  - RetrievalResult (context_block, property)
  - AugmentedPrompt (dataclass)
  - PreflightReport (healthy property)
  - Reranker.rerank() (heuristiques)
  - RAGRetriever (init, budget, config loading)
  - file_based_fallback() (keyword search sans Qdrant)
  - load_retriever_config()
  - build_retriever_from_config()
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
TOOL = KIT_DIR / "framework" / "tools" / "rag-retriever.py"


def _import_mod():
    """Import le module rag-retriever via importlib."""
    mod_name = "rag_retriever_test"
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
        self.assertTrue(hasattr(self.mod, "RAG_RETRIEVER_VERSION"))

    def test_version_format(self):
        parts = self.mod.RAG_RETRIEVER_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_default_max_chunks(self):
        self.assertIsInstance(self.mod.DEFAULT_MAX_CHUNKS, int)
        self.assertGreater(self.mod.DEFAULT_MAX_CHUNKS, 0)

    def test_default_min_score(self):
        self.assertIsInstance(self.mod.DEFAULT_MIN_SCORE, float)
        self.assertGreater(self.mod.DEFAULT_MIN_SCORE, 0)
        self.assertLess(self.mod.DEFAULT_MIN_SCORE, 1)

    def test_default_max_context_tokens(self):
        self.assertIsInstance(self.mod.DEFAULT_MAX_CONTEXT_TOKENS, int)
        self.assertGreater(self.mod.DEFAULT_MAX_CONTEXT_TOKENS, 0)

    def test_all_collections(self):
        for coll in ["agents", "memory", "docs", "code"]:
            self.assertIn(coll, self.mod.ALL_COLLECTIONS)

    def test_default_rerank_boost_keys(self):
        expected = ["agent_match", "recent_memory", "heading_match", "decision_boost", "code_penalty"]
        for key in expected:
            self.assertIn(key, self.mod.DEFAULT_RERANK_BOOST)

    def test_chars_per_token(self):
        self.assertEqual(self.mod.CHARS_PER_TOKEN, 4)


# ── Dataclasses ──────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    # -- RetrievedChunk --

    def test_retrieved_chunk_fields(self):
        field_names = {f.name for f in fields(self.mod.RetrievedChunk)}
        for expected in ["text", "source_file", "chunk_type", "heading", "score",
                         "collection", "rerank_score", "estimated_tokens"]:
            self.assertIn(expected, field_names)

    def test_retrieved_chunk_final_score(self):
        chunk = self.mod.RetrievedChunk(
            text="t", source_file="f", chunk_type="h",
            heading="h", score=0.8, collection="agents",
            rerank_score=0.15,
        )
        self.assertAlmostEqual(chunk.final_score, 0.95, places=2)

    def test_retrieved_chunk_final_score_default_rerank(self):
        chunk = self.mod.RetrievedChunk(
            text="t", source_file="f", chunk_type="h",
            heading="h", score=0.7, collection="agents",
        )
        self.assertAlmostEqual(chunk.final_score, 0.7, places=2)

    def test_retrieved_chunk_negative_rerank(self):
        chunk = self.mod.RetrievedChunk(
            text="t", source_file="f", chunk_type="code",
            heading="", score=0.6, collection="code",
            rerank_score=-0.05,
        )
        self.assertAlmostEqual(chunk.final_score, 0.55, places=2)

    # -- RetrievalResult --

    def test_retrieval_result_fields(self):
        field_names = {f.name for f in fields(self.mod.RetrievalResult)}
        for expected in ["query", "agent", "chunks", "total_tokens",
                         "retrieval_time_ms", "qdrant_available", "fallback_used"]:
            self.assertIn(expected, field_names)

    def test_retrieval_result_context_block_empty(self):
        result = self.mod.RetrievalResult(query="test", agent="dev")
        self.assertEqual(result.context_block, "")

    def test_retrieval_result_context_block_with_chunks(self):
        chunk = self.mod.RetrievedChunk(
            text="Content here", source_file="test.md",
            chunk_type="header", heading="My Section",
            score=0.9, collection="docs",
        )
        result = self.mod.RetrievalResult(
            query="test", agent="dev",
            chunks=[chunk], total_tokens=100,
        )
        block = result.context_block
        self.assertIn("RAG Context", block)
        self.assertIn("Content here", block)
        self.assertIn("test.md", block)
        self.assertIn("My Section", block)

    def test_retrieval_result_context_block_multi_chunks(self):
        chunks = [
            self.mod.RetrievedChunk(
                text=f"Content {i}", source_file=f"file{i}.md",
                chunk_type="header", heading=f"Section {i}",
                score=0.9 - i * 0.1, collection="docs",
            )
            for i in range(3)
        ]
        result = self.mod.RetrievalResult(query="q", agent="a", chunks=chunks)
        block = result.context_block
        self.assertIn("[1]", block)
        self.assertIn("[2]", block)
        self.assertIn("[3]", block)

    def test_retrieval_result_defaults(self):
        result = self.mod.RetrievalResult(query="q", agent="a")
        self.assertEqual(result.chunks, [])
        self.assertTrue(result.qdrant_available)
        self.assertFalse(result.fallback_used)

    # -- AugmentedPrompt --

    def test_augmented_prompt_fields(self):
        field_names = {f.name for f in fields(self.mod.AugmentedPrompt)}
        for expected in ["original_prompt", "rag_context", "augmented_prompt",
                         "retrieval", "budget_tokens_used", "budget_pct"]:
            self.assertIn(expected, field_names)

    # -- PreflightReport --

    def test_preflight_healthy_all_good(self):
        report = self.mod.PreflightReport(
            qdrant_reachable=True,
            embedding_available=True,
            total_indexed_chunks=100,
        )
        self.assertTrue(report.healthy)

    def test_preflight_unhealthy_no_qdrant(self):
        report = self.mod.PreflightReport(
            qdrant_reachable=False,
            embedding_available=True,
        )
        self.assertFalse(report.healthy)

    def test_preflight_unhealthy_no_embedding(self):
        report = self.mod.PreflightReport(
            qdrant_reachable=True,
            embedding_available=False,
        )
        self.assertFalse(report.healthy)

    def test_preflight_unhealthy_with_errors(self):
        report = self.mod.PreflightReport(
            qdrant_reachable=True,
            embedding_available=True,
            errors=["some error"],
        )
        self.assertFalse(report.healthy)

    def test_preflight_empty_defaults(self):
        report = self.mod.PreflightReport()
        self.assertFalse(report.healthy)
        self.assertFalse(report.qdrant_reachable)
        self.assertFalse(report.embedding_available)
        self.assertEqual(report.total_indexed_chunks, 0)

    # -- asdict round-trip --

    def test_asdict_retrieval_result(self):
        result = self.mod.RetrievalResult(query="q", agent="a")
        d = asdict(result)
        self.assertEqual(d["query"], "q")
        self.assertIsInstance(d["chunks"], list)

    def test_asdict_preflight(self):
        report = self.mod.PreflightReport(qdrant_reachable=True)
        d = asdict(report)
        self.assertTrue(d["qdrant_reachable"])


# ── Reranker ─────────────────────────────────────────────────────────────────

class TestReranker(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.reranker = self.mod.Reranker()

    def _make_chunk(self, **kwargs):
        defaults = {
            "text": "test text", "source_file": "test.md",
            "chunk_type": "header", "heading": "Test",
            "score": 0.8, "collection": "docs",
        }
        defaults.update(kwargs)
        return self.mod.RetrievedChunk(**defaults)

    def test_agent_match_boost(self):
        chunks = [self._make_chunk(source_file="architect.md", score=0.5)]
        self.reranker.rerank(chunks, "system design", "architect")
        self.assertGreater(chunks[0].rerank_score, 0)

    def test_no_agent_no_boost(self):
        chunks = [self._make_chunk(heading="Something")]
        self.reranker.rerank(chunks, "unrelated", "")
        self.assertEqual(chunks[0].rerank_score, 0.0)

    def test_memory_collection_boost(self):
        chunks = [self._make_chunk(collection="memory", score=0.5)]
        self.reranker.rerank(chunks, "test", "")
        self.assertGreater(chunks[0].rerank_score, 0)

    def test_heading_match_boost(self):
        chunks = [self._make_chunk(heading="Authentication System Design")]
        self.reranker.rerank(chunks, "authentication design", "")
        self.assertGreater(chunks[0].rerank_score, 0)

    def test_decision_boost(self):
        chunks = [self._make_chunk(source_file="decisions-log.md")]
        self.reranker.rerank(chunks, "decision", "")
        self.assertGreater(chunks[0].rerank_score, 0)

    def test_code_penalty(self):
        chunks = [self._make_chunk(collection="code", heading="Something")]
        self.reranker.rerank(chunks, "unrelated", "")
        self.assertLess(chunks[0].rerank_score, 0)

    def test_rerank_sorts_by_final_score(self):
        chunks = [
            self._make_chunk(score=0.5, collection="code"),      # low + penalty
            self._make_chunk(score=0.6, collection="memory"),    # medium + boost
            self._make_chunk(score=0.9, collection="docs"),      # high
        ]
        self.reranker.rerank(chunks, "test", "")
        final_scores = [c.final_score for c in chunks]
        self.assertEqual(final_scores, sorted(final_scores, reverse=True))

    def test_combined_boosts(self):
        """Agent match + memory + heading match should stack."""
        chunks = [self._make_chunk(
            source_file="dev-learnings.md",
            collection="memory",
            heading="dev patterns",
        )]
        self.reranker.rerank(chunks, "dev patterns", "dev")
        self.assertGreater(chunks[0].rerank_score, 0.25)

    def test_custom_boosts(self):
        reranker = self.mod.Reranker(boosts={"agent_match": 0.5, "code_penalty": -0.2})
        chunks = [self._make_chunk(source_file="dev.md", collection="code")]
        reranker.rerank(chunks, "test", "dev")
        # Agent match +0.5 and code penalty -0.2 = +0.3
        self.assertAlmostEqual(chunks[0].rerank_score, 0.3, places=1)

    def test_empty_chunks_list(self):
        result = self.reranker.rerank([], "query", "agent")
        self.assertEqual(result, [])


# ── File-Based Fallback ──────────────────────────────────────────────────────

class TestFileBasedFallback(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Create mock memory dir structure
        memory_dir = self.tmpdir / "_grimoire" / "_memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "decisions-log.md").write_text(
            "# Decisions Log\n\n"
            "## 2024-01-15 — Use Qdrant for vector storage\n\n"
            "We decided to use Qdrant as the primary vector database "
            "because it supports local mode and has a simple API.\n\n"
            "## 2024-01-20 — Authentication via JWT\n\n"
            "Authentication tokens will use JWT format with RS256 signing.\n",
            encoding="utf-8",
        )
        docs_dir = self.tmpdir / "docs"
        docs_dir.mkdir()
        (docs_dir / "architecture.md").write_text(
            "# Architecture\n\n"
            "## Overview\n\n"
            "The system uses a microservices architecture with "
            "event-driven communication between services.\n\n"
            "## Database Layer\n\n"
            "PostgreSQL for relational data, Qdrant for vector embeddings, "
            "Redis for caching and session storage.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_fallback_returns_result(self):
        result = self.mod.file_based_fallback(self.tmpdir, "Qdrant vector database")
        self.assertIsInstance(result, self.mod.RetrievalResult)
        self.assertFalse(result.qdrant_available)
        self.assertTrue(result.fallback_used)

    def test_fallback_finds_relevant_chunks(self):
        result = self.mod.file_based_fallback(self.tmpdir, "Qdrant vector database")
        self.assertGreater(len(result.chunks), 0)

    def test_fallback_keywords_match(self):
        result = self.mod.file_based_fallback(self.tmpdir, "JWT authentication tokens")
        found_texts = " ".join(c.text for c in result.chunks).lower()
        self.assertIn("jwt", found_texts)

    def test_fallback_respects_max_chunks(self):
        result = self.mod.file_based_fallback(self.tmpdir, "system", max_chunks=1)
        self.assertLessEqual(len(result.chunks), 1)

    def test_fallback_empty_query(self):
        result = self.mod.file_based_fallback(self.tmpdir, "")
        self.assertEqual(len(result.chunks), 0)

    def test_fallback_no_match(self):
        result = self.mod.file_based_fallback(self.tmpdir, "xyznonexistentword12345")
        self.assertEqual(len(result.chunks), 0)

    def test_fallback_collection_is_fallback(self):
        result = self.mod.file_based_fallback(self.tmpdir, "Qdrant")
        for chunk in result.chunks:
            self.assertEqual(chunk.collection, "fallback")

    def test_fallback_has_source_file(self):
        result = self.mod.file_based_fallback(self.tmpdir, "Qdrant")
        for chunk in result.chunks:
            self.assertTrue(chunk.source_file)

    def test_fallback_computes_tokens(self):
        result = self.mod.file_based_fallback(self.tmpdir, "Qdrant vector")
        for chunk in result.chunks:
            self.assertGreater(chunk.estimated_tokens, 0)

    def test_fallback_nonexistent_root(self):
        result = self.mod.file_based_fallback(Path("/tmp/nonexistent_xyz"), "test")
        self.assertEqual(len(result.chunks), 0)


# ── RAGRetriever Init ────────────────────────────────────────────────────────

class TestRAGRetrieverInit(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_init_defaults(self):
        r = self.mod.RAGRetriever(project_root=self.tmpdir)
        self.assertEqual(r.max_chunks, self.mod.DEFAULT_MAX_CHUNKS)
        self.assertEqual(r.min_score, self.mod.DEFAULT_MIN_SCORE)
        self.assertEqual(r.max_context_tokens, self.mod.DEFAULT_MAX_CONTEXT_TOKENS)

    def test_init_custom_params(self):
        r = self.mod.RAGRetriever(
            project_root=self.tmpdir,
            max_chunks=10,
            min_score=0.5,
            max_context_tokens=8192,
            project_name="myproject",
        )
        self.assertEqual(r.max_chunks, 10)
        self.assertEqual(r.min_score, 0.5)
        self.assertEqual(r.max_context_tokens, 8192)
        self.assertEqual(r.project_name, "myproject")

    def test_collection_name(self):
        r = self.mod.RAGRetriever(project_root=self.tmpdir, project_name="test")
        self.assertEqual(r._collection_name("agents"), "test-agents")

    def test_qdrant_init_no_server(self):
        r = self.mod.RAGRetriever(project_root=self.tmpdir)
        # Should return False when Qdrant not available/no path exists
        result = r._init_qdrant()
        # Depends on qdrant_client availability, just check it doesn't crash
        self.assertIsInstance(result, bool)

    def test_retrieve_without_qdrant(self):
        """Retrieve should degrade gracefully without Qdrant."""
        r = self.mod.RAGRetriever(project_root=self.tmpdir)
        result = r.retrieve("test query", "dev")
        self.assertIsInstance(result, self.mod.RetrievalResult)
        # Either fallback or qdrant not available
        self.assertTrue(result.fallback_used or not result.qdrant_available)

    def test_augment_without_qdrant(self):
        """Augment should return original prompt if no Qdrant."""
        r = self.mod.RAGRetriever(project_root=self.tmpdir)
        aug = r.augment_prompt("Hello world", "dev")
        self.assertIsInstance(aug, self.mod.AugmentedPrompt)
        self.assertIn("Hello world", aug.augmented_prompt)

    def test_preflight_without_qdrant(self):
        """Preflight should report issues without crashing."""
        r = self.mod.RAGRetriever(project_root=self.tmpdir)
        report = r.preflight()
        self.assertIsInstance(report, self.mod.PreflightReport)


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_config_returns_empty(self):
        config = self.mod.load_retriever_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_config_from_project_context(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")

        config_data = {
            "rag": {
                "max_chunks": 10,
                "min_score": 0.5,
                "embedding_model": "nomic-embed-text",
            }
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config_data), encoding="utf-8",
        )
        config = self.mod.load_retriever_config(self.tmpdir)
        self.assertEqual(config["max_chunks"], 10)
        self.assertEqual(config["min_score"], 0.5)

    def test_build_retriever_from_config_defaults(self):
        r = self.mod.build_retriever_from_config(self.tmpdir)
        self.assertIsInstance(r, self.mod.RAGRetriever)
        self.assertEqual(r.max_chunks, self.mod.DEFAULT_MAX_CHUNKS)

    def test_build_retriever_from_config_custom(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")

        config_data = {
            "rag": {
                "max_chunks": 8,
                "min_score": 0.4,
                "max_context_tokens": 8192,
                "collection_prefix": "myproject",
            }
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config_data), encoding="utf-8",
        )
        r = self.mod.build_retriever_from_config(self.tmpdir)
        self.assertEqual(r.max_chunks, 8)
        self.assertEqual(r.min_score, 0.4)
        self.assertEqual(r.max_context_tokens, 8192)
        self.assertEqual(r.project_name, "myproject")


# ── CLI Integration ──────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("retrieve", result.stdout)

    def test_version_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("rag-retriever", result.stdout)

    def test_no_command_shows_help(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        # Should exit non-zero (no command)
        self.assertNotEqual(result.returncode, 0)

    def test_retrieve_fallback_mode(self):
        """Test CLI retrieve with --fallback and no Qdrant."""
        result = subprocess.run(
            [sys.executable, str(TOOL),
             "--project-root", str(KIT_DIR),
             "retrieve", "--query", "memory system", "--fallback", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("query", data)
        self.assertFalse(data["qdrant_available"])
        self.assertTrue(data["fallback_used"])

    def test_retrieve_fallback_text(self):
        result = subprocess.run(
            [sys.executable, str(TOOL),
             "--project-root", str(KIT_DIR),
             "retrieve", "--query", "workflow design", "--fallback"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("RAG Retrieval", result.stdout)


if __name__ == "__main__":
    unittest.main()
