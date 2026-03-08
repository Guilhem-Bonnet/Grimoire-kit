#!/usr/bin/env python3
"""
Tests avancés pour maintenance.py — opérations e2e avec tmpdir.

Fonctions testées :
  - archive()
  - compact()
  - prune_decisions()
  - prune_learnings()
  - prune_activity()
  - health_check()
  - consolidate_learnings()
  - _detect_context_drift()
  - memory_audit() (capture stdout)
  - export_readable() (capture stdout)
"""

import csv
import importlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "memory"))


def _import_maint():
    return importlib.import_module("maintenance")


class _MaintenanceTestBase(unittest.TestCase):
    """Base class : redirige les paths de maintenance.py vers un tmpdir."""

    def setUp(self):
        self.maint = _import_maint()
        self.tmpdir = Path(tempfile.mkdtemp())

        # Sauvegarder les valeurs originales
        self._orig_MEMORIES_FILE = self.maint.MEMORIES_FILE
        self._orig_ARCHIVE_DIR = self.maint.ARCHIVE_DIR
        self._orig_DECISIONS_LOG = self.maint.DECISIONS_LOG
        self._orig_LEARNINGS_DIR = self.maint.LEARNINGS_DIR
        self._orig_ACTIVITY_LOG = self.maint.ACTIVITY_LOG
        self._orig_SHARED_CONTEXT = self.maint.SHARED_CONTEXT
        self._orig_AGENT_MANIFEST = self.maint.AGENT_MANIFEST
        self._orig_HEALTH_STATE = self.maint.HEALTH_STATE_FILE

        # Rediriger tout vers tmpdir
        self.maint.MEMORIES_FILE = self.tmpdir / "memories.json"
        self.maint.ARCHIVE_DIR = self.tmpdir / "archives"
        self.maint.DECISIONS_LOG = self.tmpdir / "decisions-log.md"
        self.maint.LEARNINGS_DIR = self.tmpdir / "agent-learnings"
        self.maint.ACTIVITY_LOG = self.tmpdir / "activity.jsonl"
        self.maint.SHARED_CONTEXT = self.tmpdir / "shared-context.md"
        self.maint.AGENT_MANIFEST = self.tmpdir / "agent-manifest.csv"
        self.maint.HEALTH_STATE_FILE = self.tmpdir / ".last-health-check"

    def tearDown(self):
        # Restaurer
        self.maint.MEMORIES_FILE = self._orig_MEMORIES_FILE
        self.maint.ARCHIVE_DIR = self._orig_ARCHIVE_DIR
        self.maint.DECISIONS_LOG = self._orig_DECISIONS_LOG
        self.maint.LEARNINGS_DIR = self._orig_LEARNINGS_DIR
        self.maint.ACTIVITY_LOG = self._orig_ACTIVITY_LOG
        self.maint.SHARED_CONTEXT = self._orig_SHARED_CONTEXT
        self.maint.AGENT_MANIFEST = self._orig_AGENT_MANIFEST
        self.maint.HEALTH_STATE_FILE = self._orig_HEALTH_STATE
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestArchive(_MaintenanceTestBase):
    """Test archive() — archive old memories."""

    def test_archive_empty(self):
        """Archive with no memories does nothing."""
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.archive(7)
        self.assertIn("Aucune", captured.getvalue())

    def test_archive_moves_old_entries(self):
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        recent_date = datetime.now(UTC).isoformat()
        memories = [
            {"memory": "old memory", "timestamp": old_date},
            {"memory": "recent memory", "timestamp": recent_date},
        ]
        self.maint.save_memories(memories)

        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.archive(30)

        remaining = self.maint.load_memories()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["memory"], "recent memory")

        # Archive file created
        archives = list(self.maint.ARCHIVE_DIR.glob("*.json"))
        self.assertEqual(len(archives), 1)
        with open(archives[0], encoding="utf-8") as f:
            archived = json.load(f)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["memory"], "old memory")

    def test_archive_nothing_to_do(self):
        recent_date = datetime.now(UTC).isoformat()
        self.maint.save_memories([{"memory": "fresh", "timestamp": recent_date}])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.archive(30)
        self.assertIn("Aucune entrée", captured.getvalue())


