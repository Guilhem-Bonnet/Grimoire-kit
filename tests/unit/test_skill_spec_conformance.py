"""Conformité des skills à la spécification Agent Skills (issue #874).

Chaque règle de la spec est prouvée deux fois : un skill qui l'enfreint fait
échouer le contrôle avec un message qui nomme la règle et la valeur reçue, et
tous les skills que le kit livre passent le même contrôle.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from grimoire.hosts.emitters.base import EmittedFile
from grimoire.hosts.emitters.claude_code import _skill_file as _claude_skill_file
from grimoire.hosts.emitters.copilot import _skill_file as _copilot_skill_file
from grimoire.hosts.skill_spec import (
    BODY_MAX_LINES,
    COMPATIBILITY_MAX,
    DESCRIPTION_MAX,
    NAME_MAX,
    validate_skill_file,
    validate_skill_text,
)
from grimoire.hosts.surface import SkillSpec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _skill(
    *,
    name: str | None = None,
    description: str = "Une description utile.",
    extra: str = "",
    body: str = "# Corps\n\nTexte.\n",
) -> str:
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    lines.append(f"description: '{description}'")
    if extra:
        lines.append(extra)
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _rules(text: str, slug: str = "bon-skill") -> list[str]:
    return [v.rule for v in validate_skill_text(text, slug=slug)]


class TestConformingSkill:
    def test_minimal_valid_skill_has_no_violation(self) -> None:
        assert validate_skill_text(_skill(), slug="bon-skill") == ()

    @pytest.mark.parametrize("slug", ["stack-python", "meta-memory-quality", "a", "x1-y2-z3"])
    def test_stack_and_meta_prefixes_are_legitimate(self, slug: str) -> None:
        """Pas de règle ``^grimoire-`` : l'ancien skill-validator la rendait fausse."""
        assert validate_skill_text(_skill(), slug=slug) == ()


class TestNameRules:
    def test_uppercase_is_named_with_its_value(self) -> None:
        violations = validate_skill_text(_skill(), slug="Bon-Skill")
        assert [v.rule for v in violations] == ["name.format"]
        assert "majuscule" in str(violations[0])
        assert "Bon-Skill" in str(violations[0])

    def test_double_hyphen_is_named_with_its_value(self) -> None:
        violations = validate_skill_text(_skill(), slug="bon--skill")
        assert [v.rule for v in violations] == ["name.format"]
        assert "tiret doublé" in str(violations[0])
        assert "bon--skill" in str(violations[0])

    @pytest.mark.parametrize("slug", ["-bon", "bon-"])
    def test_leading_or_trailing_hyphen_is_refused(self, slug: str) -> None:
        violations = validate_skill_text(_skill(), slug=slug)
        assert [v.rule for v in violations] == ["name.format"]
        assert "tiret initial ou final" in str(violations[0])

    def test_invalid_character_is_refused(self) -> None:
        violations = validate_skill_text(_skill(), slug="bon_skill")
        assert [v.rule for v in violations] == ["name.format"]
        assert "bon_skill" in str(violations[0])

    def test_name_longer_than_64_is_refused(self) -> None:
        slug = "a" * (NAME_MAX + 1)
        violations = validate_skill_text(_skill(), slug=slug)
        assert [v.rule for v in violations] == ["name.length"]
        assert str(NAME_MAX + 1) in str(violations[0])

    def test_name_of_exactly_64_characters_is_accepted(self) -> None:
        assert validate_skill_text(_skill(), slug="a" * NAME_MAX) == ()

    def test_declared_name_must_match_folder(self) -> None:
        violations = validate_skill_text(_skill(name="autre-nom"), slug="bon-skill")
        assert [v.rule for v in violations] == ["name.dir"]
        assert "autre-nom" in str(violations[0])
        assert "bon-skill" in str(violations[0])

    def test_declared_name_equal_to_folder_is_accepted(self) -> None:
        assert validate_skill_text(_skill(name="bon-skill"), slug="bon-skill") == ()


class TestDescriptionRules:
    def test_description_over_1024_characters_is_refused(self) -> None:
        text = _skill(description="d" * (DESCRIPTION_MAX + 1))
        violations = validate_skill_text(text, slug="bon-skill")
        assert [v.rule for v in violations] == ["description.length"]
        assert str(DESCRIPTION_MAX + 1) in str(violations[0])

    def test_description_of_exactly_1024_characters_is_accepted(self) -> None:
        assert validate_skill_text(_skill(description="d" * DESCRIPTION_MAX), slug="bon-skill") == ()

    def test_empty_description_is_refused(self) -> None:
        violations = validate_skill_text(_skill(description="   "), slug="bon-skill")
        assert [v.rule for v in violations] == ["description.empty"]

    def test_missing_description_is_refused(self) -> None:
        text = "---\nname: bon-skill\n---\n# Corps\n"
        assert _rules(text) == ["description.empty"]


