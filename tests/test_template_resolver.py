"""Tests for grimoire.core.template_resolver — placeholder substitution."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.template_resolver import TemplateResolver


class TestListVariables(unittest.TestCase):
    def test_no_variables(self) -> None:
        self.assertEqual(TemplateResolver.list_variables("plain text"), [])

    def test_single_variable(self) -> None:
        self.assertEqual(TemplateResolver.list_variables("Hello {{NAME}}"), ["NAME"])

    def test_multiple_variables(self) -> None:
        result = TemplateResolver.list_variables("{{A}} and {{B}} and {{C}}")
        self.assertEqual(result, ["A", "B", "C"])

    def test_no_lowercase(self) -> None:
        result = TemplateResolver.list_variables("{{lowercase}}")
        self.assertEqual(result, [])

    def test_underscore_variable(self) -> None:
        result = TemplateResolver.list_variables("{{SESSION_CHAIN}}")
        self.assertEqual(result, ["SESSION_CHAIN"])


class TestResolveStatic(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.resolver = TemplateResolver(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_timestamp_resolved(self) -> None:
        result = self.resolver.resolve("At {{TIMESTAMP}}")
        self.assertNotIn("{{TIMESTAMP}}", result)
        self.assertIn("UTC", result)

    def test_skill_name_resolved(self) -> None:
        result = self.resolver.resolve("Skill: {{SKILL_NAME}}", skill="grimoire-tdd")
        self.assertEqual(result, "Skill: grimoire-tdd")

    def test_agent_name_resolved(self) -> None:
        result = self.resolver.resolve("Agent: {{AGENT_NAME}}", agent="dev")
        self.assertEqual(result, "Agent: dev")

    def test_unknown_variable_kept(self) -> None:
        result = self.resolver.resolve("{{UNKNOWN_VAR}}")
        self.assertEqual(result, "{{UNKNOWN_VAR}}")

    def test_no_variables_passthrough(self) -> None:
        text = "No variables here"
        result = self.resolver.resolve(text)
        self.assertEqual(result, text)


class TestResolveExtraVars(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.resolver = TemplateResolver(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_extra_vars_override(self) -> None:
        result = self.resolver.resolve(
            "Name: {{PROJECT_NAME}}",
            extra_vars={"PROJECT_NAME": "overridden"},
        )
        self.assertEqual(result, "Name: overridden")

    def test_extra_custom_variable(self) -> None:
        result = self.resolver.resolve(
            "Custom: {{CUSTOM_VAR}}",
            extra_vars={"CUSTOM_VAR": "my-value"},
        )
        self.assertEqual(result, "Custom: my-value")


class TestResolveProjectName(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_project_name_from_yaml(self) -> None:
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: my-project\n",
            encoding="utf-8",
        )
        resolver = TemplateResolver(self.root)
        result = resolver.resolve("Project: {{PROJECT_NAME}}")
        self.assertEqual(result, "Project: my-project")

    def test_project_name_missing_yaml(self) -> None:
        resolver = TemplateResolver(self.root)
        result = resolver.resolve("{{PROJECT_NAME}}")
        self.assertEqual(result, "unknown")


class TestResolvePreamble(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Set up project context and learnings data
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: preamble-test\n",
            encoding="utf-8",
        )
        learn_dir = self.root / "_grimoire" / "_memory" / "learnings"
        learn_dir.mkdir(parents=True)
        (learn_dir / "operational.jsonl").write_text(
            json.dumps({"key": "test-key", "insight": "test insight", "confidence": 95}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_preamble_injected(self) -> None:
        resolver = TemplateResolver(self.root)
        result = resolver.resolve("Before\n{{PREAMBLE}}\nAfter")
        self.assertIn("PREAMBLE:START", result)
        self.assertIn("preamble-test", result)
        self.assertIn("PREAMBLE:END", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_learnings_alone(self) -> None:
        resolver = TemplateResolver(self.root)
        result = resolver.resolve("{{LEARNINGS}}")
        self.assertIn("test-key", result)


class TestResolveFile(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.resolver = TemplateResolver(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolve_existing_file(self) -> None:
        tpl = self.root / "template.md"
        tpl.write_text("Skill: {{SKILL_NAME}}", encoding="utf-8")
        result = self.resolver.resolve_file(tpl, skill="grimoire-tdd")
        self.assertEqual(result, "Skill: grimoire-tdd")

    def test_missing_file_returns_empty(self) -> None:
        result = self.resolver.resolve_file(self.root / "nope.md")
        self.assertEqual(result, "")


class TestClearCache(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: cached-project\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cache_then_clear(self) -> None:
        resolver = TemplateResolver(self.root)
        # First call caches
        r1 = resolver.resolve("{{PROJECT_NAME}}")
        self.assertEqual(r1, "cached-project")
        # Modify file
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: updated-project\n",
            encoding="utf-8",
        )
        # Should still return cached value
        r2 = resolver.resolve("{{PROJECT_NAME}}")
        self.assertEqual(r2, "cached-project")
        # Clear cache
        resolver.clear_cache()
        r3 = resolver.resolve("{{PROJECT_NAME}}")
        self.assertEqual(r3, "updated-project")


class TestMultipleVariablesInTemplate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_multiple_resolved(self) -> None:
        resolver = TemplateResolver(self.root)
        result = resolver.resolve(
            "Skill={{SKILL_NAME}} Agent={{AGENT_NAME}}",
            skill="tdd",
            agent="dev",
        )
        self.assertEqual(result, "Skill=tdd Agent=dev")


if __name__ == "__main__":
    unittest.main()
