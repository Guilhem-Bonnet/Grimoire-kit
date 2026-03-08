#!/usr/bin/env python3
"""
Tests pour cross-migrate.py — Grimoire Cross-Project Migration.

Fonctions testées :
  - export_learnings()
  - export_rules()
  - export_dna_patches()
  - export_agents()
  - export_consensus()
  - export_antifragile()
  - create_bundle()
  - save_bundle() / load_bundle()
  - import_bundle()
  - render_inspect()
  - render_import_result()
  - render_diff()
  - BundleManifest, ExportedLearning, ExportedRule, MigrationBundle dataclasses
"""

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_cm():
    return importlib.import_module("cross-migrate")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_project_tree(root: Path, learnings=None, failures=None,
                         dna_proposals=None, forge_proposals=None,
                         consensus=None, antifragile=None,
                         project_context=None):
    """Créer un arbre projet minimal pour les tests."""
    mem = root / "_grimoire" / "_memory"
    mem.mkdir(parents=True, exist_ok=True)
    out = root / "_grimoire-output"
    out.mkdir(parents=True, exist_ok=True)

    if learnings:
        ld = mem / "agent-learnings"
        ld.mkdir(exist_ok=True)
        for name, content in learnings.items():
            (ld / name).write_text(content, encoding="utf-8")

    if failures:
        (mem / "failure-museum.md").write_text(failures, encoding="utf-8")

    if dna_proposals:
        dp = out / "dna-proposals"
        dp.mkdir(exist_ok=True)
        for name, content in dna_proposals.items():
            (dp / name).write_text(content, encoding="utf-8")

    if forge_proposals:
        fp = out / "forge-proposals"
        fp.mkdir(exist_ok=True)
        for name, content in forge_proposals.items():
            (fp / name).write_text(content, encoding="utf-8")

    if consensus is not None:
        (out / "consensus-history.json").write_text(
            json.dumps(consensus), encoding="utf-8")

    if antifragile is not None:
        (out / "antifragile-history.json").write_text(
            json.dumps(antifragile), encoding="utf-8")

    if project_context:
        (root / "project-context.yaml").write_text(
            project_context, encoding="utf-8")


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ── ExportedLearning / ExportedRule dataclass tests ──────────────────────────

class TestExportedLearning(BaseTest):
    def test_to_dict(self):
        cm = _import_cm()
        learning = cm.ExportedLearning(agent="dev", text="test learning",
                                       date="2026-01-01")
        d = learning.to_dict()
        self.assertEqual(d["agent"], "dev")
        self.assertEqual(d["text"], "test learning")
        self.assertEqual(d["date"], "2026-01-01")

    def test_from_dict(self):
        cm = _import_cm()
        d = {"agent": "qa", "text": "qa learning", "date": "2026-02-01"}
        learning = cm.ExportedLearning.from_dict(d)
        self.assertEqual(learning.agent, "qa")
        self.assertEqual(learning.text, "qa learning")

    def test_from_dict_missing_fields(self):
        cm = _import_cm()
        learning = cm.ExportedLearning.from_dict({})
        self.assertEqual(learning.agent, "")
        self.assertEqual(learning.text, "")


class TestExportedRule(BaseTest):
    def test_to_dict(self):
        cm = _import_cm()
        r = cm.ExportedRule(category="CC-FAIL", rule="Always verify",
                            lesson="Double check", date="2026-01-15")
        d = r.to_dict()
        self.assertEqual(d["category"], "CC-FAIL")
        self.assertEqual(d["rule"], "Always verify")

    def test_from_dict(self):
        cm = _import_cm()
        r = cm.ExportedRule.from_dict({"category": "HALLUCINATION",
                                        "rule": "Vérifier les faits"})
        self.assertEqual(r.category, "HALLUCINATION")
        self.assertEqual(r.lesson, "")


# ── BundleManifest tests ─────────────────────────────────────────────────────

