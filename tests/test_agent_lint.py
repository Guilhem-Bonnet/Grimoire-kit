"""Tests for agent-lint.py — Agent Linter."""
from __future__ import annotations

import csv
import dataclasses
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-lint.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_lint", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_severity_class(self):
        self.assertTrue(hasattr(self.mod, "Severity"))


class TestFinding(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "Finding"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.Finding))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.Finding)}
        for name in ("severity", "rule", "message"):
            self.assertIn(name, fields)


class TestLintReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "LintReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.LintReport))


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_discover_agents(self):
        self.assertTrue(callable(getattr(self.mod, "discover_agents", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


class TestFrontmatterParsing(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_extract_frontmatter_array_supports_block_lists(self):
        content = (
            "---\n"
            "name: grimoire-master\n"
            "agents:\n"
            "  - dev\n"
            "  - qa\n"
            "user-invocable: true\n"
            "---\n"
        )

        self.assertEqual(
            self.mod.extract_frontmatter_array(content, "agents"),
            ["dev", "qa"],
        )


class TestSurfaceIndex(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-runtime" / "_config").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / ".github" / "agents" / "_archived").mkdir(parents=True, exist_ok=True)

        manifest_path = self.tmpdir / "_grimoire-runtime" / "_config" / "agent-manifest.csv"
        manifest_path.write_text(
            "name,displayName,title,icon,capabilities,role,identity,communicationStyle,principles,module,path\n"
            '"dev","Developer Agent","Developer","💻","impl","role","identity","style","principles","bmm","_grimoire-runtime/bmm/agents/dev.md"\n'
            '"grimoire-master","Grimoire Master","Master","🧙","sog","role","identity","style","principles","core","_grimoire-runtime/core/agents/grimoire-master.md"\n',
            encoding="utf-8",
        )

        (self.tmpdir / ".github" / "agents" / "dev.agent.md").write_text("---\nname: dev\n---\n", encoding="utf-8")
        (self.tmpdir / ".github" / "agents" / "bmad-master.agent.md").write_text("---\nname: bmad-master\n---\n", encoding="utf-8")
        (self.tmpdir / ".github" / "agents" / "_archived" / "dev.agent.md").write_text("---\nname: dev\n---\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_surface_index_classifies_records(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        records = self.mod.build_surface_index(self.tmpdir, manifest)
        by_name = {record.name: record for record in records}

        self.assertEqual(by_name["dev"].status, "active+archived")
        self.assertEqual(by_name["dev"].lookup_priority, 1)
        self.assertEqual(by_name["grimoire-master"].status, "runtime-only")
        self.assertEqual(by_name["bmad-master"].status, "workspace-only")

    def test_write_surface_index_and_update_files_manifest(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        records = self.mod.build_surface_index(self.tmpdir, manifest)
        index_path = self.mod.write_surface_index(self.tmpdir, records)
        files_manifest_path = self.mod.update_files_manifest(self.tmpdir, index_path)

        self.assertTrue(index_path.is_file())
        self.assertTrue(files_manifest_path.is_file())

        with index_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(any(row["name"] == "dev" for row in rows))

        with files_manifest_path.open(encoding="utf-8", newline="") as handle:
            manifest_rows = list(csv.DictReader(handle))
        self.assertTrue(any(row["path"] == self.mod.SURFACE_INDEX_CONFIG_PATH for row in manifest_rows))

    def test_lint_surface_index_sync_warns_when_missing(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        records = self.mod.build_surface_index(self.tmpdir, manifest)
        findings = self.mod.lint_surface_index_sync(self.tmpdir, records)
        self.assertTrue(any(f.rule == "surface-index-sync" for f in findings))

    def test_lint_surface_index_sync_clean_after_write(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        records = self.mod.build_surface_index(self.tmpdir, manifest)
        index_path = self.mod.write_surface_index(self.tmpdir, records)
        self.mod.update_files_manifest(self.tmpdir, index_path)
        findings = self.mod.lint_surface_index_sync(self.tmpdir, records)
        self.assertEqual(findings, [])


class TestWrapperSync(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        config_dir = self.tmpdir / "_grimoire-runtime" / "_config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (self.tmpdir / ".github" / "agents").mkdir(parents=True, exist_ok=True)

        manifest_path = config_dir / "agent-manifest.csv"
        manifest_path.write_text(
            "name,displayName,title,icon,capabilities,role,identity,communicationStyle,principles,module,path\n"
            '"dev","Developer Agent","Developer","💻","impl","role","identity","style","principles","bmm","_grimoire-runtime/bmm/agents/dev.md"\n',
            encoding="utf-8",
        )

        wrapper_spec = {
            "master": {
                "name": "grimoire-master",
                "description": "Master wrapper",
                "tools": ["read"],
                "userInvocable": True,
                "body": ["Follow the orchestrator."],
                "expectedAgents": ["dev"],
            },
            "wrappers": {
                "dev": {
                    "description": "Developer workspace wrapper",
                    "tools": ["read", "edit", "search"],
                    "summary": "Thin wrapper around Amelia.",
                }
            },
        }
        (config_dir / "agent-wrapper-spec.json").write_text(
            json.dumps(wrapper_spec, indent=2) + "\n",
            encoding="utf-8",
        )

        (self.tmpdir / ".github" / "agents" / "grimoire-master.agent.md").write_text(
            self.mod.render_master_wrapper(wrapper_spec),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_managed_wrappers_creates_expected_wrapper(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        written = self.mod.write_managed_wrappers(
            self.tmpdir,
            manifest,
            target_agent="dev",
        )

        self.assertEqual([path.name for path in written], ["dev.agent.md"])
        findings = self.mod.lint_wrapper_sync(
            self.tmpdir,
            manifest,
            target_agent="dev",
        )
        self.assertEqual(findings, [])

    def test_write_managed_wrappers_can_render_master_wrapper(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        written = self.mod.write_managed_wrappers(
            self.tmpdir,
            manifest,
            target_agent="grimoire-master",
        )

        self.assertEqual([path.name for path in written], ["grimoire-master.agent.md"])
        findings = self.mod.lint_wrapper_sync(
            self.tmpdir,
            manifest,
            target_agent="grimoire-master",
        )
        self.assertEqual(findings, [])

    def test_alias_wrapper_can_inherit_master_tools(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        wrapper_spec = json.loads(
            (self.tmpdir / "_grimoire-runtime" / "_config" / "agent-wrapper-spec.json").read_text(encoding="utf-8")
        )
        wrapper_spec["aliases"] = {
            "bmad-master": {
                "template": "alias",
                "description": "Legacy alias",
                "toolsFrom": "master",
                "agentsFrom": "master",
                "userInvocable": False,
                "body": ["Alias body."],
            }
        }
        (self.tmpdir / "_grimoire-runtime" / "_config" / "agent-wrapper-spec.json").write_text(
            json.dumps(wrapper_spec, indent=2) + "\n",
            encoding="utf-8",
        )

        written = self.mod.write_managed_wrappers(
            self.tmpdir,
            manifest,
            target_agent="bmad-master",
        )

        self.assertEqual([path.name for path in written], ["bmad-master.agent.md"])
        content = (self.tmpdir / ".github" / "agents" / "bmad-master.agent.md").read_text(encoding="utf-8")
        self.assertIn('tools: ["read"]', content)
        self.assertIn('agents: ["dev"]', content)

    def test_lint_wrapper_sync_flags_forbidden_master_tools(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        wrapper_spec = json.loads(
            (self.tmpdir / "_grimoire-runtime" / "_config" / "agent-wrapper-spec.json").read_text(encoding="utf-8")
        )
        wrapper_spec["master"]["tools"] = [
            "read",
            "github/create_pull_request",
            "gitkraken/git_push",
            "ms-python.python/installPythonPackage",
        ]
        (self.tmpdir / "_grimoire-runtime" / "_config" / "agent-wrapper-spec.json").write_text(
            json.dumps(wrapper_spec, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.tmpdir / ".github" / "agents" / "grimoire-master.agent.md").write_text(
            self.mod.render_master_wrapper(wrapper_spec),
            encoding="utf-8",
        )

        findings = self.mod.lint_wrapper_sync(
            self.tmpdir,
            manifest,
            target_agent="grimoire-master",
        )

        self.assertTrue(any(f.rule == "wrapper-master-tool-policy" for f in findings))

    def test_lint_wrapper_sync_warns_when_wrapper_drifts(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        self.mod.write_managed_wrappers(self.tmpdir, manifest, target_agent="dev")
        wrapper_path = self.tmpdir / ".github" / "agents" / "dev.agent.md"
        wrapper_path.write_text(
            wrapper_path.read_text(encoding="utf-8").replace(
                "Thin wrapper around Amelia.",
                "Manual drift.",
            ),
            encoding="utf-8",
        )

        findings = self.mod.lint_wrapper_sync(
            self.tmpdir,
            manifest,
            target_agent="dev",
        )
        self.assertTrue(any(f.rule == "wrapper-sync" for f in findings))

    def test_lint_wrapper_sync_warns_when_master_wrapper_drifts(self):
        manifest = self.mod.load_manifest(self.tmpdir)
        master_path = self.tmpdir / ".github" / "agents" / "grimoire-master.agent.md"
        master_path.write_text(
            master_path.read_text(encoding="utf-8").replace(
                "Follow the orchestrator.",
                "Manual drift.",
            ),
            encoding="utf-8",
        )

        findings = self.mod.lint_wrapper_sync(
            self.tmpdir,
            manifest,
            target_agent="grimoire-master",
        )
        self.assertTrue(any(f.rule == "wrapper-master-sync" for f in findings))


if __name__ == "__main__":
    unittest.main()
