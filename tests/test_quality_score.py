"""Tests for quality-score.py — D7 Runtime Quality Scoring."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "quality-score.py"


def _load():
    mod_name = "quality_score_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


qs = _load()


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertTrue(qs.QUALITY_SCORE_VERSION)


class TestDimensions(unittest.TestCase):
    def test_weights_sum_to_one(self):
        total = sum(qs.DIMENSIONS.values())
        self.assertAlmostEqual(total, 1.0, places=2)


class TestScoreCompleteness(unittest.TestCase):
    def test_good_markdown(self):
        content = "# Title\n\nParagraph content.\n\n## Section\n\nMore content here.\n" * 5
        score, _details = qs._score_completeness(content, ".md")
        self.assertGreaterEqual(score, 80)

    def test_empty_markdown(self):
        score, _details = qs._score_completeness("", ".md")
        self.assertLess(score, 50)

    def test_todos_penalized(self):
        content = "# Title\n\n## Section\n\nContent.\n" * 5 + "\nTODO: fix this\nFIXME: broken\n"
        score, _ = qs._score_completeness(content, ".md")
        score_clean, _ = qs._score_completeness("# Title\n\n## Section\n\nContent.\n" * 5, ".md")
        self.assertLess(score, score_clean)


class TestScoreStructure(unittest.TestCase):
    def test_unclosed_fence(self):
        content = "# Title\n\n```python\ncode\n"
        score, details = qs._score_structure(content, ".md")
        self.assertLess(score, 100)
        self.assertTrue(any("fence" in d.lower() for d in details))

    def test_heading_jump(self):
        content = "# Title\n\n### Jump to H3\n\nContent.\n"
        score, _details = qs._score_structure(content, ".md")
        self.assertLess(score, 100)


class TestScoreCCCompliance(unittest.TestCase):
    def test_cc_pass_marker(self):
        content = "# Report\n\n✅ CC PASS — py — 2024-01-01\n"
        score, _details = qs._score_cc_compliance(content, ".md")
        self.assertGreaterEqual(score, 90)

    def test_cc_fail_marker(self):
        content = "# Report\n\n🔴 CC FAIL\n"
        score, _ = qs._score_cc_compliance(content, ".md")
        self.assertLess(score, 60)

    def test_unresolved_placeholders(self):
        content = "# Title\n\nHello {{user_name}}, your {PROJECT_NAME} is ready.\n"
        score, _details = qs._score_cc_compliance(content, ".md")
        self.assertLess(score, 100)


class TestScoreConsistency(unittest.TestCase):
    def test_duplicate_headings(self):
        content = "# Title\n\n## Section\n\nContent.\n\n## Section\n\nMore.\n"
        score, _details = qs._score_consistency(content, ".md")
        self.assertLess(score, 100)

    def test_empty_section(self):
        content = "# Title\n## Empty\n## Next\n\nContent.\n"
        score, _details = qs._score_consistency(content, ".md")
        self.assertLess(score, 100)


class TestScoreArtifact(unittest.TestCase):
    def test_good_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Good Report\n\n## Overview\n\nDetailed content here.\n\n")
            f.write("## Analysis\n\nMore detailed analysis.\n\n")
            f.write("## Conclusion\n\n✅ CC PASS — py — 2024-01-01\n")
            f.flush()
            result = qs.score_artifact(Path(f.name))

        self.assertIn("score", result)
        self.assertIn("dimensions", result)
        self.assertGreaterEqual(result["score"], 50)
        Path(f.name).unlink()


class TestMcpInterface(unittest.TestCase):
    def test_file_not_found(self):
        result = qs.mcp_quality_score("/nonexistent/file.md")
        self.assertIn("error", result)

    def test_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test\n\n## Section\n\nContent.\n")
            f.flush()
            result = qs.mcp_quality_score(f.name)

        self.assertIn("score", result)
        self.assertNotIn("error", result)
        Path(f.name).unlink()


class TestDisplayHelpers(unittest.TestCase):
    def test_score_bar(self):
        bar = qs._score_bar(85)
        self.assertIn("🟢", bar)

        bar = qs._score_bar(65)
        self.assertIn("🟡", bar)

        bar = qs._score_bar(40)
        self.assertIn("🔴", bar)


if __name__ == "__main__":
    unittest.main()