class TestBundleManifest(BaseTest):
    def test_defaults(self):
        cm = _import_cm()
        m = cm.BundleManifest()
        self.assertEqual(m.version, "1.0.0")
        self.assertEqual(m.magic, "grimoire-bundle")
        self.assertEqual(m.artifact_types, [])

    def test_total_items(self):
        cm = _import_cm()
        m = cm.BundleManifest(total_items=42)
        self.assertEqual(m.total_items, 42)


# ── MigrationBundle tests ────────────────────────────────────────────────────

class TestMigrationBundle(BaseTest):
    def test_to_dict_empty(self):
        cm = _import_cm()
        b = cm.MigrationBundle(manifest=cm.BundleManifest())
        d = b.to_dict()
        self.assertEqual(d["manifest"]["magic"], "grimoire-bundle")
        self.assertEqual(d["learnings"], [])
        self.assertEqual(d["rules"], [])

    def test_roundtrip(self):
        cm = _import_cm()
        b = cm.MigrationBundle(
            manifest=cm.BundleManifest(source_project="test-proj"),
            learnings=[cm.ExportedLearning("dev", "test", "2026-01-01")],
            rules=[cm.ExportedRule("CC-FAIL", "rule1")],
        )
        d = b.to_dict()
        b2 = cm.MigrationBundle.from_dict(d)
        self.assertEqual(b2.manifest.source_project, "test-proj")
        self.assertEqual(len(b2.learnings), 1)
        self.assertEqual(len(b2.rules), 1)


# ── export_learnings tests ───────────────────────────────────────────────────

class TestExportLearnings(BaseTest):
    def test_empty_dir(self):
        cm = _import_cm()
        result = cm.export_learnings(self.root)
        self.assertEqual(result, [])

    def test_no_dir(self):
        cm = _import_cm()
        result = cm.export_learnings(self.root / "nonexistent")
        self.assertEqual(result, [])

    def test_basic_learnings(self):
        cm = _import_cm()
        _create_project_tree(self.root, learnings={
            "dev.md": "# Dev learnings\n- [2026-01-15] Always test first\n- Use type hints\n",
            "qa.md": "# QA learnings\n- [2026-02-01] Coverage matters\n",
        })
        result = cm.export_learnings(self.root)
        self.assertEqual(len(result), 3)
        agents = {entry.agent for entry in result}
        self.assertIn("dev", agents)
        self.assertIn("qa", agents)

    def test_learnings_with_since(self):
        cm = _import_cm()
        _create_project_tree(self.root, learnings={
            "dev.md": "- [2025-12-01] Old learning\n- [2026-03-01] New learning\n",
        })
        result = cm.export_learnings(self.root, since="2026-01-01")
        self.assertEqual(len(result), 1)
        self.assertIn("New learning", result[0].text)

    def test_learnings_skip_headers(self):
        cm = _import_cm()
        _create_project_tree(self.root, learnings={
            "dev.md": "# Header\n\n## Section\n- actual learning\n",
        })
        result = cm.export_learnings(self.root)
        self.assertEqual(len(result), 1)

    def test_learnings_empty_file(self):
        cm = _import_cm()
        _create_project_tree(self.root, learnings={
            "dev.md": "",
        })
        result = cm.export_learnings(self.root)
        self.assertEqual(result, [])


# ── export_rules tests ───────────────────────────────────────────────────────