class TestCompact(_MaintenanceTestBase):
    """Test compact() — dedup and clean."""

    def test_compact_removes_duplicates(self):
        memories = [
            {"memory": "Use Python 3.12 for this project", "agent": "dev"},
            {"memory": "Use Python 3.12 for this project", "agent": "dev"},
            {"memory": "Deploy Docker containers", "agent": "ops"},
        ]
        self.maint.save_memories(memories)
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.compact()
        remaining = self.maint.load_memories()
        self.assertEqual(len(remaining), 2)

    def test_compact_removes_short_entries(self):
        memories = [
            {"memory": "ok", "agent": "dev"},
            {"memory": "A legitimate learning entry with context.", "agent": "dev"},
        ]
        self.maint.save_memories(memories)
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.compact()
        remaining = self.maint.load_memories()
        self.assertEqual(len(remaining), 1)
        self.assertIn("legitimate", remaining[0]["memory"])

    def test_compact_empty(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.compact()
        self.assertIn("Aucune", captured.getvalue())

    def test_compact_no_dupes(self):
        memories = [
            {"memory": "First unique memory entry.", "agent": "a"},
            {"memory": "Second unique memory entry.", "agent": "b"},
        ]
        self.maint.save_memories(memories)
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.compact()
        self.assertIn("Aucun doublon", captured.getvalue())


class TestPruneDecisions(_MaintenanceTestBase):
    """Test prune_decisions() — archive old decision sections."""

    def test_prune_old_sections(self):
        old_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        recent_date = datetime.now().strftime("%Y-%m-%d")
        content = (
            f"## {old_date}\n"
            f"| Agent | Decision |\n"
            f"|-------|----------|\n"
            f"| dev   | Use microservices |\n"
            f"\n"
            f"## {recent_date}\n"
            f"| Agent | Decision |\n"
            f"|-------|----------|\n"
            f"| arch  | Use event sourcing |\n"
        )
        self.maint.DECISIONS_LOG.write_text(content, encoding="utf-8")
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_decisions(3)

        # Old section should be archived
        archives = list(self.maint.ARCHIVE_DIR.glob("decisions-archive-*.md"))
        self.assertEqual(len(archives), 1)
        # Recent should remain
        remaining = self.maint.DECISIONS_LOG.read_text()
        self.assertIn(recent_date, remaining)
        self.assertNotIn(old_date, remaining)

    def test_no_decisions_log(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_decisions()
        self.assertIn("Aucun decisions-log", captured.getvalue())


class TestPruneLearnings(_MaintenanceTestBase):
    """Test prune_learnings() — detect duplicates."""

    def test_detects_duplicates(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        (self.maint.LEARNINGS_DIR / "agent-learnings-dev.md").write_text(
            "# Dev Learnings\n"
            "- [2026-01-01] Always use type hints in Python\n"
            "- [2026-01-15] Always use type hints in Python functions\n"
            "- [2026-02-01] Docker containers need health checks\n"
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_learnings()
        self.assertIn("doublon", captured.getvalue().lower())

    def test_no_duplicates(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        (self.maint.LEARNINGS_DIR / "agent-learnings-qa.md").write_text(
            "# QA Learnings\n"
            "- Always run integration tests before merge\n"
            "- Docker builds need multi-stage for security\n"
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_learnings()
        self.assertIn("Aucun doublon", captured.getvalue())

    def test_no_learnings_dir(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_learnings()
        self.assertIn("Aucun répertoire", captured.getvalue())


class TestPruneActivity(_MaintenanceTestBase):
    """Test prune_activity() — compact old activity."""

    def test_prune_old_events(self):
        old_ts = (datetime.now() - timedelta(days=120)).isoformat()
        recent_ts = datetime.now().isoformat()
        events = [
            {"ts": old_ts, "cmd": "search", "top_score": 0.9},
            {"ts": old_ts, "cmd": "add", "top_score": 0.5},
            {"ts": recent_ts, "cmd": "search", "top_score": 0.8},
        ]
        with open(self.maint.ACTIVITY_LOG, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_activity(90)

        # Check archive created
        archives = list(self.maint.ARCHIVE_DIR.glob("activity-archive-*.jsonl"))
        self.assertEqual(len(archives), 1)

        # Check remaining
        with open(self.maint.ACTIVITY_LOG, encoding="utf-8") as f:
            remaining = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["ts"], recent_ts)

    def test_nothing_to_prune(self):
        recent_ts = datetime.now().isoformat()
        with open(self.maint.ACTIVITY_LOG, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": recent_ts, "cmd": "search"}) + "\n")
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_activity(90)
        self.assertIn("derniers jours", captured.getvalue())

    def test_no_activity_log(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.prune_activity()
        self.assertIn("Aucun fichier", captured.getvalue())


class TestHealthCheck(_MaintenanceTestBase):
    """Test health_check()."""

    def test_healthy_memory(self):
        self.maint.save_memories([
            {"memory": "A healthy memory entry here.", "timestamp": datetime.now(UTC).isoformat()},
        ])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.health_check(force=True)
        self.assertIn("saine", captured.getvalue())

    def test_auto_compact_on_dupes(self):
        self.maint.save_memories([
            {"memory": "Duplicate memory that should be compacted", "timestamp": datetime.now(UTC).isoformat()},
            {"memory": "Duplicate memory that should be compacted", "timestamp": datetime.now(UTC).isoformat()},
            {"memory": "Unique memory entry standing alone.", "timestamp": datetime.now(UTC).isoformat()},
        ])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.health_check(force=True)
        remaining = self.maint.load_memories()
        self.assertEqual(len(remaining), 2)

    def test_rate_limited(self):
        # Write a recent health check timestamp
        self.maint.HEALTH_STATE_FILE.write_text(datetime.now().isoformat())
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.health_check(force=False)
        # Should be silent (rate-limited)
        self.assertEqual(captured.getvalue(), "")

    def test_force_bypasses_rate_limit(self):
        self.maint.HEALTH_STATE_FILE.write_text(datetime.now().isoformat())
        self.maint.save_memories([
            {"memory": "Memory for forced check.", "timestamp": datetime.now(UTC).isoformat()},
        ])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.health_check(force=True)
        output = captured.getvalue()
        self.assertTrue("Health-check" in output or "saine" in output, f"Expected health output, got: {output!r}")


class TestConsolidateLearnings(_MaintenanceTestBase):
    """Test consolidate_learnings()."""

    def test_merges_duplicates(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        f = self.maint.LEARNINGS_DIR / "dev.md"
        f.write_text(
            "# Dev Learnings\n"
            "- [2026-01-01] Always validate inputs before processing\n"
            "- [2026-02-01] Always validate inputs before processing them\n"
            "- [2026-03-01] Use Docker health checks in compose\n"
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.consolidate_learnings("dev")
        content = f.read_text()
        # Should have removed one duplicate
        lines = [ln for ln in content.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(lines), 2)

    def test_no_duplicates_no_change(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        f = self.maint.LEARNINGS_DIR / "qa.md"
        original = "# QA\n- Run tests before merge\n- Check coverage metrics\n"
        f.write_text(original)
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.consolidate_learnings("qa")
        # File unchanged
        self.assertEqual(f.read_text(), original)

    def test_missing_file_silent(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        # Should not crash
        self.maint.consolidate_learnings("nonexistent")

    def test_all_files_mode(self):
        self.maint.LEARNINGS_DIR.mkdir(parents=True)
        for name in ["dev", "qa"]:
            (self.maint.LEARNINGS_DIR / f"{name}.md").write_text(
                f"# {name}\n- Unique entry for {name}\n"
            )
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.consolidate_learnings(None)  # all files
        self.assertIn("Aucun doublon", captured.getvalue())


class TestDetectContextDrift(_MaintenanceTestBase):
    """Test _detect_context_drift()."""

    def test_no_drift(self):
        # Create shared-context mentioning the same agents as manifest
        self.maint.SHARED_CONTEXT.write_text(
            "# Shared Context\n"
            "## Équipe d'Agents Custom\n"
            "| Agent | Role |\n"
            "|-------|------|\n"
            "| forge | Terraform |\n"
            "| hawk  | Monitoring |\n"
        )
        self.maint.AGENT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with open(self.maint.AGENT_MANIFEST, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "module"])
            writer.writeheader()
            writer.writerow({"name": "forge", "module": "custom"})
            writer.writerow({"name": "hawk", "module": "custom"})
        drifts = self.maint._detect_context_drift()
        self.assertEqual(len(drifts), 0)

    def test_missing_agent_in_context(self):
        self.maint.SHARED_CONTEXT.write_text(
            "# Shared Context\n"
            "## Équipe d'Agents Custom\n"
            "| Agent | Role |\n"
            "|-------|------|\n"
            "| forge | Terraform |\n"
        )
        self.maint.AGENT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with open(self.maint.AGENT_MANIFEST, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "module"])
            writer.writeheader()
            writer.writerow({"name": "forge", "module": "custom"})
            writer.writerow({"name": "hawk", "module": "custom"})
        drifts = self.maint._detect_context_drift()
        self.assertGreater(len(drifts), 0)
        self.assertTrue(any("hawk" in d for d in drifts))

    def test_no_files(self):
        drifts = self.maint._detect_context_drift()
        self.assertEqual(len(drifts), 0)


class TestExportReadable(_MaintenanceTestBase):
    """Test export_readable()."""

    def test_export_formats_correctly(self):
        self.maint.save_memories([
            {"agent": "dev", "memory": "Use type hints", "timestamp": "2026-01-01T12:00:00"},
            {"agent": "qa", "memory": "Run coverage", "timestamp": "2026-02-01T12:00:00"},
        ])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.export_readable()
        output = captured.getvalue()
        self.assertIn("Export mémoire Grimoire", output)
        self.assertIn("2 entrées", output)
        self.assertIn("Use type hints", output)
        self.assertIn("[1]", output)
        self.assertIn("[2]", output)


class TestMemoryAudit(_MaintenanceTestBase):
    """Test memory_audit() — full audit."""

    def test_audit_runs_without_crash(self):
        self.maint.save_memories([
            {"memory": "A valid memory to audit here.", "timestamp": datetime.now(UTC).isoformat()},
        ])
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.memory_audit()
        output = captured.getvalue()
        self.assertIn("Audit Mémoire", output)
        self.assertIn("Score santé", output)

    def test_audit_empty_memory(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.maint.memory_audit()
        output = captured.getvalue()
        self.assertIn("0 entrées", output)


if __name__ == "__main__":
    unittest.main()
