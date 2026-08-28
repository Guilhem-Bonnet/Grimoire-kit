"""Le catalogue doit voir tout ce que le kit installe.

Il n'indexait que `.github/prompts/` : sept fichiers d'hygiène. Les workflows
d'orchestration, déposés par le scaffold sous le tier kit, étaient installés
dans chaque projet et listés par aucune surface. Ces tests fixent ce que le
catalogue voit, d'où, et dans quel ordre de priorité.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.data import framework_path
from grimoire.workflows import registry


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _orchestration(description: str = "Une orchestration", **fields: str) -> str:
    lines = ["---", "kind: orchestration", f'description: "{description}"']
    lines += [f"{key}: {value}" for key, value in fields.items()]
    lines += ["---", "", "Corps du workflow."]
    return "\n".join(lines)


class TestShippedWorkflows:
    """Garde mécanique sur ce que le kit livre — pas sur un projet de test."""

    def test_the_six_orchestrations_are_in_the_catalogue(self, tmp_path: Path) -> None:
        slugs = {entry.slug for entry in registry.load_workflows(tmp_path) if entry.is_orchestration}

        assert {
            "boomerang-orchestration",
            "subagent-orchestration",
            "party-mode",
            "incident-response",
            "state-checkpoint",
            "repo-map-generator",
        } <= slugs

    @pytest.mark.parametrize(
        "path",
        sorted(p for p in (framework_path() / "workflows").glob("*.md") if not p.name.endswith(".tpl.md")),
        ids=lambda p: p.stem,
    )
    def test_every_shipped_orchestration_declares_a_description(self, path: Path) -> None:
        """Un frontmatter qui ne parse pas dégrade en silence.

        Deux descriptions contenaient un `:` non échappé ; le YAML échouait,
        `parse_frontmatter` renvoyait un dict vide, et le workflow arrivait au
        catalogue sans nom ni agents. Rien ne le signalait.
        """
        entry = registry._read_entry(path, registry.SOURCE_FRAMEWORK, default_kind=registry.KIND_ORCHESTRATION)

        assert entry is not None, f"{path.name} ne se déclare pas"
        assert entry.description, f"{path.name} : frontmatter absent ou illisible"
        assert entry.kind == registry.KIND_ORCHESTRATION

    def test_the_hygiene_prompts_declare_themselves_as_commands(self, tmp_path: Path) -> None:
        commands = {e.slug for e in registry.load_workflows(tmp_path) if e.kind == registry.KIND_COMMAND}

        assert "grimoire-status" in commands
        assert "grimoire-health-check" in commands


class TestSelfDeclaration:
    def test_a_file_without_frontmatter_is_not_a_workflow(self, tmp_path: Path) -> None:
        """Sous `workflows/` vivent aussi des gabarits de rapport."""
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "run-status.md", "# Rapport de run\n")

        slugs = {entry.slug for entry in registry.load_workflows(tmp_path)}

        assert "run-status" not in slugs

    def test_a_template_is_excluded_even_when_it_declares_itself(self, tmp_path: Path) -> None:
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "gabarit.tpl.md", _orchestration())

        slugs = {entry.slug for entry in registry.load_workflows(tmp_path)}

        assert "gabarit" not in slugs and "gabarit.tpl" not in slugs

    def test_an_installed_workflow_that_declares_itself_is_listed(self, tmp_path: Path) -> None:
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "revue-croisee.md", _orchestration("Revue croisée"))

        entry = registry.find_workflow(tmp_path, "revue-croisee")

        assert entry is not None
        assert entry.source == registry.SOURCE_INSTALLED
        assert entry.description == "Revue croisée"


class TestPrecedence:
    def test_an_override_shadows_the_kit_copy(self, tmp_path: Path) -> None:
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "party-mode.md", _orchestration("version kit"))
        _write(tmp_path / "_grimoire" / "overrides" / "workflows" / "party-mode.md", _orchestration("version projet"))

        entry = registry.find_workflow(tmp_path, "party-mode")

        assert entry is not None
        assert entry.description == "version projet"

    def test_a_project_prompt_shadows_the_framework_one(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".github" / "prompts" / "grimoire-status.prompt.md",
            "---\nkind: command\ndescription: 'statut maison'\n---\n\nCorps.\n",
        )

        entry = registry.find_workflow(tmp_path, "grimoire-status")

        assert entry is not None
        assert entry.source == registry.SOURCE_PROJECT
        assert entry.description == "statut maison"

    def test_the_installed_copy_wins_over_the_framework_one(self, tmp_path: Path) -> None:
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "party-mode.md", _orchestration("version installée"))

        entry = registry.find_workflow(tmp_path, "party-mode")

        assert entry is not None
        assert entry.source == registry.SOURCE_INSTALLED


class TestMetadata:
    def test_agents_accept_a_list_and_a_comma_string(self, tmp_path: Path) -> None:
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "a.md", _orchestration(agents="[dev, qa]"))
        _write(tmp_path / "_grimoire" / "kit" / "workflows" / "b.md", _orchestration(agents="dev, qa"))

        a = registry.find_workflow(tmp_path, "a")
        b = registry.find_workflow(tmp_path, "b")

        assert a is not None and b is not None
        assert a.agents == ("dev", "qa")
        assert b.agents == ("dev", "qa")

    def test_a_trigger_is_a_phrase_and_survives_its_spaces(self, tmp_path: Path) -> None:
        """Les déclencheurs ne passent pas par le découpage des listes d'agents."""
        _write(
            tmp_path / "_grimoire" / "kit" / "workflows" / "c.md",
            "---\nkind: orchestration\ndescription: 'x'\ntriggers:\n  - décision qui engage plusieurs domaines\n---\n",
        )

        entry = registry.find_workflow(tmp_path, "c")

        assert entry is not None
        assert entry.triggers == ("décision qui engage plusieurs domaines",)

    def test_an_unknown_kind_falls_back_rather_than_propagating(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "_grimoire" / "kit" / "workflows" / "d.md",
            "---\nkind: sorcellerie\ndescription: 'x'\n---\n",
        )

        entry = registry.find_workflow(tmp_path, "d")

        assert entry is not None
        assert entry.kind == registry.KIND_ORCHESTRATION

    def test_the_command_form_is_the_slug(self, tmp_path: Path) -> None:
        entry = registry.find_workflow(tmp_path, "party-mode")

        assert entry is not None
        assert entry.command == "/party-mode"

    def test_find_accepts_a_filename(self, tmp_path: Path) -> None:
        assert registry.find_workflow(tmp_path, "party-mode.md") is not None

    def test_find_returns_none_for_an_unknown_slug(self, tmp_path: Path) -> None:
        assert registry.find_workflow(tmp_path, "workflow-imaginaire") is None