class TestExportRules(BaseTest):
    def test_no_failure_museum(self):
        cm = _import_cm()
        result = cm.export_rules(self.root)
        self.assertEqual(result, [])

    def test_basic_rules(self):
        cm = _import_cm()
        failures = (
            "# Failure Museum\n\n"
            "### [2026-01-10] CC-FAIL — Wrong import\n"
            "- Règle instaurée : Always check imports\n"
            "- Leçon : Double check before commit\n\n"
            "### [2026-02-05] HALLUCINATION — Made up API\n"
            "- Règle instaurée : Verify API existence\n"
        )
        _create_project_tree(self.root, failures=failures)
        result = cm.export_rules(self.root)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].category, "CC-FAIL")
        self.assertEqual(result[0].rule, "Always check imports")
        self.assertEqual(result[0].lesson, "Double check before commit")
        self.assertEqual(result[1].category, "HALLUCINATION")

    def test_rules_with_since(self):
        cm = _import_cm()
        failures = (
            "### [2025-06-01] CC-FAIL — Old\n"
            "- Règle instaurée : Old rule\n\n"
            "### [2026-03-01] ARCH-MISTAKE — New\n"
            "- Règle instaurée : New rule\n"
        )
        _create_project_tree(self.root, failures=failures)
        result = cm.export_rules(self.root, since="2026-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].rule, "New rule")

    def test_rules_no_category_match(self):
        cm = _import_cm()
        failures = (
            "### [2026-01-01] UNKNOWN-TYPE — Something\n"
            "- Règle instaurée : Some rule\n"
        )
        _create_project_tree(self.root, failures=failures)
        result = cm.export_rules(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "UNKNOWN")


# ── export_dna_patches tests ─────────────────────────────────────────────────

class TestExportDnaPatches(BaseTest):
    def test_no_dir(self):
        cm = _import_cm()
        result = cm.export_dna_patches(self.root)
        self.assertEqual(result, [])

    def test_basic_patches(self):
        cm = _import_cm()
        _create_project_tree(self.root, dna_proposals={
            "patch-001.yaml": "mutation: add_tool\ntool: linter",
        })
        result = cm.export_dna_patches(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "patch-001.yaml")
        self.assertIn("mutation", result[0]["content"])


# ── export_agents tests ──────────────────────────────────────────────────────

class TestExportAgents(BaseTest):
    def test_no_dir(self):
        cm = _import_cm()
        result = cm.export_agents(self.root)
        self.assertEqual(result, [])

    def test_basic_agents(self):
        cm = _import_cm()
        _create_project_tree(self.root, forge_proposals={
            "linter-agent.proposed.md": "# Linter Agent\nDoes linting.",
        })
        result = cm.export_agents(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "linter-agent.proposed.md")


# ── export_consensus / export_antifragile tests ──────────────────────────────

class TestExportConsensus(BaseTest):
    def test_no_file(self):
        cm = _import_cm()
        self.assertEqual(cm.export_consensus(self.root), [])

    def test_basic(self):
        cm = _import_cm()
        data = [{"timestamp": "2026-01-01T00:00:00", "decision": "go"}]
        _create_project_tree(self.root, consensus=data)
        result = cm.export_consensus(self.root)
        self.assertEqual(len(result), 1)

    def test_invalid_json(self):
        cm = _import_cm()
        out = self.root / "_grimoire-output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "consensus-history.json").write_text("not json")
        self.assertEqual(cm.export_consensus(self.root), [])


class TestExportAntifragile(BaseTest):
    def test_no_file(self):
        cm = _import_cm()
        self.assertEqual(cm.export_antifragile(self.root), [])

    def test_basic(self):
        cm = _import_cm()
        data = [{"timestamp": "2026-01-01", "score": 75}]
        _create_project_tree(self.root, antifragile=data)
        result = cm.export_antifragile(self.root)
        self.assertEqual(len(result), 1)


# ── create_bundle tests ──────────────────────────────────────────────────────

class TestCreateBundle(BaseTest):
    def test_empty_project(self):
        cm = _import_cm()
        bundle = cm.create_bundle(self.root)
        self.assertEqual(bundle.manifest.total_items, 0)
        self.assertEqual(bundle.manifest.artifact_types, [])

    def test_full_bundle(self):
        cm = _import_cm()
        _create_project_tree(
            self.root,
            learnings={"dev.md": "- Learning 1\n- Learning 2\n"},
            failures=(
                "### [2026-01-01] CC-FAIL — test\n"
                "- Règle instaurée : test rule\n"
            ),
            consensus=[{"timestamp": "T1", "d": "ok"}],
            project_context='name: "test-project"\n',
        )
        bundle = cm.create_bundle(self.root)
        self.assertGreater(bundle.manifest.total_items, 0)
        self.assertEqual(bundle.manifest.source_project, "test-project")
        self.assertIn("learnings", bundle.manifest.artifact_types)

    def test_only_filter(self):
        cm = _import_cm()
        _create_project_tree(
            self.root,
            learnings={"dev.md": "- Learning 1\n"},
            failures=(
                "### [2026-01-01] CC-FAIL — test\n"
                "- Règle instaurée : test rule\n"
            ),
        )
        bundle = cm.create_bundle(self.root, only={"learnings"})
        self.assertGreater(len(bundle.learnings), 0)
        self.assertEqual(len(bundle.rules), 0)

    def test_since_filter(self):
        cm = _import_cm()
        _create_project_tree(
            self.root,
            learnings={"dev.md": "- [2025-01-01] Old\n- [2026-06-01] New\n"},
        )
        bundle = cm.create_bundle(self.root, since="2026-01-01")
        self.assertEqual(len(bundle.learnings), 1)


# ── save_bundle / load_bundle tests ──────────────────────────────────────────

class TestSaveLoadBundle(BaseTest):
    def test_roundtrip(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(source_project="round-trip"),
            learnings=[cm.ExportedLearning("dev", "test", "2026-01-01")],
        )
        path = self.root / "test-bundle.json"
        cm.save_bundle(bundle, path)
        self.assertTrue(path.exists())

        loaded = cm.load_bundle(path)
        self.assertEqual(loaded.manifest.source_project, "round-trip")
        self.assertEqual(len(loaded.learnings), 1)

    def test_load_invalid_magic(self):
        cm = _import_cm()
        path = self.root / "bad.json"
        path.write_text(json.dumps({"manifest": {"magic": "wrong"}}))
        with self.assertRaises(ValueError):
            cm.load_bundle(path)

    def test_save_creates_directories(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(manifest=cm.BundleManifest())
        path = self.root / "deep" / "nested" / "bundle.json"
        cm.save_bundle(bundle, path)
        self.assertTrue(path.exists())


# ── import_bundle tests ──────────────────────────────────────────────────────

class TestImportBundle(BaseTest):
    def test_import_learnings(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            learnings=[
                cm.ExportedLearning("dev", "imported learning", "2026-01-01"),
                cm.ExportedLearning("qa", "qa learning", "2026-02-01"),
            ],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.learnings_imported, 2)

        # Verify files created
        dev_file = self.root / "_grimoire" / "_memory" / "agent-learnings" / "dev.md"
        self.assertTrue(dev_file.exists())
        content = dev_file.read_text()
        self.assertIn("migré", content)
        self.assertIn("imported learning", content)

    def test_import_learnings_dedup(self):
        cm = _import_cm()
        # Pre-existing learning
        _create_project_tree(self.root, learnings={
            "dev.md": "- [migré] existing learning\n",
        })
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            learnings=[
                cm.ExportedLearning("dev", "existing learning", ""),
                cm.ExportedLearning("dev", "new learning", ""),
            ],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.learnings_imported, 1)
        self.assertEqual(result.skipped, 1)

    def test_import_rules(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(source_project="src"),
            rules=[cm.ExportedRule("CC-FAIL", "Always test", "", "2026-01-01")],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.rules_imported, 1)
        rules_path = self.root / "_grimoire" / "_memory" / "migrated-rules.md"
        self.assertTrue(rules_path.exists())

    def test_import_rules_dedup(self):
        cm = _import_cm()
        # Pre-create rules file
        mem = self.root / "_grimoire" / "_memory"
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "migrated-rules.md").write_text(
            "- [2026-01-01] [CC-FAIL] Règle: Always test\n")

        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            rules=[cm.ExportedRule("CC-FAIL", "Always test", "", "2026-01-01")],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.rules_imported, 0)
        self.assertEqual(result.skipped, 1)

    def test_import_dna_patches(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            dna_patches=[{"filename": "p1.yaml", "content": "mutation: test"}],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.dna_patches_imported, 1)
        target = self.root / "_grimoire-output" / "dna-proposals" / "migrated" / "p1.yaml"
        self.assertTrue(target.exists())

    def test_import_dna_patches_conflict(self):
        cm = _import_cm()
        # Pre-create the file
        d = self.root / "_grimoire-output" / "dna-proposals" / "migrated"
        d.mkdir(parents=True, exist_ok=True)
        (d / "p1.yaml").write_text("existing")

        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            dna_patches=[{"filename": "p1.yaml", "content": "new"}],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.dna_patches_imported, 0)
        self.assertEqual(result.skipped, 1)
        self.assertGreater(len(result.conflicts), 0)

    def test_import_agents(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            agents=[{"filename": "a.proposed.md", "content": "# Agent"}],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.agents_imported, 1)

    def test_import_consensus_merge(self):
        cm = _import_cm()
        _create_project_tree(self.root, consensus=[
            {"timestamp": "T1", "d": "existing"},
        ])
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            consensus=[
                {"timestamp": "T1", "d": "dupe"},
                {"timestamp": "T2", "d": "new"},
            ],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.consensus_imported, 1)
        self.assertEqual(result.skipped, 1)

    def test_import_antifragile_merge(self):
        cm = _import_cm()
        _create_project_tree(self.root, antifragile=[
            {"timestamp": "AF1", "score": 50},
        ])
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            antifragile=[
                {"timestamp": "AF1", "score": 50},
                {"timestamp": "AF2", "score": 75},
            ],
        )
        result = cm.import_bundle(bundle, self.root)
        self.assertEqual(result.antifragile_imported, 1)

    def test_import_dry_run(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            learnings=[cm.ExportedLearning("dev", "test", "2026-01-01")],
            rules=[cm.ExportedRule("CC-FAIL", "rule", "", "2026-01-01")],
        )
        result = cm.import_bundle(bundle, self.root, dry_run=True)
        self.assertEqual(result.learnings_imported, 1)
        self.assertEqual(result.rules_imported, 1)

        # But nothing written to disk
        dev_file = self.root / "_grimoire" / "_memory" / "agent-learnings" / "dev.md"
        self.assertFalse(dev_file.exists())

    def test_import_result_total(self):
        cm = _import_cm()
        r = cm.ImportResult(learnings_imported=3, rules_imported=2,
                            dna_patches_imported=1)
        self.assertEqual(r.total, 6)


