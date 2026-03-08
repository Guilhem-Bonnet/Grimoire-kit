#!/usr/bin/env python3
"""Tests pour schema-validator.py — Validateur structurel configs Grimoire."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))

try:
    import yaml as _yaml  # noqa: F401
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _import():
    import importlib
    return importlib.import_module("schema-validator")


def _write(root, relpath, content):
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── DNA validation ────────────────────────────────────────────────────────────

class TestValidateDNA(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_dna(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
$schema: "grimoire-archetype-dna/v1"
id: test
name: "Test"
version: "1.0.0"
description: "Test archetype"
icon: "🧪"
author: "test"
tags: [test]
inherits: minimal
""")
        data, err = self.mod._load_yaml(dna)
        self.assertIsNone(err)
        issues = self.mod.validate_dna(dna, data)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(len(errors), 0)

    def test_missing_required_fields(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
$schema: "grimoire-archetype-dna/v1"
id: test
""")
        data, err = self.mod._load_yaml(dna)
        self.assertIsNone(err)
        issues = self.mod.validate_dna(dna, data)
        missing = [i for i in issues if "manquant" in i.message]
        self.assertGreaterEqual(len(missing), 3)  # name, version, description, icon, author

    def test_empty_required_field(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
id: test
name: ""
version: "1.0.0"
description: "Test"
icon: "🧪"
author: "test"
""")
        data, err = self.mod._load_yaml(dna)
        self.assertIsNone(err)
        issues = self.mod.validate_dna(dna, data)
        empty = [i for i in issues if "vide" in i.message]
        self.assertGreaterEqual(len(empty), 1)

    def test_bad_enforcement_value(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
id: test
name: "Test"
version: "1.0.0"
description: "Test"
icon: "🧪"
author: "test"
constraints:
  - id: c1
    description: "test"
    enforcement: "invalid_value"
""")
        data, err = self.mod._load_yaml(dna)
        if err:
            self.skipTest("YAML parse needs PyYAML for nested structures")
        issues = self.mod.validate_dna(dna, data)
        enforcement_issues = [i for i in issues if "enforcement" in i.field]
        self.assertGreaterEqual(len(enforcement_issues), 1)

    def test_bad_version_format(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
id: test
name: "Test"
version: "1"
description: "Test"
icon: "🧪"
author: "test"
""")
        data, err = self.mod._load_yaml(dna)
        self.assertIsNone(err)
        issues = self.mod.validate_dna(dna, data)
        version_issues = [i for i in issues if "SemVer" in i.message]
        self.assertGreaterEqual(len(version_issues), 1)

    def test_confidence_boost_out_of_range(self):
        dna = _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
id: test
name: "Test"
version: "1.0.0"
description: "Test"
icon: "🧪"
author: "test"
auto_detect:
  confidence_boost: 150
""")
        data, err = self.mod._load_yaml(dna)
        if err:
            self.skipTest("Needs PyYAML")
        issues = self.mod.validate_dna(dna, data)
        boost_issues = [i for i in issues if "confidence_boost" in i.field]
        self.assertGreaterEqual(len(boost_issues), 1)


# ── Team manifest validation ─────────────────────────────────────────────────

class TestValidateTeam(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_team(self):
        team_file = _write(self.tmpdir, "framework/teams/team-test.yaml", """\
team:
  name: "team-test"
  display_name: "Test Team"
  version: "1.0.0"
  description: "A test team"
  agents:
    - name: dev
      role: Lead
""")
        data, err = self.mod._load_yaml(team_file)
        if err:
            self.skipTest("Needs PyYAML")
        issues = self.mod.validate_team(team_file, data)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(len(errors), 0)

    def test_missing_team_key(self):
        team_file = _write(self.tmpdir, "framework/teams/team-test.yaml", """\
name: "bad-format"
""")
        data, err = self.mod._load_yaml(team_file)
        self.assertIsNone(err)
        issues = self.mod.validate_team(team_file, data)
        self.assertTrue(any("team" in i.message.lower() for i in issues))

    def test_missing_nested_fields(self):
        team_file = _write(self.tmpdir, "framework/teams/team-test.yaml", """\
team:
  name: "team-test"
""")
        data, err = self.mod._load_yaml(team_file)
        if err:
            self.skipTest("Needs PyYAML")
        issues = self.mod.validate_team(team_file, data)
        missing = [i for i in issues if i.severity == "error"]
        self.assertGreaterEqual(len(missing), 2)


# ── Agent DNA validation ─────────────────────────────────────────────────────

class TestValidateAgentDNA(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_agent_dna(self):
        agent = _write(self.tmpdir, "stack/agents/test.dna.yaml", """\
id: test-agent
name: "Test Agent"
version: "1.0.0"
description: "A test agent"
""")
        data, err = self.mod._load_yaml(agent)
        self.assertIsNone(err)
        issues = self.mod.validate_agent_dna(agent, data)
        self.assertEqual(len(issues), 0)

    def test_missing_agent_fields(self):
        agent = _write(self.tmpdir, "stack/agents/test.dna.yaml", """\
