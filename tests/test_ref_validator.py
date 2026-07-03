"""Tests for grimoire.core.ref_validator."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.ref_validator import RefIssue, RefReport, RefValidator


class TestRefIssue(unittest.TestCase):
    def test_to_dict(self) -> None:
        issue = RefIssue("doc.md", 10, "missing.py", "broken", "Not found")
        d = issue.to_dict()
        self.assertEqual(d["source"], "doc.md")
        self.assertEqual(d["type"], "broken")


class TestRefReport(unittest.TestCase):
    def test_clean(self) -> None:
        report = RefReport(5, 20, (), "2026-01-01T00:00:00Z")
        self.assertTrue(report.clean)
        self.assertEqual(report.broken_count, 0)
        self.assertEqual(report.stale_count, 0)

    def test_counts(self) -> None:
        issues = (
            RefIssue("a.md", 1, "x.py", "broken"),
            RefIssue("b.md", 2, "y.py", "broken"),
            RefIssue("c.md", 3, "z.py", "stale"),
        )
        report = RefReport(3, 10, issues, "2026-01-01T00:00:00Z")
        self.assertEqual(report.broken_count, 2)
        self.assertEqual(report.stale_count, 1)
        self.assertFalse(report.clean)

    def test_to_markdown(self) -> None:
        report = RefReport(1, 1, (RefIssue("a.md", 1, "x.py", "broken"),), "2026")
        md = report.to_markdown()
        self.assertIn("Reference Validation", md)
        self.assertIn("a.md", md)
        self.assertIn("x.py", md)


class TestRefValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # Create docs directory with markdown
        docs = self.root / "docs"
        docs.mkdir()

        # Target file that exists
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("# app")

        # Markdown with valid and broken links
        (docs / "guide.md").write_text(
            "# Guide\n\n"
            "See [the app](../src/app.py) for details.\n"
            "Also check [missing](../src/missing.py) file.\n"
            "External link [google](https://google.com) ignored.\n"
            "Backtick ref `src/other.py` in docs.\n"
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_validate_finds_broken(self) -> None:
        rv = RefValidator(self.root, scan_dirs=("docs",))
        report = rv.validate(check_stale=False)
        self.assertEqual(report.files_scanned, 1)
        self.assertGreater(report.refs_checked, 0)
        broken = [i for i in report.issues if i.issue_type == "broken"]
        # missing.py should be detected
        self.assertTrue(any("missing" in i.ref for i in broken))

    def test_validate_valid_links_pass(self) -> None:
        rv = RefValidator(self.root, scan_dirs=("docs",))
        report = rv.validate(check_stale=False)
        refs = [i.ref for i in report.issues]
        self.assertNotIn("../src/app.py", refs)

    def test_validate_skips_external(self) -> None:
        rv = RefValidator(self.root, scan_dirs=("docs",))
        report = rv.validate(check_stale=False)
        refs = [i.ref for i in report.issues]
        self.assertNotIn("https://google.com", refs)

    def test_validate_stale_detection(self) -> None:
        # Create a file with old mtime
        old_file = self.root / "src" / "old.py"
        old_file.write_text("# old")
        old_time = time.time() - (100 * 86400)  # 100 days ago
        import os
        os.utime(old_file, (old_time, old_time))

        docs = self.root / "docs"
        (docs / "stale-test.md").write_text("[old code](../src/old.py)\n")

        rv = RefValidator(self.root, scan_dirs=("docs",), staleness_days=90)
        report = rv.validate(check_stale=True)
        stale = [i for i in report.issues if i.issue_type == "stale"]
        self.assertTrue(any("old.py" in i.ref for i in stale))

    def test_validate_empty_project(self) -> None:
        with TemporaryDirectory() as td:
            rv = RefValidator(Path(td), scan_dirs=("docs",))
            report = rv.validate()
            self.assertTrue(report.clean)
            self.assertEqual(report.files_scanned, 0)

    def test_validate_file_specific(self) -> None:
        rv = RefValidator(self.root, scan_dirs=("docs",))
        issues = rv.validate_file(self.root / "docs" / "guide.md", check_stale=False)
        self.assertIsInstance(issues, list)

    def test_inline_code_ref(self) -> None:
        # src/other.py doesn't exist → should be broken
        rv = RefValidator(self.root, scan_dirs=("docs",))
        report = rv.validate(check_stale=False)
        broken_refs = [i.ref for i in report.issues if i.issue_type == "broken"]
        self.assertTrue(any("other.py" in r for r in broken_refs))

    def test_anchor_only_link_skipped(self) -> None:
        docs = self.root / "docs"
        (docs / "anchors.md").write_text("[section](#intro)\n")
        rv = RefValidator(self.root, scan_dirs=("docs",))
        report = rv.validate(check_stale=False)
        refs = [i.ref for i in report.issues]
        self.assertNotIn("#intro", refs)

    def test_relative_to_source_dir(self) -> None:
        # Link relative to the markdown file location
        sub = self.root / "docs" / "sub"
        sub.mkdir()
        (sub / "deep.md").write_text("[parent guide](../guide.md)\n")
        rv = RefValidator(self.root, scan_dirs=("docs",))
        # guide.md exists → should NOT be broken
        report = rv.validate(check_stale=False)
        broken_refs = [i.ref for i in report.issues if i.issue_type == "broken"]
        self.assertNotIn("../guide.md", broken_refs)


if __name__ == "__main__":
    unittest.main()