# ── render tests ─────────────────────────────────────────────────────────────

class TestRender(BaseTest):
    def test_render_inspect_empty(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(manifest=cm.BundleManifest(
            source_project="test", export_date="2026-01-01T00:00"))
        text = cm.render_inspect(bundle)
        self.assertIn("Grimoire Migration Bundle", text)
        self.assertIn("test", text)

    def test_render_inspect_with_data(self):
        cm = _import_cm()
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(
                source_project="proj",
                export_date="2026-01-01T00:00",
                artifact_types=["learnings"],
                total_items=2),
            learnings=[
                cm.ExportedLearning("dev", "l1"),
                cm.ExportedLearning("dev", "l2"),
            ],
        )
        text = cm.render_inspect(bundle)
        self.assertIn("Learnings (2)", text)
        self.assertIn("dev", text)

    def test_render_import_result(self):
        cm = _import_cm()
        result = cm.ImportResult(learnings_imported=5, skipped=2)
        text = cm.render_import_result(result)
        self.assertIn("5", text)
        self.assertIn("Import terminé", text)

    def test_render_import_result_dry_run(self):
        cm = _import_cm()
        result = cm.ImportResult(learnings_imported=3)
        text = cm.render_import_result(result, dry_run=True)
        self.assertIn("DRY RUN", text)

    def test_render_import_result_conflicts(self):
        cm = _import_cm()
        result = cm.ImportResult(conflicts=["file already exists"])
        text = cm.render_import_result(result)
        self.assertIn("Conflits", text)

    def test_render_diff(self):
        cm = _import_cm()
        _create_project_tree(self.root, learnings={
            "dev.md": "- existing learning\n",
        })
        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            learnings=[
                cm.ExportedLearning("dev", "existing learning"),
                cm.ExportedLearning("dev", "new learning"),
            ],
        )
        text = cm.render_diff(bundle, self.root)
        self.assertIn("Diff", text)
        self.assertIn("1 nouveaux", text)


