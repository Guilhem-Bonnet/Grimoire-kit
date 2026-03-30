#!/usr/bin/env python3
"""
Tests pour skill-validator.py — Validateur déterministe de skills Grimoire.

Fonctions testées :
  - parse_frontmatter()
  - body_after_frontmatter()
  - check_skill_01..07()
  - check_wf_01, check_wf_02()
  - check_step_01, check_step_06, check_step_07()
  - check_seq_02()
  - validate_skill()
  - discover_skills()
  - format_human(), format_json()
  - build_parser(), main()
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "skill-validator.py"

spec = importlib.util.spec_from_file_location("skill_validator", TOOL)
sv = importlib.util.module_from_spec(spec)
sys.modules["skill_validator"] = sv
spec.loader.exec_module(sv)


# ── Frontmatter ───────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\nname: grimoire-test\ndescription: 'A test skill'\n---\n# Body"
        fm = sv.parse_frontmatter(content)
        assert fm["name"] == "grimoire-test"
        assert fm["description"] == "A test skill"

    def test_no_frontmatter(self):
        assert sv.parse_frontmatter("# Just a heading") == {}

    def test_empty_content(self):
        assert sv.parse_frontmatter("") == {}

    def test_quoted_values(self):
        content = '---\nname: "grimoire-quoted"\n---\n'
        fm = sv.parse_frontmatter(content)
        assert fm["name"] == "grimoire-quoted"


class TestBodyAfterFrontmatter:
    def test_with_frontmatter(self):
        content = "---\nname: test\n---\n\n# Body here"
        assert sv.body_after_frontmatter(content) == "# Body here"

    def test_no_frontmatter(self):
        content = "# Just body"
        assert sv.body_after_frontmatter(content) == "# Just body"


# ── Skill Rules ───────────────────────────────────────────────────────────────


class TestSkillRules:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skill_dir = Path(self.tmpdir) / "grimoire-test"
        self.skill_dir.mkdir()

    def _write_skill(self, content: str):
        (self.skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_skill_01_missing(self):
        findings = sv.check_skill_01(self.skill_dir)
        assert len(findings) == 1
        assert findings[0].rule == "SKILL-01"
        assert findings[0].severity == sv.CRITICAL

    def test_skill_01_exists(self):
        self._write_skill("---\nname: grimoire-test\n---\n# Test")
        assert sv.check_skill_01(self.skill_dir) == []

    def test_skill_02_missing_name(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_02(skill_md, {})
        assert len(findings) == 1
        assert findings[0].rule == "SKILL-02"

    def test_skill_02_has_name(self):
        skill_md = self.skill_dir / "SKILL.md"
        assert sv.check_skill_02(skill_md, {"name": "grimoire-test"}) == []

    def test_skill_03_missing_description(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_03(skill_md, {})
        assert len(findings) == 1

    def test_skill_04_valid_name(self):
        skill_md = self.skill_dir / "SKILL.md"
        assert sv.check_skill_04(skill_md, {"name": "grimoire-edge-case-hunter"}) == []

    def test_skill_04_invalid_name_no_prefix(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_04(skill_md, {"name": "bad-name"})
        assert len(findings) == 1
        assert findings[0].rule == "SKILL-04"

    def test_skill_04_invalid_name_uppercase(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_04(skill_md, {"name": "grimoire-BadName"})
        assert len(findings) == 1

    def test_skill_05_name_matches_dir(self):
        skill_md = self.skill_dir / "SKILL.md"
        assert sv.check_skill_05(skill_md, {"name": "grimoire-test"}, self.skill_dir) == []

    def test_skill_05_name_mismatch(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_05(skill_md, {"name": "grimoire-other"}, self.skill_dir)
        assert len(findings) == 1

    def test_skill_06_short_description(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_06(skill_md, {"description": "Short"})
        assert any(f.rule == "SKILL-06" and "courte" in f.message for f in findings)

    def test_skill_06_no_use_when(self):
        skill_md = self.skill_dir / "SKILL.md"
        desc = "A long enough description for the skill validator to accept"
        findings = sv.check_skill_06(skill_md, {"description": desc})
        assert any(f.rule == "SKILL-06" and "Use when" in f.message for f in findings)

    def test_skill_06_valid(self):
        skill_md = self.skill_dir / "SKILL.md"
        findings = sv.check_skill_06(skill_md, {"description": "A long enough description. Use when: testing"})
        assert len(findings) == 0

    def test_skill_07_has_body(self):
        skill_md = self.skill_dir / "SKILL.md"
        content = "---\nname: test\n---\n\n# A real body with enough content here"
        assert sv.check_skill_07(skill_md, content) == []

    def test_skill_07_no_body(self):
        skill_md = self.skill_dir / "SKILL.md"
        content = "---\nname: test\n---\n"
        findings = sv.check_skill_07(skill_md, content)
        assert len(findings) == 1


# ── Workflow/Step Rules ───────────────────────────────────────────────────────


class TestWorkflowStepRules:
    def test_wf_01_name_in_workflow(self):
        p = Path("/fake/workflow.md")
        findings = sv.check_wf_01(p, {"name": "should-not-exist"})
        assert len(findings) == 1

    def test_wf_01_no_name(self):
        p = Path("/fake/workflow.md")
        assert sv.check_wf_01(p, {}) == []

    def test_wf_02_description_in_workflow(self):
        p = Path("/fake/workflow.md")
        findings = sv.check_wf_02(p, {"description": "should-not-exist"})
        assert len(findings) == 1

    def test_step_01_valid_filename(self):
        assert sv.check_step_01(Path("step-01-init.md")) == []
        assert sv.check_step_01(Path("step-02a-clarify.md")) == []

    def test_step_01_invalid_filename(self):
        findings = sv.check_step_01(Path("my-step.md"))
        assert len(findings) == 1

    def test_step_06_no_name_or_desc(self):
        assert sv.check_step_06(Path("step-01.md"), {}) == []

    def test_step_06_has_name(self):
        findings = sv.check_step_06(Path("step-01.md"), {"name": "bad"})
        assert len(findings) >= 1

    def test_step_07_no_steps(self):
        tmpdir = tempfile.mkdtemp()
        assert sv.check_step_07(Path(tmpdir)) == []

    def test_step_07_valid_count(self):
        tmpdir = Path(tempfile.mkdtemp())
        for i in range(3):
            (tmpdir / f"step-0{i+1}-test.md").write_text("content")
        assert sv.check_step_07(tmpdir) == []

    def test_step_07_too_few(self):
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "step-01-only.md").write_text("content")
        findings = sv.check_step_07(tmpdir)
        assert len(findings) == 1

    def test_seq_02_no_estimates(self):
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "SKILL.md").write_text("---\nname: test\n---\n# No time here")
        assert sv.check_seq_02(tmpdir) == []

    def test_seq_02_has_estimate(self):
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "SKILL.md").write_text("---\nname: test\n---\n# Takes 5 minutes")
        findings = sv.check_seq_02(tmpdir)
        assert len(findings) == 1
        assert findings[0].rule == "SEQ-02"


# ── Integration ───────────────────────────────────────────────────────────────


class TestIntegration:
    def _make_valid_skill(self, base: Path, name: str = "grimoire-valid") -> Path:
        skill_dir = base / ".github" / "skills" / name
        skill_dir.mkdir(parents=True)
        content = (
            f"---\nname: {name}\n"
            f"description: 'Valid skill. Use when: testing.'\n"
            f"---\n\n# {name}\n\nEnough body content here."
        )
        (skill_dir / "SKILL.md").write_text(content)
        return skill_dir

    def test_validate_skill_valid(self):
        tmpdir = Path(tempfile.mkdtemp())
        skill_dir = self._make_valid_skill(tmpdir)
        report = sv.validate_skill(skill_dir)
        assert report.findings == []

    def test_validate_skill_invalid(self):
        tmpdir = Path(tempfile.mkdtemp())
        skill_dir = tmpdir / ".github" / "skills" / "bad-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: bad-name\n---\n")
        report = sv.validate_skill(skill_dir)
        assert len(report.findings) > 0

    def test_discover_skills(self):
        tmpdir = Path(tempfile.mkdtemp())
        self._make_valid_skill(tmpdir, "grimoire-one")
        self._make_valid_skill(tmpdir, "grimoire-two")
        skills = sv.discover_skills(tmpdir)
        assert len(skills) == 2

    def test_discover_skills_empty(self):
        tmpdir = Path(tempfile.mkdtemp())
        assert sv.discover_skills(tmpdir) == []

    def test_format_human_clean(self):
        report = sv.SkillReport(skill_dir="test-skill")
        output = sv.format_human([report])
        assert "✅" in output

    def test_format_json_structure(self):
        report = sv.SkillReport(skill_dir="test-skill")
        data = json.loads(sv.format_json([report]))
        assert data["skills_count"] == 1
        assert data["findings_count"] == 0

    def test_main_on_real_project(self):
        """Run validator on actual project skills."""
        project_root = KIT_DIR.parent  # bmad-custom
        if (project_root / ".github" / "skills").is_dir():
            exit_code = sv.main(["--project-root", str(project_root)])
            assert exit_code == 0

    def test_main_strict_clean(self):
        tmpdir = Path(tempfile.mkdtemp())
        self._make_valid_skill(tmpdir)
        exit_code = sv.main(["--project-root", str(tmpdir), "--strict"])
        assert exit_code == 0

    def test_main_strict_fails_on_critical(self):
        tmpdir = Path(tempfile.mkdtemp())
        skill_dir = tmpdir / ".github" / "skills" / "grimoire-broken"
        skill_dir.mkdir(parents=True)
        # No SKILL.md → CRITICAL
        exit_code = sv.main(["--project-root", str(tmpdir), "--strict"])
        assert exit_code == 1

    def test_main_json_output(self, capsys):
        tmpdir = Path(tempfile.mkdtemp())
        self._make_valid_skill(tmpdir)
        sv.main(["--project-root", str(tmpdir), "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["version"] == sv.SKILL_VALIDATOR_VERSION
