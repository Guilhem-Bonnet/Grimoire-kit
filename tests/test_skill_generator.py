"""Tests for grimoire.core.skill_generator."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.skill_generator import (
    FunctionInfo,
    ModuleInfo,
    SkillGenerator,
    _extract_module_info,
)

_SAMPLE_MODULE = '''\
"""Sample tool — does useful things.

Extended description for the module.
"""

from __future__ import annotations

SAMPLE_VERSION = "2.1.0"

class SampleTool:
    """A sample tool for testing."""

    def run(self, query: str) -> list[str]:
        """Execute the query."""
        return []

    def status(self) -> dict:
        """Return current status."""
        return {}

    def _internal(self) -> None:
        pass

def helper(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

def _private():
    pass
'''


class TestExtractModuleInfo(unittest.TestCase):
    def test_basic_extraction(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        self.assertEqual(info.module_name, "sample")
        self.assertIn("Sample tool", info.docstring)
        self.assertEqual(info.version, "2.1.0")

    def test_classes(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        self.assertIn("SampleTool", info.classes)

    def test_methods(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        method_names = [f.name for f in info.functions if f.is_method]
        self.assertIn("run", method_names)
        self.assertIn("status", method_names)
        # Should NOT include _internal
        self.assertNotIn("_internal", method_names)

    def test_top_level_functions(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        top = [f.name for f in info.functions if not f.is_method]
        self.assertIn("helper", top)
        self.assertNotIn("_private", top)

    def test_function_args(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        helper = next(f for f in info.functions if f.name == "helper")
        self.assertEqual(len(helper.args), 2)
        self.assertIn("x", helper.args[0])

    def test_return_type(self) -> None:
        info = _extract_module_info(_SAMPLE_MODULE, "sample", "sample.py")
        helper = next(f for f in info.functions if f.name == "helper")
        self.assertEqual(helper.return_type, "int")


class TestFunctionInfo(unittest.TestCase):
    def test_signature_display(self) -> None:
        fi = FunctionInfo(name="foo", docstring="doc", args=("x: int", "y: str"), return_type="bool")
        self.assertEqual(fi.signature_display(), "foo(x: int, y: str) -> bool")

    def test_signature_no_args(self) -> None:
        fi = FunctionInfo(name="bar", docstring="", args=(), return_type="")
        self.assertEqual(fi.signature_display(), "bar()")


class TestSkillGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.module_path = self.root / "tools" / "sample_tool.py"
        self.module_path.parent.mkdir(parents=True)
        self.module_path.write_text(_SAMPLE_MODULE)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_inspect(self) -> None:
        gen = SkillGenerator(self.root)
        info = gen.inspect("tools/sample_tool.py")
        self.assertIsInstance(info, ModuleInfo)
        self.assertEqual(info.module_name, "sample_tool")

    def test_inspect_absolute(self) -> None:
        gen = SkillGenerator(self.root)
        info = gen.inspect(self.module_path)
        self.assertEqual(info.module_name, "sample_tool")

    def test_inspect_not_found(self) -> None:
        gen = SkillGenerator(self.root)
        with self.assertRaises(FileNotFoundError):
            gen.inspect("nonexistent.py")

    def test_generate(self) -> None:
        gen = SkillGenerator(self.root)
        md = gen.generate("tools/sample_tool.py")
        self.assertIn("grimoire-sample-tool", md)
        self.assertIn("SampleTool", md)
        self.assertIn("2.1.0", md)
        self.assertIn("auto_generated: true", md)

    def test_generate_custom_name(self) -> None:
        gen = SkillGenerator(self.root)
        md = gen.generate("tools/sample_tool.py", skill_name="my-custom-skill")
        self.assertIn("my-custom-skill", md)

    def test_generate_has_sections(self) -> None:
        gen = SkillGenerator(self.root)
        md = gen.generate("tools/sample_tool.py")
        self.assertIn("## Triggers", md)
        self.assertIn("## API Reference", md)
        self.assertIn("## Process", md)
        self.assertIn("## Quality Checklist", md)

    def test_generate_and_save(self) -> None:
        gen = SkillGenerator(self.root)
        out = gen.generate_and_save("tools/sample_tool.py")
        self.assertTrue(out.exists())
        self.assertIn("SKILL.md", out.name)
        content = out.read_text()
        self.assertIn("grimoire-sample-tool", content)

    def test_generate_and_save_custom_dir(self) -> None:
        gen = SkillGenerator(self.root)
        out = gen.generate_and_save("tools/sample_tool.py", output_dir="custom-skills")
        self.assertIn("custom-skills", str(out))
        self.assertTrue(out.exists())

    def test_methods_exclude_self(self) -> None:
        gen = SkillGenerator(self.root)
        info = gen.inspect("tools/sample_tool.py")
        run_method = next(f for f in info.functions if f.name == "run")
        # "self" should be excluded from args
        for arg in run_method.args:
            self.assertNotIn("self", arg)


if __name__ == "__main__":
    unittest.main()
