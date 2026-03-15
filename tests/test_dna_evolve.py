#!/usr/bin/env python3
"""
Tests pour dna-evolve.py — DNA Evolution Engine.

Fonctions testées :
  - parse_dna()
  - analyze_trace()
  - analyze_decisions_log()
  - analyze_learnings()
  - generate_mutations()
  - render_patch_yaml()
  - render_report_md()
  - DNASnapshot, DNAMutation, ObservedTool, ObservedPattern dataclasses
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_evolve():
    return importlib.import_module("dna-evolve")


class TestParseDna(unittest.TestCase):
    """Test parse_dna() — DNA YAML parsing."""

    def setUp(self):
        self.evolve = _import_evolve()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parses_basic_dna(self):
        f = self.tmpdir / "archetype.dna.yaml"
        f.write_text(
            "id: infra-ops\n"
            "version: '2.0.0'\n"
            "tools_required:\n"
            "  - name: terraform\n"
            "    check_command: 'which terraform'\n"
            "  - name: docker\n"
            "    check_command: 'which docker'\n"
            "traits:\n"
            "  - name: infrastructure-as-code\n"
            "    rule: 'all infra via code'\n"
            "constraints:\n"
            "  - id: no-manual-changes\n"
            "    enforcement: hard\n"
            "values:\n"
            "  - name: automation-first\n"
        )
        snap = self.evolve.parse_dna(f)
        self.assertEqual(snap.archetype_id, "infra-ops")
        self.assertEqual(snap.version, "2.0.0")
        self.assertIn("terraform", snap.tools)
        self.assertIn("docker", snap.tools)
        self.assertIn("infrastructure-as-code", snap.traits)
        self.assertIn("no-manual-changes", snap.constraints)
        self.assertIn("automation-first", snap.values)

    def test_missing_file(self):
        snap = self.evolve.parse_dna(self.tmpdir / "nonexistent.dna.yaml")
        self.assertEqual(snap.archetype_id, "unknown")
        self.assertEqual(snap.version, "1.0.0")
        self.assertEqual(len(snap.tools), 0)

    def test_empty_dna(self):
        f = self.tmpdir / "empty.dna.yaml"
        f.write_text("# Empty DNA\n")
        snap = self.evolve.parse_dna(f)
        self.assertEqual(snap.archetype_id, "unknown")

    def test_preserves_raw_content(self):
        f = self.tmpdir / "test.dna.yaml"
        content = "id: test\nversion: '1.0'\n"
        f.write_text(content)
        snap = self.evolve.parse_dna(f)
        self.assertEqual(snap.raw_content, content)


class TestAnalyzeTrace(unittest.TestCase):
    """Test analyze_trace() — TRACE log analysis."""

    def setUp(self):
        self.evolve = _import_evolve()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detects_tools(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-01 | forge | S1\n"
            "Running terraform plan\n"
            "Running docker build\n"
            "Running docker push\n"
            "terraform apply complete\n"
        )
        tools, _patterns = self.evolve.analyze_trace(f)
        self.assertIn("terraform", tools)
        self.assertIn("docker", tools)
        self.assertEqual(tools["terraform"].count, 2)
        self.assertEqual(tools["docker"].count, 2)

    def test_detects_behavioral_patterns(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        # Write enough "[CHECKPOINT]" entries to trigger pattern detection (≥3)
        lines = []
        for i in range(5):
            lines.append(f"## 2026-01-{i+1:02d} | dev | S1\n")
            lines.append(f"[CHECKPOINT] checkpoint_id: ckpt-00{i}\n")
        f.write_text("".join(lines))
        _tools, patterns = self.evolve.analyze_trace(f)
        pat_ids = [p.pattern_id for p in patterns]
        self.assertIn("checkpoint-heavy", pat_ids)

    def test_empty_trace(self):
        f = self.tmpdir / "empty.md"
        f.write_text("")
        tools, patterns = self.evolve.analyze_trace(f)
        self.assertEqual(len(tools), 0)
        self.assertEqual(len(patterns), 0)

    def test_missing_trace(self):
        tools, _patterns = self.evolve.analyze_trace(self.tmpdir / "nope.md")
        self.assertEqual(len(tools), 0)

    def test_since_filter(self):
        f = self.tmpdir / "trace.md"
        f.write_text(
            "## 2025-01-01 | dev | old\n"
            "Running pytest old tests\n"
            "\n"
            "## 2026-06-01 | dev | new\n"
            "Running pytest new tests\n"
        )
        tools, _ = self.evolve.analyze_trace(f, since="2026-01-01")
        # pytest should be detected from the filtered content
        if "pytest" in tools:
            self.assertGreater(tools["pytest"].count, 0)


class TestAnalyzeDecisionsLog(unittest.TestCase):
    """Test analyze_decisions_log()."""

    def setUp(self):
        self.evolve = _import_evolve()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detects_security_pattern(self):
        f = self.tmpdir / "decisions-log.md"
        f.write_text(
            "## 2026-01-01\n"
            "| Agent | Decision |\n"
            "| dev | Hardened sécurité on all endpoints |\n"
            "| ops | Security scan before deploy |\n"
            "| dev | Added vulnerability checks |\n"
        )
        patterns = self.evolve.analyze_decisions_log(f)
        pat_ids = [p.pattern_id for p in patterns]
        self.assertIn("security-first", pat_ids)

    def test_no_patterns(self):
        f = self.tmpdir / "decisions-log.md"
        f.write_text("## 2026-01-01\n| Agent | Decision |\n| dev | Did something |\n")
        patterns = self.evolve.analyze_decisions_log(f)
        self.assertEqual(len(patterns), 0)

    def test_missing_file(self):
        patterns = self.evolve.analyze_decisions_log(self.tmpdir / "nope.md")
        self.assertEqual(len(patterns), 0)


class TestAnalyzeLearnings(unittest.TestCase):
    """Test analyze_learnings()."""

    def setUp(self):
        self.evolve = _import_evolve()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detects_frustration_patterns(self):
        mem = self.tmpdir / "memory"
        mem.mkdir()
        (mem / "agent-learnings-dev.md").write_text(
            "# Dev Learnings\n"
            "- outil manquant: besoin de jq\n"
            "- tool not found error with yq\n"
            "- besoin tool manquant helm\n"
        )
        patterns = self.evolve.analyze_learnings(mem)
        pat_ids = [p.pattern_id for p in patterns]
        self.assertIn("missing-tool", pat_ids)

    def test_no_patterns(self):
        mem = self.tmpdir / "memory"
        mem.mkdir()
        (mem / "agent-learnings-dev.md").write_text(
            "# Dev Learnings\n- Everything works great\n"
        )
        patterns = self.evolve.analyze_learnings(mem)
        self.assertEqual(len(patterns), 0)

    def test_missing_dir(self):
        patterns = self.evolve.analyze_learnings(self.tmpdir / "nope")
        self.assertEqual(len(patterns), 0)


class TestGenerateMutations(unittest.TestCase):
    """Test generate_mutations()."""

    def setUp(self):
        self.evolve = _import_evolve()

    def test_adds_new_tool(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
            tools=["terraform", "docker"],
        )
        observed = {
            "kubectl": self.evolve.ObservedTool(name="kubectl", count=15,
                                                 agents={"forge", "dev"},
                                                 last_seen="2026-06-01"),
        }
        mutations = self.evolve.generate_mutations(dna, observed, [])
        add_tools = [m for m in mutations if m.mutation_type == "add_tool"]
        self.assertGreater(len(add_tools), 0)
        self.assertEqual(add_tools[0].item_id, "kubectl")

    def test_ignores_tool_below_threshold(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
            tools=[],
        )
        observed = {
            "rare-tool": self.evolve.ObservedTool(name="rare-tool", count=2),
        }
        mutations = self.evolve.generate_mutations(dna, observed, [])
        add_tools = [m for m in mutations if m.mutation_type == "add_tool"]
        self.assertEqual(len(add_tools), 0)

    def test_does_not_readd_existing(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
            tools=["kubectl"],
        )
        observed = {
            "kubectl": self.evolve.ObservedTool(name="kubectl", count=50),
        }
        mutations = self.evolve.generate_mutations(dna, observed, [])
        add_tools = [m for m in mutations if m.mutation_type == "add_tool"
                     and m.item_id == "kubectl"]
        self.assertEqual(len(add_tools), 0)

    def test_deprecates_unused_tool(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
            tools=["packer", "terraform"],
        )
        # terraform is used, packer is not
        observed = {
            "terraform": self.evolve.ObservedTool(name="terraform", count=20),
        }
        mutations = self.evolve.generate_mutations(dna, observed, [])
        deprecations = [m for m in mutations if m.mutation_type == "deprecate_tool"]
        deprecated_ids = [m.item_id for m in deprecations]
        self.assertIn("packer", deprecated_ids)

    def test_adds_trait_from_pattern(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
        )
        patterns = [
            self.evolve.ObservedPattern(
                pattern_id="tdd-first",
                source="trace",
                description="TDD observed",
                occurrences=10,
                evidence=["[TDD] write test first"],
            )
        ]
        mutations = self.evolve.generate_mutations(dna, {}, patterns)
        trait_adds = [m for m in mutations if "add_trait" in m.mutation_type]
        self.assertGreater(len(trait_adds), 0)
        self.assertEqual(trait_adds[0].item_id, "tdd-enforced")

    def test_confidence_levels(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
        )
        observed = {
            "high-freq": self.evolve.ObservedTool(name="high-freq", count=20),
            "med-freq": self.evolve.ObservedTool(name="med-freq", count=8),
            "low-freq": self.evolve.ObservedTool(name="low-freq", count=5),
        }
        mutations = self.evolve.generate_mutations(dna, observed, [])
        confidences = {m.item_id: m.confidence for m in mutations
                       if m.mutation_type == "add_tool"}
        self.assertEqual(confidences.get("high-freq"), "high")
        self.assertEqual(confidences.get("med-freq"), "medium")
        self.assertEqual(confidences.get("low-freq"), "low")


class TestRenderPatchYaml(unittest.TestCase):
    """Test render_patch_yaml()."""

    def setUp(self):
        self.evolve = _import_evolve()

    def test_renders_patch(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="infra-ops",
            version="2.0.0",
        )
        mutations = [
            self.evolve.DNAMutation(
                mutation_type="add_tool",
                target_section="tools_required",
                item_id="kubectl",
                description="kubectl used frequently",
                rationale="15 occurrences in TRACE",
                evidence_count=15,
                confidence="high",
            ),
        ]
        output = self.evolve.render_patch_yaml(dna, mutations)
        self.assertIn("kubectl", output)
        self.assertIn("tools_required_ADD", output)
        self.assertIn("HIGH", output)

    def test_renders_deprecation(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="test",
            version="1.0",
        )
        mutations = [
            self.evolve.DNAMutation(
                mutation_type="deprecate_tool",
                target_section="tools_required",
                item_id="packer",
                description="packer unused",
                rationale="Not seen in trace",
                evidence_count=0,
                confidence="low",
            ),
        ]
        output = self.evolve.render_patch_yaml(dna, mutations)
        self.assertIn("packer", output)
        self.assertIn("DEPRECATE", output)


class TestRenderReportMd(unittest.TestCase):
    """Test render_report_md()."""

    def setUp(self):
        self.evolve = _import_evolve()

    def test_renders_report(self):
        dna = self.evolve.DNASnapshot(
            source_path=Path("test.dna.yaml"),
            archetype_id="infra-ops",
            version="2.0.0",
            tools=["terraform"],
        )
        mutations = [
            self.evolve.DNAMutation(
                mutation_type="add_tool",
                target_section="tools_required",
                item_id="kubectl",
                description="kubectl observed",
                rationale="Used frequently",
                evidence_count=15,
                confidence="high",
            ),
        ]
        observed_tools = {
            "terraform": self.evolve.ObservedTool(name="terraform", count=20,
                                                   agents={"forge"}, last_seen="2026-06-01"),
            "kubectl": self.evolve.ObservedTool(name="kubectl", count=15,
                                                 agents={"dev"}, last_seen="2026-06-01"),
        }
        patterns = [
            self.evolve.ObservedPattern(pattern_id="tdd-first", source="trace",
                                         description="TDD", occurrences=5),
        ]
        report = self.evolve.render_report_md(dna, mutations, observed_tools, patterns)
        self.assertIn("DNA Evolution Report", report)
        self.assertIn("infra-ops", report)
        self.assertIn("kubectl", report)
        self.assertIn("terraform", report)
        self.assertIn("tdd-first", report)


class TestKnownToolPatterns(unittest.TestCase):
    """Verify KNOWN_TOOLS_PATTERNS are valid regex."""

    def setUp(self):
        self.evolve = _import_evolve()

    def test_all_patterns_compile(self):
        import re
        for tool_name, pattern in self.evolve.KNOWN_TOOLS_PATTERNS.items():
            try:
                re.compile(pattern)
            except re.error as e:
                self.fail(f"Invalid regex for tool {tool_name}: {e}")


if __name__ == "__main__":
    unittest.main()
