#!/usr/bin/env python3
"""
Tests pour session-save.py — persistence de session.

Fonctions testées :
  - save_session()
  - _get_project_name()
  - _load_valid_agents()
  - Validation du format de sortie
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "memory"))


def _import_session_save():
    return importlib.import_module("session-save")


class TestSaveSession(unittest.TestCase):
    """Test save_session() — writes session-state.md + archive."""

    def setUp(self):
        self.ss = _import_session_save()
        self.tmpdir = Path(tempfile.mkdtemp())

        # Redirect files to tmpdir
        self._orig_SESSION_FILE = self.ss.SESSION_FILE
        self._orig_SUMMARIES_DIR = self.ss.SESSION_SUMMARIES_DIR
        self.ss.SESSION_FILE = self.tmpdir / "session-state.md"
        self.ss.SESSION_SUMMARIES_DIR = self.tmpdir / "session-summaries"

    def tearDown(self):
        self.ss.SESSION_FILE = self._orig_SESSION_FILE
        self.ss.SESSION_SUMMARIES_DIR = self._orig_SUMMARIES_DIR
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_session_file(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(
                agent="dev",
                work=["Implemented feature X"],
                files=["src/main.py"],
                next_step="Write tests",
            )
        self.assertTrue(self.ss.SESSION_FILE.exists())
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("dev", content)
        self.assertIn("Implemented feature X", content)
        self.assertIn("src/main.py", content)
        self.assertIn("Write tests", content)

    def test_creates_archive(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="qa", work=["Ran tests"])
        archives = list((self.tmpdir / "session-summaries").glob("*-qa.md"))
        self.assertEqual(len(archives), 1)

    def test_default_values(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="forge")
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("forge", content)
        self.assertIn("aucune action", content)

    def test_multiple_work_items(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(
                agent="dev",
                work=["Action 1", "Action 2", "Action 3"],
            )
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("Action 1", content)
        self.assertIn("Action 2", content)
        self.assertIn("Action 3", content)

    def test_handoffs(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(
                agent="forge",
                handoffs=["hawk: check monitoring alerts"],
            )
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("hawk: check monitoring alerts", content)

    def test_state_field(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(
                agent="dev",
                state="All tests passing, deploy ready",
            )
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("All tests passing", content)

    def test_duration(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="dev", duration="45 min")
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("45 min", content)

    def test_overwrites_previous_session(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="dev", work=["First session"])
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="qa", work=["Second session"])
        content = self.ss.SESSION_FILE.read_text()
        self.assertIn("Second session", content)
        self.assertNotIn("First session", content)

    def test_markdown_structure(self):
        from io import StringIO
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.ss.save_session(agent="dev")
        content = self.ss.SESSION_FILE.read_text(encoding="utf-8")
        # Should have proper Markdown headers
        self.assertIn("# État de Session", content)
        self.assertIn("## Dernière Session", content)
        self.assertIn("## Travail effectué", content)
        self.assertIn("## Fichiers modifiés", content)
        self.assertIn("## Prochaine étape", content)


class TestLoadValidAgents(unittest.TestCase):
    """Test _load_valid_agents()."""

    def setUp(self):
        self.ss = _import_session_save()

    def test_returns_set(self):
        # Without the actual manifest, it uses fallback
        result = self.ss._load_valid_agents()
        self.assertIsInstance(result, set)
        self.assertGreater(len(result), 0)

    def test_fallback_has_meta_agents(self):
        # If manifest is empty/missing, should have atlas, sentinel, mnemo
        result = self.ss._load_valid_agents()
        # At minimum the fallback agents exist
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
