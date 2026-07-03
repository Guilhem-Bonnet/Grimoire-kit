"""Tests for grimoire.core.skill_dispatcher — automatic skill invocation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.skill_dispatcher import SkillDispatcher, SkillInvocation


class TestSkillInvocation(unittest.TestCase):
    def test_defaults(self) -> None:
        inv = SkillInvocation(
            skill="test",
            path=None,
            found=False,
            preamble_injected=False,
            template_resolved=False,
            content_length=0,
            timestamp="2025-01-01T00:00:00Z",
        )
        self.assertEqual(inv.skill, "test")
        self.assertFalse(inv.found)

    def test_frozen(self) -> None:
        inv = SkillInvocation(
            skill="x", path=None, found=False,
            preamble_injected=False, template_resolved=False,
            content_length=0, timestamp="",
        )
        with self.assertRaises(AttributeError):
            inv.skill = "changed"  # type: ignore[misc]


class TestDiscover(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_find_in_github_skills(self) -> None:
        skill_dir = self.root / ".github" / "skills" / "grimoire-tdd"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# TDD\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        result = dispatcher.discover("grimoire-tdd")
        self.assertIsNotNone(result)
        self.assertTrue(result.exists())

    def test_find_in_bmad_skills(self) -> None:
        skill_dir = self.root / "_bmad" / "skills" / "custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Custom\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        result = dispatcher.discover("custom-skill")
        self.assertIsNotNone(result)

    def test_not_found(self) -> None:
        dispatcher = SkillDispatcher(self.root)
        result = dispatcher.discover("nonexistent")
        self.assertIsNone(result)


class TestListSkills(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_empty(self) -> None:
        dispatcher = SkillDispatcher(self.root)
        self.assertEqual(dispatcher.list_skills(), [])

    def test_list_multiple(self) -> None:
        for name in ["alpha", "beta", "gamma"]:
            d = self.root / ".github" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        skills = dispatcher.list_skills()
        self.assertEqual(skills, ["alpha", "beta", "gamma"])

    def test_list_legacy_bmad_skills(self) -> None:
        skill_dir = self.root / "_bmad" / "skills" / "legacy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# legacy\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        self.assertEqual(dispatcher.list_skills(), ["legacy"])

    def test_ignores_dirs_without_skill_md(self) -> None:
        skill_dir = self.root / ".github" / "skills" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "README.md").write_text("not a skill\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        self.assertEqual(dispatcher.list_skills(), [])


class TestPrepare(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        skill_dir = self.root / ".github" / "skills" / "grimoire-tdd"
        skill_dir.mkdir(parents=True)
        self.skill_path = skill_dir / "SKILL.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_prepare_not_found(self) -> None:
        dispatcher = SkillDispatcher(self.root)
        content, inv = dispatcher.prepare("nonexistent")
        self.assertEqual(content, "")
        self.assertFalse(inv.found)

    def test_prepare_simple(self) -> None:
        self.skill_path.write_text("# TDD Skill\n\nContent here\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        content, inv = dispatcher.prepare("grimoire-tdd", inject_preamble=False, resolve_templates=False)
        self.assertTrue(inv.found)
        self.assertIn("TDD Skill", content)
        self.assertFalse(inv.preamble_injected)
        self.assertFalse(inv.template_resolved)

    def test_prepare_with_template_resolution(self) -> None:
        self.skill_path.write_text("Skill: {{SKILL_NAME}}\n", encoding="utf-8")
        dispatcher = SkillDispatcher(self.root)
        content, inv = dispatcher.prepare("grimoire-tdd", inject_preamble=False)
        self.assertIn("grimoire-tdd", content)
        self.assertNotIn("{{SKILL_NAME}}", content)
        self.assertTrue(inv.template_resolved)

    def test_prepare_with_preamble(self) -> None:
        self.skill_path.write_text("---\ndescription: test\n---\n# TDD\n", encoding="utf-8")
        # Create project-context.yaml for preamble vitals
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: test-preamble\n", encoding="utf-8",
        )
        dispatcher = SkillDispatcher(self.root)
        content, inv = dispatcher.prepare("grimoire-tdd")
        self.assertTrue(inv.preamble_injected)
        self.assertIn("PREAMBLE:START", content)
        self.assertIn("test-preamble", content)

    def test_prepare_preamble_after_frontmatter(self) -> None:
        self.skill_path.write_text("---\nname: test\n---\n# Title\n", encoding="utf-8")
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: after-fm\n", encoding="utf-8",
        )
        dispatcher = SkillDispatcher(self.root)
        content, _inv = dispatcher.prepare("grimoire-tdd")
        # Preamble should be between frontmatter and title
        fm_end = content.find("---", 3)
        preamble_pos = content.find("PREAMBLE:START")
        title_pos = content.find("# Title")
        self.assertGreater(preamble_pos, fm_end)
        self.assertLess(preamble_pos, title_pos)


class TestComplete(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_complete_records_telemetry(self) -> None:
        dispatcher = SkillDispatcher(self.root)
        dispatcher.complete("grimoire-tdd", outcome="success", duration_s=5.0)
        telem_file = self.root / "_grimoire" / "_memory" / "telemetry" / "skill-usage.jsonl"
        self.assertTrue(telem_file.exists())
        data = json.loads(telem_file.read_text(encoding="utf-8").strip())
        self.assertEqual(data["skill"], "grimoire-tdd")
        self.assertEqual(data["outcome"], "success")


class TestInjectAfterFrontmatter(unittest.TestCase):
    def test_no_frontmatter(self) -> None:
        result = SkillDispatcher._inject_after_frontmatter("# Title\n", "PREAMBLE")
        self.assertTrue(result.startswith("PREAMBLE"))

    def test_with_frontmatter(self) -> None:
        content = "---\nname: test\n---\n# Title\n"
        result = SkillDispatcher._inject_after_frontmatter(content, "PREAMBLE")
        # Frontmatter should be before preamble
        self.assertTrue(result.startswith("---"))
        fm_end = result.find("---", 3)
        self.assertGreater(result.find("PREAMBLE"), fm_end)
        self.assertIn("# Title", result)


if __name__ == "__main__":
    unittest.main()
