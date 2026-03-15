"""Tests for framework/tools/gen-tests.py — BM-29 test scaffolding."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "framework" / "tools"


def _import_gen():
    spec = importlib.util.spec_from_file_location("gen_tests", TOOLS_DIR / "gen-tests.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestToSnake(unittest.TestCase):
    """Test to_snake() utility."""

    def setUp(self):
        self.gen = _import_gen()

    def test_basic(self):
        self.assertEqual(self.gen.to_snake("Hello World"), "hello_world")

    def test_dashes(self):
        self.assertEqual(self.gen.to_snake("tdd-mandatory"), "tdd_mandatory")

    def test_colons_stripped(self):
        self.assertNotIn(":", self.gen.to_snake("AC:001"))

    def test_slashes(self):
        self.assertNotIn("/", self.gen.to_snake("src/test/something"))

    def test_truncated_at_60(self):
        result = self.gen.to_snake("a" * 100)
        self.assertLessEqual(len(result), 60)


class TestToPascal(unittest.TestCase):
    """Test to_pascal() utility."""

    def setUp(self):
        self.gen = _import_gen()

    def test_basic(self):
        self.assertEqual(self.gen.to_pascal("hello world"), "HelloWorld")

    def test_dashes(self):
        self.assertEqual(self.gen.to_pascal("tdd-mandatory"), "TddMandatory")

    def test_truncated_at_60(self):
        result = self.gen.to_pascal("a-" * 50)
        self.assertLessEqual(len(result), 60)


class TestLoadDna(unittest.TestCase):
    """Test load_dna()."""

    def setUp(self):
        self.gen = _import_gen()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_yaml(self):
        f = self.tmpdir / "test.dna.yaml"
        f.write_text("name: test-dna\nversion: '1.0.0'\ntraits: []\n")
        dna = self.gen.load_dna(str(f))
        self.assertEqual(dna["name"], "test-dna")

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            self.gen.load_dna(str(self.tmpdir / "nonexistent.yaml"))

    def test_invalid_format_exits(self):
        f = self.tmpdir / "bad.yaml"
        f.write_text("just a plain string\n")
        with self.assertRaises(SystemExit):
            self.gen.load_dna(str(f))


class TestExtractAcItems(unittest.TestCase):
    """Test extract_ac_items() from various DNA structures."""

    def setUp(self):
        self.gen = _import_gen()

    def test_empty_dna(self):
        items = self.gen.extract_ac_items({})
        self.assertEqual(len(items), 0)

    def test_root_level_ac(self):
        dna = {
            "name": "test",
            "acceptance_criteria": [
                {"id": "ac-001", "description": "Must pass", "enforcement": "hard"},
            ],
        }
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "ac-001")
        self.assertEqual(items[0]["source"], "root")

    def test_trait_level_ac(self):
        dna = {
            "traits": [
                {
                    "name": "tdd",
                    "acceptance_criteria": [
                        {"id": "test-first", "description": "Write tests first"},
                    ],
                },
            ],
        }
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "trait")
        self.assertEqual(items[0]["trait_name"], "tdd")

    def test_constraints_mapped_as_hard(self):
        dna = {
            "constraints": [
                {"id": "no-secrets", "description": "No secrets in code"},
            ],
        }
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["enforcement"], "hard")
        self.assertEqual(items[0]["source"], "constraint")

    def test_mixed_sources(self):
        dna = {
            "acceptance_criteria": [{"id": "root-ac", "description": "Root AC"}],
            "traits": [
                {"name": "t1", "acceptance_criteria": [{"id": "trait-ac", "description": "Trait AC"}]},
            ],
            "constraints": [{"id": "c1", "description": "Constraint"}],
        }
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(len(items), 3)
        sources = {i["source"] for i in items}
        self.assertEqual(sources, {"root", "trait", "constraint"})

    def test_default_enforcement_is_hard(self):
        dna = {"acceptance_criteria": [{"description": "No explicit enforcement"}]}
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(items[0]["enforcement"], "hard")

    def test_soft_enforcement_preserved(self):
        dna = {"acceptance_criteria": [{"description": "Soft", "enforcement": "soft"}]}
        items = self.gen.extract_ac_items(dna)
        self.assertEqual(items[0]["enforcement"], "soft")


class TestGenerateTests(unittest.TestCase):
    """Test generate_tests() for all supported frameworks."""

    def setUp(self):
        self.gen = _import_gen()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dna = {
            "name": "test-archetype",
            "version": "1.0.0",
            "traits": [
                {
                    "name": "tdd-mandatory",
                    "description": "TDD trait",
                    "acceptance_criteria": [
                        {"id": "test-first", "description": "Tests before code", "enforcement": "hard"},
                        {"id": "coverage-80", "description": "Coverage above 80%", "enforcement": "soft"},
                    ],
                },
            ],
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pytest_generates_py_files(self):
        files = self.gen.generate_tests(self.dna, "pytest", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 1)
        self.assertTrue(str(files[0]).endswith(".py"))
        content = Path(files[0]).read_text()
        self.assertIn("def test_", content)
        self.assertIn("test-archetype", content)

    def test_jest_generates_ts_files(self):
        files = self.gen.generate_tests(self.dna, "jest", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 1)
        self.assertTrue(str(files[0]).endswith(".test.ts"))
        content = Path(files[0]).read_text()
        self.assertIn("describe(", content)

    def test_bats_generates_bats_files(self):
        files = self.gen.generate_tests(self.dna, "bats", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 1)
        self.assertTrue(str(files[0]).endswith(".bats"))
        content = Path(files[0]).read_text()
        self.assertIn("@test", content)

    def test_go_test_generates_go_files(self):
        files = self.gen.generate_tests(self.dna, "go-test", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 1)
        self.assertTrue(str(files[0]).endswith("_test.go"))
        content = Path(files[0]).read_text()
        self.assertIn("func Test_", content)

    def test_unsupported_framework_exits(self):
        with self.assertRaises(SystemExit):
            self.gen.generate_tests(self.dna, "unknown", str(self.tmpdir), "test.dna.yaml")

    def test_empty_ac_returns_empty(self):
        dna_no_ac = {"name": "empty", "traits": [{"name": "t1"}]}
        files = self.gen.generate_tests(dna_no_ac, "pytest", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 0)

    def test_creates_output_dir(self):
        out = str(self.tmpdir / "nested" / "deep")
        self.gen.generate_tests(self.dna, "pytest", out, "test.dna.yaml")
        self.assertTrue(Path(out).is_dir())

    def test_multiple_traits_multiple_files(self):
        dna = {
            "name": "multi",
            "version": "1.0.0",
            "traits": [
                {"name": "trait-a", "acceptance_criteria": [{"id": "a1", "description": "test a"}]},
                {"name": "trait-b", "acceptance_criteria": [{"id": "b1", "description": "test b"}]},
            ],
        }
        files = self.gen.generate_tests(dna, "pytest", str(self.tmpdir), "test.dna.yaml")
        self.assertEqual(len(files), 2)

    def test_soft_enforcement_pytest_xfail(self):
        dna = {
            "name": "soft-test",
            "version": "1.0.0",
            "acceptance_criteria": [
                {"id": "soft-ac", "description": "Soft check", "enforcement": "soft"},
            ],
        }
        files = self.gen.generate_tests(dna, "pytest", str(self.tmpdir), "test.dna.yaml")
        content = Path(files[0]).read_text()
        self.assertIn("xfail", content)


class TestTemplates(unittest.TestCase):
    """Test TEMPLATES structure."""

    def setUp(self):
        self.gen = _import_gen()

    def test_all_templates_have_ext(self):
        for name, tmpl in self.gen.TEMPLATES.items():
            self.assertIn("ext", tmpl, f"Template {name} missing 'ext'")

    def test_all_templates_have_header(self):
        for name, tmpl in self.gen.TEMPLATES.items():
            self.assertIn("header", tmpl, f"Template {name} missing 'header'")

    def test_all_templates_have_test_func(self):
        for name, tmpl in self.gen.TEMPLATES.items():
            self.assertIn("test_func", tmpl, f"Template {name} missing 'test_func'")

    def test_minimum_4_frameworks(self):
        self.assertGreaterEqual(len(self.gen.TEMPLATES), 4)


if __name__ == "__main__":
    unittest.main()
