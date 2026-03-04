#!/usr/bin/env python3
"""
Tests pour semantic-cache.py — Cache sémantique Qdrant BMAD (BM-41 Story 3.2).

Fonctions testées :
  - CacheEntry (dataclass, is_expired, age_hours)
  - CacheStatsManager (record_hit, record_miss, hit_rate)
  - SemanticCache (query, store, invalidate, clear, get_stats)
  - build_cache_from_config()
  - main()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "semantic-cache.py"


def _import_mod():
    mod_name = "semantic_cache"
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
        self.assertTrue(hasattr(self.mod, "SEMANTIC_CACHE_VERSION"))

    def test_version_format(self):
        parts = self.mod.SEMANTIC_CACHE_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_chars_per_token(self):
        self.assertEqual(self.mod.CHARS_PER_TOKEN, 4)

    def test_default_similarity_threshold(self):
        self.assertAlmostEqual(self.mod.DEFAULT_SIMILARITY_THRESHOLD, 0.90)

    def test_default_ttls_not_empty(self):
        self.assertGreater(len(self.mod.DEFAULT_TTLS), 3)

    def test_ttl_code_review_1h(self):
        self.assertEqual(self.mod.DEFAULT_TTLS["code-review"], 3600)

    def test_ttl_architecture_24h(self):
        self.assertEqual(self.mod.DEFAULT_TTLS["architecture"], 24 * 3600)

    def test_ttl_formatting_7d(self):
        self.assertEqual(self.mod.DEFAULT_TTLS["formatting"], 7 * 86400)

    def test_cache_collection_name(self):
        self.assertEqual(self.mod.CACHE_COLLECTION, "cache")


# ── Data Classes ────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_cache_entry_creation(self):
        entry = self.mod.CacheEntry(
            prompt_hash="abc123",
            prompt_summary="Test prompt",
            response="Test response",
            query_type="default",
            agent="dev",
            created_at=time.time(),
            ttl_seconds=3600,
        )
        self.assertEqual(entry.hit_count, 0)
        self.assertEqual(entry.tokens_saved, 0)

    def test_cache_entry_not_expired(self):
        entry = self.mod.CacheEntry(
            prompt_hash="abc", prompt_summary="s", response="r",
            query_type="default", agent="dev",
            created_at=time.time(), ttl_seconds=3600,
        )
        self.assertFalse(entry.is_expired)

    def test_cache_entry_expired(self):
        entry = self.mod.CacheEntry(
            prompt_hash="abc", prompt_summary="s", response="r",
            query_type="default", agent="dev",
            created_at=time.time() - 7200, ttl_seconds=3600,
        )
        self.assertTrue(entry.is_expired)

    def test_cache_entry_age_hours(self):
        entry = self.mod.CacheEntry(
            prompt_hash="abc", prompt_summary="s", response="r",
            query_type="default", agent="dev",
            created_at=time.time() - 3600, ttl_seconds=7200,
        )
        self.assertAlmostEqual(entry.age_hours, 1.0, places=0)

    def test_cache_result_defaults(self):
        r = self.mod.CacheResult()
        self.assertFalse(r.hit)
        self.assertEqual(r.response, "")
        self.assertEqual(r.similarity, 0.0)

    def test_cache_stats_defaults(self):
        s = self.mod.CacheStats()
        self.assertEqual(s.total_entries, 0)
        self.assertEqual(s.hit_rate, 0.0)
        self.assertFalse(s.qdrant_available)


# ── CacheStatsManager ─────────────────────────────────────────────────────

class TestCacheStatsManager(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initial_state(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        self.assertEqual(mgr.hits, 0)
        self.assertEqual(mgr.misses, 0)

    def test_record_hit(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        mgr.record_hit(100)
        self.assertEqual(mgr.hits, 1)
        self.assertEqual(mgr.tokens_saved, 100)

    def test_record_miss(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        mgr.record_miss()
        self.assertEqual(mgr.misses, 1)

    def test_record_store(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        mgr.record_store()
        # Should not crash

    def test_hit_rate_calculation(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        mgr.record_hit()
        mgr.record_hit()
        mgr.record_miss()
        self.assertAlmostEqual(mgr.hit_rate, 0.667, places=2)

    def test_hit_rate_zero_total(self):
        mgr = self.mod.CacheStatsManager(self.tmpdir / "stats.json")
        self.assertEqual(mgr.hit_rate, 0.0)

    def test_persistence(self):
        stats_file = self.tmpdir / "stats.json"
        mgr1 = self.mod.CacheStatsManager(stats_file)
        mgr1.record_hit(50)
        mgr1.record_miss()

        # Reload
        mgr2 = self.mod.CacheStatsManager(stats_file)
        self.assertEqual(mgr2.hits, 1)
        self.assertEqual(mgr2.misses, 1)
        self.assertEqual(mgr2.tokens_saved, 50)

    def test_corrupted_file(self):
        stats_file = self.tmpdir / "stats.json"
        stats_file.write_text("not json")
        mgr = self.mod.CacheStatsManager(stats_file)
        self.assertEqual(mgr.hits, 0)


# ── SemanticCache ──────────────────────────────────────────────────────────

class TestSemanticCache(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_bmad-output").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_collection_name(self):
        cache = self.mod.SemanticCache(self.tmpdir, project_name="test")
        self.assertEqual(cache.collection_name, "test-cache")

    def test_default_threshold(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        self.assertAlmostEqual(cache.threshold, 0.90)

    def test_custom_threshold(self):
        cache = self.mod.SemanticCache(self.tmpdir, similarity_threshold=0.85)
        self.assertAlmostEqual(cache.threshold, 0.85)

    def test_custom_ttls(self):
        custom = {"test": 999}
        cache = self.mod.SemanticCache(self.tmpdir, ttls=custom)
        self.assertEqual(cache.ttls["test"], 999)

    def test_get_ttl_known_type(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        ttl = cache._get_ttl("code-review")
        self.assertEqual(ttl, 3600)

    def test_get_ttl_unknown_type(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        ttl = cache._get_ttl("unknown-type")
        self.assertEqual(ttl, cache.ttls.get("default", 4 * 3600))

    def test_query_without_qdrant(self):
        """Query returns miss when Qdrant is unavailable."""
        cache = self.mod.SemanticCache(self.tmpdir)
        result = cache.query("test prompt")
        self.assertFalse(result.hit)

    def test_store_without_qdrant(self):
        """Store returns False when Qdrant is unavailable."""
        cache = self.mod.SemanticCache(self.tmpdir)
        ok = cache.store("prompt", "response")
        self.assertFalse(ok)

    def test_invalidate_without_qdrant(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        count = cache.invalidate(["test.py"])
        self.assertEqual(count, 0)

    def test_clear_all(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        count = cache.clear()
        # Returns -1 (all cleared) if qdrant available, 0 otherwise
        self.assertIn(count, (0, -1))

    def test_get_stats_default(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        stats = cache.get_stats()
        self.assertEqual(stats.total_entries, 0)
        # qdrant_available depends on whether qdrant-client is installed
        self.assertIsInstance(stats.qdrant_available, bool)

    def test_stats_accumulate_misses(self):
        cache = self.mod.SemanticCache(self.tmpdir)
        cache.query("test1")
        cache.query("test2")
        stats = cache.get_stats()
        self.assertGreaterEqual(stats.total_misses, 2)


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_no_file(self):
        config = self.mod.load_cache_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_build_from_config_defaults(self):
        cache = self.mod.build_cache_from_config(self.tmpdir)
        self.assertIsInstance(cache, self.mod.SemanticCache)
        self.assertAlmostEqual(cache.threshold, 0.90)


# ── CLI Integration ────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, timeout=15,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Semantic Cache", r.stdout)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("semantic-cache", r.stdout)

    def test_stats_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "stats")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Stats", r.stdout)

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_query_without_qdrant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "query", "--prompt", "test")
            self.assertEqual(r.returncode, 0)
            self.assertIn("MISS", r.stdout)

    def test_clear_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "clear")
            self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