class TestCompatibilityRules:
    def test_compatibility_over_500_characters_is_refused(self) -> None:
        text = _skill(extra=f"compatibility: '{'c' * (COMPATIBILITY_MAX + 1)}'")
        assert _rules(text) == ["compatibility.length"]

    def test_compatibility_within_limits_is_accepted(self) -> None:
        assert _rules(_skill(extra="compatibility: 'Claude Code, Copilot'")) == []


class TestBodyRules:
    def test_body_of_500_lines_is_refused(self) -> None:
        body = "\n".join(f"ligne {n}" for n in range(BODY_MAX_LINES)) + "\n"
        violations = validate_skill_text(_skill(body=body), slug="bon-skill")
        assert [v.rule for v in violations] == ["body.lines"]
        assert str(BODY_MAX_LINES) in str(violations[0])

    def test_body_of_499_lines_is_accepted(self) -> None:
        body = "\n".join(f"ligne {n}" for n in range(BODY_MAX_LINES - 1)) + "\n"
        assert _rules(_skill(body=body)) == []


class TestReferenceRules:
    @pytest.mark.parametrize("target", ["references/guide.md", "./scripts/run.sh", "guide.md"])
    def test_relative_single_level_reference_is_accepted(self, target: str) -> None:
        assert _rules(_skill(body=f"Voir [le guide]({target}).\n")) == []

    def test_reference_two_levels_deep_is_refused(self) -> None:
        violations = validate_skill_text(_skill(body="Voir [x](references/deep/guide.md).\n"), slug="bon-skill")
        assert [v.rule for v in violations] == ["references.depth"]
        assert "references/deep/guide.md" in str(violations[0])

    def test_absolute_reference_is_refused(self) -> None:
        violations = validate_skill_text(_skill(body="Voir [x](/etc/guide.md).\n"), slug="bon-skill")
        assert [v.rule for v in violations] == ["references.relative"]
        assert "/etc/guide.md" in str(violations[0])

    def test_reference_escaping_the_skill_is_refused(self) -> None:
        violations = validate_skill_text(_skill(body="Voir [x](../autre/guide.md).\n"), slug="bon-skill")
        assert [v.rule for v in violations] == ["references.escape"]

    def test_urls_and_anchors_are_not_references(self) -> None:
        body = "[site](https://example.org/a/b/c) [ancre](#section) [mail](mailto:a@b.c)\n"
        assert _rules(_skill(body=body)) == []


class TestFileEntry:
    def test_skill_directory_file_takes_its_folder_as_slug(self, tmp_path: Path) -> None:
        """``<slug>/SKILL.md`` (extensions) : le slug est le dossier, pas ``SKILL``."""
        path = tmp_path / "bon-skill" / "SKILL.md"
        path.parent.mkdir()
        path.write_text(_skill(), encoding="utf-8")
        assert validate_skill_file(path) == ()

    def test_flat_file_takes_its_stem_as_slug(self, tmp_path: Path) -> None:
        path = tmp_path / "Mauvais.md"
        path.write_text(_skill(), encoding="utf-8")
        assert [v.rule for v in validate_skill_file(path)] == ["name.format"]


class TestShippedSkills:
    """Tous les skills que le kit livre passent le contrôle, sans assouplir la règle."""

    @staticmethod
    def _shipped_files() -> list[Path]:
        files = sorted((REPO_ROOT / "framework" / "hosts" / "skills").glob("*.md"))
        files += sorted((REPO_ROOT / "archetypes").glob("*/skills/*.md"))
        files += sorted((REPO_ROOT / "extensions").glob("*/artifacts/skills/*/SKILL.md"))
        return files

    def test_the_real_tree_is_scanned(self) -> None:
        """Garde contre un glob vide qui rendrait le test vert sans rien lire."""
        assert len(self._shipped_files()) >= 25

    def test_every_shipped_skill_is_conforming(self) -> None:
        broken = {
            path.relative_to(REPO_ROOT).as_posix(): [str(v) for v in validate_skill_file(path)]
            for path in self._shipped_files()
        }
        broken = {key: value for key, value in broken.items() if value}
        assert broken == {}


class TestEmittedOptionalFields:
    """``license`` et ``compatibility`` sont transmis à l'émission quand ils sont présents."""

    @staticmethod
    def _spec(**fields: str) -> SkillSpec:
        return SkillSpec(slug="bon-skill", name="bon-skill", description="Une description.", body="# Corps\n", **fields)

    @pytest.mark.parametrize("emit", [_claude_skill_file, _copilot_skill_file])
    def test_present_fields_reach_the_emitted_frontmatter(self, emit: Callable[[SkillSpec], EmittedFile]) -> None:
        content = emit(self._spec(license="MIT", compatibility="Claude Code")).content
        header = content.split("---", 2)[1]
        assert "license: 'MIT'" in header
        assert "compatibility: 'Claude Code'" in header

    @pytest.mark.parametrize("emit", [_claude_skill_file, _copilot_skill_file])
    def test_absent_fields_are_not_emitted(self, emit: Callable[[SkillSpec], EmittedFile]) -> None:
        header = emit(self._spec()).content.split("---", 2)[1]
        assert "license" not in header
        assert "compatibility" not in header