id: test-agent
""")
        data, err = self.mod._load_yaml(agent)
        self.assertIsNone(err)
        issues = self.mod.validate_agent_dna(agent, data)
        self.assertGreaterEqual(len(issues), 2)


# ── File discovery ────────────────────────────────────────────────────────────

class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_dna_files(self):
        _write(self.tmpdir, "archetypes/a/archetype.dna.yaml", "id: a\nname: A\n")
        _write(self.tmpdir, "archetypes/b/archetype.dna.yaml", "id: b\nname: B\n")
        files = self.mod.discover_files(self.tmpdir, "dna")
        self.assertEqual(len(files), 2)
        self.assertTrue(all(t == "dna" for _, t in files))

    def test_discover_team_files(self):
        _write(self.tmpdir, "framework/teams/team-x.yaml", "team:\n  name: x\n")
        files = self.mod.discover_files(self.tmpdir, "team")
        self.assertEqual(len(files), 1)

    def test_discover_single_file(self):
        p = _write(self.tmpdir, "test.dna.yaml", "id: x\n")
        files = self.mod.discover_files(self.tmpdir, single_file=str(p))
        self.assertEqual(len(files), 1)

    def test_discover_empty(self):
        files = self.mod.discover_files(self.tmpdir)
        self.assertEqual(len(files), 0)

    def test_guess_type(self):
        self.assertEqual(self.mod._guess_type(Path("archetype.dna.yaml")), "dna")
        self.assertEqual(self.mod._guess_type(Path("team-build.yaml")), "team")
        self.assertEqual(self.mod._guess_type(Path("go-expert.dna.yaml")), "agent_dna")


# ── YAML loading ─────────────────────────────────────────────────────────────

class TestYAMLLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_yaml(self):
        p = _write(self.tmpdir, "test.yaml", "key: value\n")
        data, err = self.mod._load_yaml(p)
        self.assertIsNone(err)
        self.assertEqual(data["key"], "value")

    def test_empty_file(self):
        p = _write(self.tmpdir, "test.yaml", "")
        data, err = self.mod._load_yaml(p)
        self.assertIsNotNone(err)

    def test_nonexistent_file(self):
        data, err = self.mod._load_yaml(self.tmpdir / "nope.yaml")
        self.assertIsNotNone(err)

    def test_corrupted_yaml(self):
        p = _write(self.tmpdir, "test.yaml", "{{{{invalid yaml::::}")
        data, err = self.mod._load_yaml(p)
        # Either error from PyYAML or from basic parser
        if data is None:
            self.assertIsNotNone(err)

    def test_inline_list(self):
        p = _write(self.tmpdir, "test.yaml", "tags: [a, b, c]\nid: x\n")
        data, err = self.mod._load_yaml(p)
        self.assertIsNone(err)
        if isinstance(data.get("tags"), list):
            self.assertEqual(len(data["tags"]), 3)


# ── Orchestration ─────────────────────────────────────────────────────────────

class TestValidateAll(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_project(self):
        report = self.mod.validate_all(self.tmpdir)
        self.assertEqual(report.files_checked, 0)
        self.assertTrue(report.is_valid)

    def test_valid_project(self):
        _write(self.tmpdir, "archetypes/test/archetype.dna.yaml", """\
id: test
name: "Test"
version: "1.0.0"
description: "Test"
icon: "🧪"
author: "test"
""")
        report = self.mod.validate_all(self.tmpdir)
        self.assertEqual(report.files_checked, 1)
        self.assertTrue(report.is_valid)

    def test_real_project_validates(self):
        """Les vrais fichiers du kit doivent être valides."""
        report = self.mod.validate_all(KIT_DIR)
        self.assertTrue(report.is_valid,
                        f"Erreurs dans les configs du kit : "
                        f"{[(i.file, i.message) for i in report.issues if i.severity == 'error']}")


# ── Rendu ─────────────────────────────────────────────────────────────────────

class TestRendering(unittest.TestCase):
    def setUp(self):
        self.mod = _import()

    def test_render_clean(self):
        report = self.mod.ValidationReport(files_checked=5)
        text = self.mod.render_report(report)
        self.assertIn("✅", text)
        self.assertIn("5", text)

    def test_render_with_issues(self):
        report = self.mod.ValidationReport(
            files_checked=1,
            issues=[self.mod.ValidationIssue(
                severity="error", file="test.yaml",
                message="Missing field",
            )],
        )
        text = self.mod.render_report(report)
        self.assertIn("❌", text)
        self.assertIn("Missing field", text)

    def test_report_to_dict(self):
        report = self.mod.ValidationReport(files_checked=2)
        d = self.mod.report_to_dict(report)
        self.assertEqual(d["files_checked"], 2)
        self.assertTrue(d["valid"])
        self.assertEqual(d["errors"], 0)

    def test_data_class_properties(self):
        report = self.mod.ValidationReport(
            issues=[
                self.mod.ValidationIssue(severity="error"),
                self.mod.ValidationIssue(severity="warning"),
                self.mod.ValidationIssue(severity="warning"),
                self.mod.ValidationIssue(severity="info"),
            ],
        )
        self.assertEqual(report.error_count, 1)
        self.assertEqual(report.warning_count, 2)
        self.assertEqual(report.info_count, 1)
        self.assertFalse(report.is_valid)


if __name__ == "__main__":
    unittest.main()