# ── _get_project_name tests ─────────────────────────────────────────────────

class TestGetProjectName(BaseTest):
    def test_from_project_context(self):
        cm = _import_cm()
        _create_project_tree(self.root,
                             project_context='name: "my-awesome-project"\n')
        name = cm._get_project_name(self.root)
        self.assertEqual(name, "my-awesome-project")

    def test_fallback_to_dirname(self):
        cm = _import_cm()
        name = cm._get_project_name(self.root)
        self.assertEqual(name, self.root.name)


# ── _parse_date_from_line tests ──────────────────────────────────────────────

class TestParseDateFromLine(BaseTest):
    def test_bracketed_date(self):
        cm = _import_cm()
        self.assertEqual(cm._parse_date_from_line("[2026-01-15] Something"),
                         "2026-01-15")

    def test_unbracketed_date(self):
        cm = _import_cm()
        self.assertEqual(cm._parse_date_from_line("2026-03-20 text"),
                         "2026-03-20")

    def test_no_date(self):
        cm = _import_cm()
        self.assertEqual(cm._parse_date_from_line("no date here"), "")


# ── End-to-end workflow tests ────────────────────────────────────────────────

class TestE2EWorkflow(BaseTest):
    def test_export_import_roundtrip(self):
        """Full export → save → load → import workflow."""
        cm = _import_cm()

        # Source project
        src = self.root / "source"
        _create_project_tree(
            src,
            learnings={
                "dev.md": "- [2026-01-01] Test-driven dev\n",
                "qa.md": "- [2026-01-15] Coverage > 80%\n",
            },
            failures=(
                "### [2026-01-20] CC-FAIL — Missing guard\n"
                "- Règle instaurée : Always add guard clauses\n"
                "- Leçon : Check edge cases\n"
            ),
            project_context='name: "source-project"\n',
        )

        # Export
        bundle = cm.create_bundle(src)
        self.assertGreater(bundle.manifest.total_items, 0)
        self.assertEqual(bundle.manifest.source_project, "source-project")

        # Save
        bundle_path = self.root / "exported.json"
        cm.save_bundle(bundle, bundle_path)

        # Load
        loaded = cm.load_bundle(bundle_path)
        self.assertEqual(loaded.manifest.source_project, "source-project")
        self.assertEqual(len(loaded.learnings), len(bundle.learnings))

        # Import into target
        target = self.root / "target"
        target.mkdir()
        result = cm.import_bundle(loaded, target)
        self.assertGreater(result.total, 0)

    def test_double_import_deduplicates(self):
        """Importing the same bundle twice should skip duplicates."""
        cm = _import_cm()

        bundle = cm.MigrationBundle(
            manifest=cm.BundleManifest(),
            learnings=[cm.ExportedLearning("dev", "unique learning", "2026-01-01")],
        )

        target = self.root / "target"
        target.mkdir()

        # First import
        r1 = cm.import_bundle(bundle, target)
        self.assertEqual(r1.learnings_imported, 1)

        # Second import
        r2 = cm.import_bundle(bundle, target)
        self.assertEqual(r2.learnings_imported, 0)
        self.assertEqual(r2.skipped, 1)


if __name__ == "__main__":
    unittest.main()
