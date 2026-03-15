"""Tests for vision-judge.py — Vision Judge."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "vision-judge.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("vision_judge", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vision_judge"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "VISION_JUDGE_VERSION"))
        self.assertIsInstance(self.mod.VISION_JUDGE_VERSION, str)

    def test_rubrics(self):
        self.assertTrue(hasattr(self.mod, "RUBRICS"))
        self.assertIsInstance(self.mod.RUBRICS, dict)
        self.assertGreater(len(self.mod.RUBRICS), 0)


class TestCriterionScore(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "CriterionScore"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.CriterionScore))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.CriterionScore)}
        for name in ("name", "score", "weight", "feedback"):
            self.assertIn(name, fields)


class TestVisionVerdict(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "VisionVerdict"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.VisionVerdict))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.VisionVerdict)}
        for name in ("image_path", "rubric_used", "criteria_scores", "overall_score",
                      "decision", "feedback", "confidence"):
            self.assertIn(name, fields)


class TestComparisonResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ComparisonResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ComparisonResult))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ComparisonResult)}
        for name in ("before_score", "after_score", "improvement", "changes_detected", "recommendation"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_encode_image_base64(self):
        self.assertTrue(callable(getattr(self.mod, "encode_image_base64", None)))

    def test_detect_mime_type(self):
        self.assertTrue(callable(getattr(self.mod, "detect_mime_type", None)))

    def test_build_evaluation_prompt(self):
        self.assertTrue(callable(getattr(self.mod, "build_evaluation_prompt", None)))

    def test_parse_evaluation_response(self):
        self.assertTrue(callable(getattr(self.mod, "parse_evaluation_response", None)))

    def test_validate_svg_offline(self):
        self.assertTrue(callable(getattr(self.mod, "validate_svg_offline", None)))


if __name__ == "__main__":
    unittest.main()
