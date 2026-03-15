#!/usr/bin/env python3
"""
Tests pour agent-forge.py — génération de scaffold d'agents.

Fonctions testées :
  - detect_domain()
  - extract_agent_name()
  - scan_gaps_from_shared_context()
  - scan_gaps_from_trace()
  - list_existing_agents()
  - check_overlap()
  - read_project_context()
  - read_active_dna()
  - AgentProposal dataclass
  - DOMAIN_TAXONOMY completeness
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_forge():
    return importlib.import_module("agent-forge")


class TestDetectDomain(unittest.TestCase):
    """Test detect_domain() — text-to-domain matching."""

    def setUp(self):
        self.forge = _import_forge()

    def test_database_domain(self):
        key, profile = self.forge.detect_domain("je veux un agent pour les migrations PostgreSQL")
        self.assertEqual(key, "database")
        self.assertIn("🗄", profile["icon"])

    def test_security_domain(self):
        key, _profile = self.forge.detect_domain("audit de sécurité et scan de vulnérabilités")
        self.assertEqual(key, "security")

    def test_frontend_domain(self):
        key, _profile = self.forge.detect_domain("I need a React component review agent")
        self.assertEqual(key, "frontend")

    def test_api_domain(self):
        key, _profile = self.forge.detect_domain("gestion des endpoints REST et contrats OpenAPI")
        self.assertEqual(key, "api")

    def test_testing_domain(self):
        key, _profile = self.forge.detect_domain("couverture de tests e2e avec Playwright")
        self.assertEqual(key, "testing")

    def test_devops_domain(self):
        key, _profile = self.forge.detect_domain("pipeline CI/CD GitHub Actions")
        self.assertEqual(key, "devops")

    def test_monitoring_domain(self):
        key, _profile = self.forge.detect_domain("alerting Prometheus et Grafana dashboards")
        self.assertEqual(key, "monitoring")

    def test_networking_domain(self):
        key, _profile = self.forge.detect_domain("configuration du proxy Nginx et DNS")
        self.assertEqual(key, "networking")

    def test_performance_domain(self):
        key, _profile = self.forge.detect_domain("optimisation performance et profiling")
        self.assertEqual(key, "performance")

    def test_documentation_domain(self):
        key, _profile = self.forge.detect_domain("rédaction de documentation technique et guides")
        self.assertEqual(key, "documentation")

    def test_unknown_returns_custom(self):
        key, profile = self.forge.detect_domain("blablabla xyz 12345")
        self.assertEqual(key, "custom")
        self.assertEqual(profile["role"], "Custom Domain Specialist")

    def test_returns_profile_dict(self):
        _, profile = self.forge.detect_domain("database migration")
        self.assertIn("icon", profile)
        self.assertIn("tools", profile)
        self.assertIn("keywords", profile)
        self.assertIn("role", profile)

    def test_data_domain(self):
        key, _ = self.forge.detect_domain("pipeline ETL avec dbt et Airflow")
        self.assertEqual(key, "data")

    def test_storage_domain(self):
        key, _ = self.forge.detect_domain("backup avec restic et stockage S3")
        self.assertEqual(key, "storage")


class TestExtractAgentName(unittest.TestCase):
    """Test extract_agent_name()."""

    def setUp(self):
        self.forge = _import_forge()

    def test_database_migration(self):
        _, profile = self.forge.detect_domain("migrations DB PostgreSQL")
        name, tag = self.forge.extract_agent_name("migrations DB PostgreSQL", "database", profile)
        self.assertIsInstance(name, str)
        self.assertIsInstance(tag, str)
        self.assertGreater(len(tag), 0)
        self.assertTrue(tag.startswith("db-"))

    def test_security_audit(self):
        _, profile = self.forge.detect_domain("audit sécurité")
        _name, tag = self.forge.extract_agent_name("audit sécurité", "security", profile)
        self.assertTrue(tag.startswith("sec-"))

    def test_french_contractions_cleaned(self):
        _, profile = self.forge.detect_domain("l'audit d'agents de sécurité")
        _name, tag = self.forge.extract_agent_name("l'audit d'agents de sécurité", "security", profile)
        # Should not contain l' or d'
        self.assertNotIn("l'", tag)
        self.assertNotIn("d'", tag)

    def test_tag_max_length(self):
        _, profile = self.forge.detect_domain("very long description about many things")
        _, tag = self.forge.extract_agent_name(
            "this is a very very very very long description about things",
            "custom", profile
        )
        # tag_prefix + hyphen + tag should be manageable
        self.assertLessEqual(len(tag), 35)  # prefix(5) + hyphen + 25 max

    def test_empty_fallback(self):
        profile = self.forge.DEFAULT_DOMAIN.copy()
        _name, tag = self.forge.extract_agent_name("je tu il", "custom", profile)
        # Should fallback to domain key
        self.assertGreater(len(tag), 0)


class TestScanGapsFromSharedContext(unittest.TestCase):
    """Test scan_gaps_from_shared_context()."""

    def setUp(self):
        self.forge = _import_forge()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_finds_unresolved_gaps(self):
        f = self.tmpdir / "shared-context.md"
        f.write_text(
            "# Shared Context\n"
            "## Requêtes inter-agents\n"
            "- [ ] [forge→?] Besoin d'un agent pour gérer les backups\n"
            "- [x] [hawk→probe] Monitoring actif\n"
            "- [ ] [dev→?] Agent migration DB nécessaire\n"
            "## Autre section\n"
            "Contenu normal.\n"
        )
        gaps = self.forge.scan_gaps_from_shared_context(f)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].source_agent, "forge")
        self.assertIn("backup", gaps[0].target_description.lower())

    def test_no_gaps(self):
        f = self.tmpdir / "shared-context.md"
        f.write_text("# Shared Context\n## Requêtes inter-agents\n## Autre section\n")
        gaps = self.forge.scan_gaps_from_shared_context(f)
        self.assertEqual(len(gaps), 0)

    def test_missing_file(self):
        gaps = self.forge.scan_gaps_from_shared_context(self.tmpdir / "nope.md")
        self.assertEqual(len(gaps), 0)


class TestScanGapsFromTrace(unittest.TestCase):
    """Test scan_gaps_from_trace()."""

    def setUp(self):
        self.forge = _import_forge()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_finds_recurring_failures(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        lines = [
            "## 2026-01-01 | dev | story-1\n",
            "[FAILURE] db-migration failed\n",
            "[FAILURE] db-migration timeout\n",
            "[FAILURE] db-migration lock error\n",
            "[FAILURE] db-migration schema mismatch\n",
        ]
        f.write_text("".join(lines))
        gaps = self.forge.scan_gaps_from_trace(f, known_agents=["dev", "qa"])
        self.assertGreater(len(gaps), 0)

    def test_no_failures(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text("## 2026-01-01 | dev | story-1\nAll good here.\n")
        gaps = self.forge.scan_gaps_from_trace(f, known_agents=["dev"])
        self.assertEqual(len(gaps), 0)

    def test_missing_trace(self):
        gaps = self.forge.scan_gaps_from_trace(self.tmpdir / "nope.md", known_agents=[])
        self.assertEqual(len(gaps), 0)


class TestListExistingAgents(unittest.TestCase):
    """Test list_existing_agents()."""

    def setUp(self):
        self.forge = _import_forge()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lists_agents(self):
        agents_dir = self.tmpdir / "agents"
        agents_dir.mkdir()
        (agents_dir / "forge.md").write_text("# Forge\n")
        (agents_dir / "hawk.md").write_text("# Hawk\n")
        result = self.forge.list_existing_agents(agents_dir)
        self.assertEqual(len(result), 2)
        self.assertIn("forge", result)
        self.assertIn("hawk", result)

    def test_excludes_custom_agent_template(self):
        agents_dir = self.tmpdir / "agents"
        agents_dir.mkdir()
        (agents_dir / "custom-agent.tpl.md").write_text("# Template\n")
        # custom-agent starts are excluded
        # But the code checks f.name.startswith("custom-agent")
        result = self.forge.list_existing_agents(agents_dir)
        self.assertEqual(len(result), 0)

    def test_missing_dir(self):
        result = self.forge.list_existing_agents(self.tmpdir / "nope")
        self.assertEqual(len(result), 0)


class TestCheckOverlap(unittest.TestCase):
    """Test check_overlap()."""

    def setUp(self):
        self.forge = _import_forge()

    def test_detects_overlap(self):
        overlaps = self.forge.check_overlap(
            "db-migration", "database",
            ["forge", "db-backup", "hawk"]
        )
        # "db-migration" has "migration" (>3 chars) but "db-backup" has "backup"
        # Actually "db" is only 2 chars, let's check the real logic
        # The function checks tag keywords with len > 3
        # "migration" is > 3 chars — should not match any of these
        # Let's use a better example
        overlaps = self.forge.check_overlap(
            "sec-vulnerability-scan", "security",
            ["vulnerability-auditor", "hawk", "forge"]
        )
        self.assertIn("vulnerability-auditor", overlaps)

    def test_no_overlap(self):
        overlaps = self.forge.check_overlap(
            "db-migration", "database",
            ["hawk", "forge", "probe"]
        )
        self.assertEqual(len(overlaps), 0)


class TestReadProjectContext(unittest.TestCase):
    """Test read_project_context()."""

    def setUp(self):
        self.forge = _import_forge()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reads_basic_yaml(self):
        f = self.tmpdir / "project-context.yaml"
        f.write_text("project_name: TestProject\nuser: Guilhem\n")
        ctx = self.forge.read_project_context(f)
        self.assertEqual(ctx["project_name"], "TestProject")
        self.assertEqual(ctx["user"], "Guilhem")

    def test_missing_file(self):
        ctx = self.forge.read_project_context(self.tmpdir / "nope.yaml")
        self.assertEqual(ctx, {})

    def test_ignores_comments(self):
        f = self.tmpdir / "ctx.yaml"
        f.write_text("# Comment\nkey: value\n# Another\nfoo: bar\n")
        ctx = self.forge.read_project_context(f)
        self.assertEqual(ctx["key"], "value")
        self.assertEqual(ctx["foo"], "bar")


class TestReadActiveDna(unittest.TestCase):
    """Test read_active_dna()."""

    def setUp(self):
        self.forge = _import_forge()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extracts_descriptions(self):
        dna_dir = self.tmpdir / "archetypes/infra-ops"
        dna_dir.mkdir(parents=True)
        (dna_dir / "archetype.dna.yaml").write_text(
            "acceptance_criteria:\n"
            "  - description: 'All containers must have health checks'\n"
            "  - description: 'Terraform plan must be idempotent'\n"
        )
        ac = self.forge.read_active_dna(self.tmpdir / "archetypes")
        self.assertGreater(len(ac), 0)

    def test_empty_dir(self):
        empty = self.tmpdir / "archetypes"
        empty.mkdir()
        ac = self.forge.read_active_dna(empty)
        self.assertEqual(len(ac), 0)

    def test_max_10(self):
        dna_dir = self.tmpdir / "archetypes/big"
        dna_dir.mkdir(parents=True)
        lines = "acceptance_criteria:\n"
        for i in range(20):
            lines += f"  - description: 'AC number {i:02d} for testing limits'\n"
        (dna_dir / "archetype.dna.yaml").write_text(lines)
        ac = self.forge.read_active_dna(self.tmpdir / "archetypes")
        self.assertLessEqual(len(ac), 10)


class TestDomainTaxonomy(unittest.TestCase):
    """Test DOMAIN_TAXONOMY completeness."""

    def setUp(self):
        self.forge = _import_forge()

    def test_all_domains_have_required_keys(self):
        required_keys = {"icon", "tag_prefix", "tools", "keywords", "role",
                         "domain_word", "prompt_patterns", "cc_check"}
        for domain_key, profile in self.forge.DOMAIN_TAXONOMY.items():
            for key in required_keys:
                self.assertIn(key, profile, f"Domain {domain_key} missing key {key}")

    def test_all_domains_have_nonempty_keywords(self):
        for domain_key, profile in self.forge.DOMAIN_TAXONOMY.items():
            self.assertGreater(len(profile["keywords"]), 0,
                               f"Domain {domain_key} has no keywords")

    def test_minimum_12_domains(self):
        self.assertGreaterEqual(len(self.forge.DOMAIN_TAXONOMY), 12)


if __name__ == "__main__":
    unittest.main()
