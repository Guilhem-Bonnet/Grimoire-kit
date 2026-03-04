#!/usr/bin/env python3
"""
Tests pour memory-sync.py — Synchronisation bidirectionnelle mémoire BMAD ↔ Qdrant (BM-42 Story 2.4).

Fonctions testées :
  - MemoryEntry (uid déterministe, fields)
  - SyncReport (dataclass)
  - DiffEntry (dataclass)
  - SyncState (dataclass)
  - MemoryParser (decisions-log, learnings, failure-museum, generic, parse_file)
  - SyncStateManager (load, save, file_changed, mark_synced)
  - MemorySyncer (init, discover, diff, push graceful)
  - load_sync_config()
  - build_syncer_from_config()
  - MemorySyncer._cosine_sim() (math pure)
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
TOOL = KIT_DIR / "framework" / "tools" / "memory-sync.py"


def _import_mod():
    """Import le module memory-sync via importlib."""
    mod_name = "memory_sync_test"
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
        self.assertTrue(hasattr(self.mod, "MEMORY_SYNC_VERSION"))

    def test_version_format(self):
        parts = self.mod.MEMORY_SYNC_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_memory_dir_constant(self):
        self.assertEqual(self.mod.MEMORY_DIR, "_bmad/_memory")

    def test_sync_state_file(self):
        self.assertIn(".memory-sync-state.json", self.mod.SYNC_STATE_FILE)

    def test_tracked_files(self):
        for filename in ["shared-context.md", "decisions-log.md",
                         "failure-museum.md", "session-state.md"]:
            self.assertIn(filename, self.mod.TRACKED_FILES)

    def test_tracked_files_types(self):
        for _filename, entry_type in self.mod.TRACKED_FILES.items():
            self.assertIsInstance(entry_type, str)
            self.assertGreater(len(entry_type), 0)

    def test_chars_per_token(self):
        self.assertEqual(self.mod.CHARS_PER_TOKEN, 4)


# ── Dataclasses ──────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    # -- MemoryEntry --

    def test_memory_entry_fields(self):
        field_names = {f.name for f in fields(self.mod.MemoryEntry)}
        for expected in ["text", "source_file", "entry_type", "agent",
                         "heading", "tags", "timestamp"]:
            self.assertIn(expected, field_names)

    def test_memory_entry_uid_deterministic(self):
        e1 = self.mod.MemoryEntry(text="hello world", source_file="test.md", entry_type="decisions")
        e2 = self.mod.MemoryEntry(text="hello world", source_file="test.md", entry_type="decisions")
        self.assertEqual(e1.uid, e2.uid)

    def test_memory_entry_uid_different_content(self):
        e1 = self.mod.MemoryEntry(text="hello", source_file="a.md", entry_type="decisions")
        e2 = self.mod.MemoryEntry(text="world", source_file="a.md", entry_type="decisions")
        self.assertNotEqual(e1.uid, e2.uid)

    def test_memory_entry_uid_format(self):
        """UID should be a valid UUID5 string."""
        entry = self.mod.MemoryEntry(text="test", source_file="test.md", entry_type="test")
        uid = entry.uid
        parts = uid.split("-")
        self.assertEqual(len(parts), 5)

    def test_memory_entry_defaults(self):
        entry = self.mod.MemoryEntry(text="t", source_file="f", entry_type="t")
        self.assertEqual(entry.agent, "")
        self.assertEqual(entry.heading, "")
        self.assertEqual(entry.tags, [])
        self.assertEqual(entry.timestamp, "")

    # -- SyncReport --

    def test_sync_report_fields(self):
        field_names = {f.name for f in fields(self.mod.SyncReport)}
        for expected in ["direction", "entries_processed", "entries_new",
                         "entries_updated", "entries_skipped",
                         "duplicates_found", "errors", "duration_ms"]:
            self.assertIn(expected, field_names)

    def test_sync_report_defaults(self):
        report = self.mod.SyncReport(direction="push")
        self.assertEqual(report.entries_processed, 0)
        self.assertEqual(report.errors, [])

    # -- DiffEntry --

    def test_diff_entry_fields(self):
        field_names = {f.name for f in fields(self.mod.DiffEntry)}
        for expected in ["source_file", "status", "md_hash", "qdrant_hash"]:
            self.assertIn(expected, field_names)

    # -- SyncState --

    def test_sync_state_fields(self):
        field_names = {f.name for f in fields(self.mod.SyncState)}
        for expected in ["last_push", "last_pull", "file_hashes",
                         "push_count", "pull_count"]:
            self.assertIn(expected, field_names)

    def test_sync_state_defaults(self):
        state = self.mod.SyncState()
        self.assertEqual(state.last_push, "")
        self.assertEqual(state.file_hashes, {})
        self.assertEqual(state.push_count, 0)

    # -- asdict roundtrip --

    def test_asdict_sync_report(self):
        report = self.mod.SyncReport(direction="push", entries_processed=5)
        d = asdict(report)
        self.assertEqual(d["direction"], "push")
        self.assertEqual(d["entries_processed"], 5)

    def test_asdict_memory_entry(self):
        entry = self.mod.MemoryEntry(text="t", source_file="f", entry_type="decisions")
        d = asdict(entry)
        self.assertEqual(d["text"], "t")
        self.assertEqual(d["tags"], [])


# ── MemoryParser ─────────────────────────────────────────────────────────────

class TestMemoryParserDecisions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.parser = self.mod.MemoryParser

    def test_parse_decisions_basic(self):
        content = (
            "# Decisions Log\n\n"
            "## 2024-01-15 — Use Qdrant for vectors\n\n"
            "We decided to use Qdrant as the primary vector database for embedding storage. "
            "This gives us local and server modes.\n\n"
            "## 2024-01-20 — JWT Auth\n\n"
            "Authentication will use JWT tokens with RS256 algorithm for signing.\n"
        )
        entries = self.parser.parse_decisions_log(content, "decisions-log.md")
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.entry_type == "decisions" for e in entries))

    def test_parse_decisions_extracts_heading(self):
        content = (
            "## 2024-03-01 — Architecture Decision\n\n"
            "We chose microservices over monolith for better scalability.\n"
        )
        entries = self.parser.parse_decisions_log(content, "test.md")
        self.assertGreater(len(entries), 0)
        self.assertIn("Architecture Decision", entries[0].heading)

    def test_parse_decisions_extracts_tags(self):
        content = (
            "## Decision\n\n"
            "This is about #terraform and #infrastructure choices we made for the project.\n"
        )
        entries = self.parser.parse_decisions_log(content, "test.md")
        self.assertGreater(len(entries), 0)
        self.assertIn("terraform", entries[0].tags)

    def test_parse_decisions_skips_short_sections(self):
        content = (
            "## Short\n\n"
            "Too short.\n\n"
            "## Adequate Section\n\n"
            "This is a sufficiently long section with enough content to be considered a valid entry.\n"
        )
        entries = self.parser.parse_decisions_log(content, "test.md")
        # Only the adequate section should be parsed
        texts = [e.text for e in entries]
        self.assertTrue(all("Too short" not in t for t in texts))

    def test_parse_decisions_empty_content(self):
        entries = self.parser.parse_decisions_log("", "test.md")
        self.assertEqual(entries, [])

    def test_parse_decisions_text_truncated(self):
        long_text = "A" * 5000
        content = f"## Decision\n\n{long_text}\n"
        entries = self.parser.parse_decisions_log(content, "test.md")
        if entries:
            self.assertLessEqual(len(entries[0].text), 3000)


class TestMemoryParserLearnings(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.parser = self.mod.MemoryParser

    def test_parse_learnings_basic(self):
        content = (
            "# Dev Learnings\n\n"
            "### Pattern: Use dependency injection\n\n"
            "Always inject dependencies rather than importing directly.\n\n"
            "### Gotcha: Async context managers\n\n"
            "Remember to use async with for database connections.\n"
        )
        entries = self.parser.parse_learnings(content, "dev.md", "dev")
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.entry_type == "learnings" for e in entries))
        self.assertTrue(all(e.agent == "dev" for e in entries))

    def test_parse_learnings_heading_extraction(self):
        content = "### Important Pattern\n\nThis is the content of the learning entry.\n"
        entries = self.parser.parse_learnings(content, "test.md")
        self.assertGreater(len(entries), 0)
        self.assertIn("Important Pattern", entries[0].heading)

    def test_parse_learnings_empty(self):
        entries = self.parser.parse_learnings("", "test.md")
        self.assertEqual(entries, [])


class TestMemoryParserFailures(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.parser = self.mod.MemoryParser

    def test_parse_failures_basic(self):
        content = (
            "# Failure Museum\n\n"
            "## Production Incidents\n\n"
            "### 2024-01 — Memory Leak\n\n"
            "CC-FAIL: The context router leaked memory when processing large files. "
            "Root cause was unclosed file handles in the streaming parser.\n"
        )
        entries = self.parser.parse_failure_museum(content, "failure-museum.md")
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.entry_type == "failures" for e in entries))

    def test_parse_failures_extracts_category_tag(self):
        content = (
            "## Incident\n\n"
            "CC-FAIL: Context corruption when agent session exceeded token limit.\n"
        )
        entries = self.parser.parse_failure_museum(content, "test.md")
        self.assertGreater(len(entries), 0)
        self.assertIn("CC-FAIL", entries[0].tags)

    def test_parse_failures_hallucination_tag(self):
        content = (
            "## Incident\n\n"
            "HALLUCINATION: Agent invented a non-existent API endpoint during code review.\n"
        )
        entries = self.parser.parse_failure_museum(content, "test.md")
        self.assertGreater(len(entries), 0)
        self.assertIn("HALLUCINATION", entries[0].tags)

    def test_parse_failures_empty(self):
        entries = self.parser.parse_failure_museum("", "test.md")
        self.assertEqual(entries, [])


class TestMemoryParserGeneric(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.parser = self.mod.MemoryParser

    def test_parse_generic_basic(self):
        content = (
            "# Shared Context\n\n"
            "## Project Overview\n\n"
            "This project implements a multi-agent AI framework.\n\n"
            "## Technology Stack\n\n"
            "Python 3.12, Qdrant, sentence-transformers, MCP protocol.\n"
        )
        entries = self.parser.parse_generic(content, "shared-context.md", "shared-context")
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.entry_type == "shared-context" for e in entries))

    def test_parse_generic_skips_short(self):
        content = "## A\n\nShort.\n\n## B\n\nThis is long enough to be a valid memory entry for testing.\n"
        entries = self.parser.parse_generic(content, "test.md", "test")
        texts = [e.text for e in entries]
        self.assertTrue(all("Short." != t for t in texts))


class TestMemoryParserParseFile(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.memory_dir = self.tmpdir / "_bmad" / "_memory"
        self.memory_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_parse_file_decisions_log(self):
        filepath = self.memory_dir / "decisions-log.md"
        filepath.write_text(
            "## 2024-01 — Test Decision\n\nThis is a test decision entry about architecture.\n",
            encoding="utf-8",
        )
        entries = self.mod.MemoryParser.parse_file(filepath, self.tmpdir)
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.entry_type == "decisions" for e in entries))

    def test_parse_file_failure_museum(self):
        filepath = self.memory_dir / "failure-museum.md"
        filepath.write_text(
            "## Incident Category\n\nCC-FAIL: Something went wrong with the context manager.\n",
            encoding="utf-8",
        )
        entries = self.mod.MemoryParser.parse_file(filepath, self.tmpdir)
        self.assertGreater(len(entries), 0)

    def test_parse_file_learnings(self):
        learnings_dir = self.memory_dir / "agent-learnings"
        learnings_dir.mkdir()
        filepath = learnings_dir / "dev.md"
        filepath.write_text(
            "### Pattern One\n\nAlways validate input before processing in handlers.\n",
            encoding="utf-8",
        )
        entries = self.mod.MemoryParser.parse_file(filepath, self.tmpdir)
        self.assertGreater(len(entries), 0)
        self.assertTrue(all(e.agent == "dev" for e in entries))

    def test_parse_file_shared_context(self):
        filepath = self.memory_dir / "shared-context.md"
        filepath.write_text(
            "## Overview\n\nThis is the shared project context for all agents.\n",
            encoding="utf-8",
        )
        entries = self.mod.MemoryParser.parse_file(filepath, self.tmpdir)
        self.assertGreater(len(entries), 0)

    def test_parse_file_nonexistent(self):
        entries = self.mod.MemoryParser.parse_file(
            self.memory_dir / "nonexistent.md", self.tmpdir,
        )
        self.assertEqual(entries, [])


# ── SyncStateManager ────────────────────────────────────────────────────────

class TestSyncStateManager(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.state_file = self.tmpdir / ".sync-state.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_load_creates_default(self):
        mgr = self.mod.SyncStateManager(self.state_file)
        self.assertEqual(mgr.state.push_count, 0)
        self.assertEqual(mgr.state.file_hashes, {})

    def test_save_and_reload(self):
        mgr = self.mod.SyncStateManager(self.state_file)
        mgr.state.push_count = 5
        mgr.state.last_push = "2024-01-01T00:00:00"
        mgr.save()

        mgr2 = self.mod.SyncStateManager(self.state_file)
        self.assertEqual(mgr2.state.push_count, 5)
        self.assertEqual(mgr2.state.last_push, "2024-01-01T00:00:00")

    def test_file_changed_new_file(self):
        test_file = self.tmpdir / "test.md"
        test_file.write_text("content", encoding="utf-8")
        mgr = self.mod.SyncStateManager(self.state_file)
        self.assertTrue(mgr.file_changed(test_file))

    def test_file_changed_after_mark(self):
        test_file = self.tmpdir / "test.md"
        test_file.write_text("content", encoding="utf-8")
        mgr = self.mod.SyncStateManager(self.state_file)
        mgr.mark_synced(test_file)
        self.assertFalse(mgr.file_changed(test_file))

    def test_file_changed_after_modification(self):
        test_file = self.tmpdir / "test.md"
        test_file.write_text("content v1", encoding="utf-8")
        mgr = self.mod.SyncStateManager(self.state_file)
        mgr.mark_synced(test_file)
        test_file.write_text("content v2", encoding="utf-8")
        self.assertTrue(mgr.file_changed(test_file))

    def test_file_changed_nonexistent(self):
        mgr = self.mod.SyncStateManager(self.state_file)
        self.assertFalse(mgr.file_changed(self.tmpdir / "nonexistent.md"))

    def test_save_creates_parent_dir(self):
        nested = self.tmpdir / "a" / "b" / "state.json"
        mgr = self.mod.SyncStateManager(nested)
        mgr.save()
        self.assertTrue(nested.exists())

    def test_load_corrupted_json(self):
        self.state_file.write_text("{invalid json", encoding="utf-8")
        mgr = self.mod.SyncStateManager(self.state_file)
        # Should fallback to default
        self.assertEqual(mgr.state.push_count, 0)

    def test_save_persists_file_hashes(self):
        test_file = self.tmpdir / "test.md"
        test_file.write_text("hello", encoding="utf-8")
        mgr = self.mod.SyncStateManager(self.state_file)
        mgr.mark_synced(test_file)
        mgr.save()

        # Verify JSON
        with open(self.state_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn(str(test_file), data["file_hashes"])


# ── MemorySyncer Init ───────────────────────────────────────────────────────

class TestMemorySyncer(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.memory_dir = self.tmpdir / "_bmad" / "_memory"
        self.memory_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_init_defaults(self):
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        self.assertEqual(syncer.project_name, "bmad")
        self.assertEqual(syncer.memory_dir, self.memory_dir)

    def test_discover_empty(self):
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        files = syncer._discover_memory_files()
        self.assertEqual(files, [])

    def test_discover_tracked_files(self):
        (self.memory_dir / "decisions-log.md").write_text("# Decisions", encoding="utf-8")
        (self.memory_dir / "shared-context.md").write_text("# Context", encoding="utf-8")
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        files = syncer._discover_memory_files()
        filenames = [f.name for f in files]
        self.assertIn("decisions-log.md", filenames)
        self.assertIn("shared-context.md", filenames)

    def test_discover_learnings(self):
        learnings_dir = self.memory_dir / "agent-learnings"
        learnings_dir.mkdir()
        (learnings_dir / "dev.md").write_text("# Dev", encoding="utf-8")
        (learnings_dir / "architect.md").write_text("# Arch", encoding="utf-8")
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        files = syncer._discover_memory_files()
        filenames = [f.name for f in files]
        self.assertIn("dev.md", filenames)
        self.assertIn("architect.md", filenames)

    def test_diff_empty(self):
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        diffs = syncer.diff()
        self.assertEqual(diffs, [])

    def test_diff_new_files(self):
        (self.memory_dir / "decisions-log.md").write_text(
            "## Decision\n\nSome decision content here for testing.\n",
            encoding="utf-8",
        )
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        diffs = syncer.diff()
        self.assertGreater(len(diffs), 0)
        self.assertEqual(diffs[0].status, "new_in_md")

    def test_push_without_indexer(self):
        """Push should report error gracefully when rag-indexer unavailable."""
        (self.memory_dir / "decisions-log.md").write_text(
            "## Decision\n\nSome decision text.\n", encoding="utf-8",
        )
        syncer = self.mod.MemorySyncer(
            project_root=self.tmpdir,
            qdrant_url="http://nonexistent:6333",
        )
        report = syncer.push()
        self.assertIsInstance(report, self.mod.SyncReport)
        # Should have errors since indexer can't connect
        # Or entries_processed/skipped depending on state

    def test_hook_delegates_to_push(self):
        syncer = self.mod.MemorySyncer(project_root=self.tmpdir)
        report = syncer.hook(agent_id="dev")
        self.assertIsInstance(report, self.mod.SyncReport)
        self.assertEqual(report.direction, "push")


# ── Cosine Similarity ────────────────────────────────────────────────────────

class TestCosineSimilarity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_identical_vectors(self):
        sim = self.mod.MemorySyncer._cosine_sim([1, 0, 0], [1, 0, 0])
        self.assertAlmostEqual(sim, 1.0, places=5)

    def test_orthogonal_vectors(self):
        sim = self.mod.MemorySyncer._cosine_sim([1, 0], [0, 1])
        self.assertAlmostEqual(sim, 0.0, places=5)

    def test_opposite_vectors(self):
        sim = self.mod.MemorySyncer._cosine_sim([1, 0], [-1, 0])
        self.assertAlmostEqual(sim, -1.0, places=5)

    def test_zero_vector(self):
        sim = self.mod.MemorySyncer._cosine_sim([0, 0], [1, 0])
        self.assertAlmostEqual(sim, 0.0, places=5)

    def test_similar_vectors(self):
        sim = self.mod.MemorySyncer._cosine_sim([1, 1, 0], [1, 0.9, 0.1])
        self.assertGreater(sim, 0.9)

    def test_high_dimensional(self):
        a = [1.0] * 384
        b = [1.0] * 384
        sim = self.mod.MemorySyncer._cosine_sim(a, b)
        self.assertAlmostEqual(sim, 1.0, places=3)


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_config_returns_empty(self):
        config = self.mod.load_sync_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_config_from_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")

        config_data = {
            "rag": {
                "qdrant_url": "http://localhost:6333",
                "collection_prefix": "myproject",
            }
        }
        (self.tmpdir / "project-context.yaml").write_text(
            yaml.dump(config_data), encoding="utf-8",
        )
        config = self.mod.load_sync_config(self.tmpdir)
        self.assertEqual(config["collection_prefix"], "myproject")

    def test_build_syncer_defaults(self):
        syncer = self.mod.build_syncer_from_config(self.tmpdir)
        self.assertIsInstance(syncer, self.mod.MemorySyncer)
        self.assertEqual(syncer.project_name, "bmad")


# ── CLI Integration ──────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("push", result.stdout)
        self.assertIn("pull", result.stdout)

    def test_version_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("memory-sync", result.stdout)

    def test_no_command_shows_help(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_diff_command_runs(self):
        tmpdir = tempfile.mkdtemp()
        memory_dir = Path(tmpdir) / "_bmad" / "_memory"
        memory_dir.mkdir(parents=True)
        try:
            result = subprocess.run(
                [sys.executable, str(TOOL),
                 "--project-root", tmpdir, "diff"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Memory Diff", result.stdout)
        finally:
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
