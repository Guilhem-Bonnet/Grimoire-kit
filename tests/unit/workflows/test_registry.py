"""Le catalogue doit voir tout ce que le kit installe.

Il n'indexait que `.github/prompts/` : sept fichiers d'hygiène. Les workflows
d'orchestration, déposés par le scaffold sous le tier kit, étaient installés
dans chaque projet et listés par aucune surface. Ces tests fixent ce que le
catalogue voit, d'où, et dans quel ordre de priorité.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

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


class TestDeprecation:
    """Quatre des sept prompts livrés redisent une commande CLI.

    Ils occupaient la moitié du catalogue et donnaient l'impression d'un
    produit qui ne sait faire que du diagnostic. Ils restent livrés et
    invocables — un projet qui les a ne les perd pas — mais sortent de la vue
    par défaut, en nommant la commande qui les remplace.
    """

    _REPLACED: ClassVar[dict[str, str]] = {
        "grimoire-status": "grimoire status",
        "grimoire-health-check": "grimoire doctor",
        "grimoire-self-heal": "grimoire doctor --fix",
        "grimoire-pre-push": "grimoire check",
    }

    @pytest.mark.parametrize(("slug", "command"), sorted(_REPLACED.items()))
    def test_a_replaced_prompt_names_its_command(self, tmp_path: Path, slug: str, command: str) -> None:
        entry = registry.find_workflow(tmp_path, slug)

        assert entry is not None
        assert entry.is_deprecated
        assert entry.deprecated_by == command

    def test_the_default_catalogue_hides_them(self, tmp_path: Path) -> None:
        visible = {e.slug for e in registry.load_workflows(tmp_path, include_deprecated=False)}

        assert not (visible & set(self._REPLACED))

    def test_they_remain_reachable_on_demand(self, tmp_path: Path) -> None:
        """Cacher n'est pas supprimer : `--all` et `show` doivent les rendre."""
        every = {e.slug for e in registry.load_workflows(tmp_path)}

        assert set(self._REPLACED) <= every

    def test_the_synthesising_prompts_are_kept(self, tmp_path: Path) -> None:
        """changelog, dream et session-bootstrap ne redisent aucune commande.

        Ils lisent l'historique et la mémoire pour en tirer une synthèse ; le
        SDK n'a rien qui fasse ça. Les supprimer retirerait une capacité.
        """
        visible = {e.slug for e in registry.load_workflows(tmp_path, include_deprecated=False)}

        assert {"grimoire-changelog", "grimoire-dream", "grimoire-session-bootstrap"} <= visible

    def test_no_orchestration_is_deprecated(self, tmp_path: Path) -> None:
        for entry in registry.load_workflows(tmp_path):
            if entry.is_orchestration:
                assert not entry.is_deprecated, entry.slug

    def test_is_deprecated_file_reads_the_frontmatter(self, tmp_path: Path) -> None:
        plain = _write(tmp_path / "a.md", "---\nkind: command\ndescription: 'x'\n---\n")
        marked = _write(tmp_path / "b.md", "---\nkind: command\ndeprecated_by: 'grimoire status'\n---\n")

        assert registry.is_deprecated_file(plain) is False
        assert registry.is_deprecated_file(marked) is True

    def test_a_missing_file_is_not_deprecated(self, tmp_path: Path) -> None:
        """La sonde du scaffold ne doit pas exploser sur un chemin absent."""
        assert registry.is_deprecated_file(tmp_path / "absent.md") is False


class TestDeclaredPatterns:
    """Les patterns déclarés doivent exister au catalogue.

    Le champ existait et n'était rempli nulle part. Les valeurs posées le sont
    d'après le texte du pattern et celui du workflow ; ce test empêche qu'un
    identifiant inventé passe.
    """

    @pytest.mark.parametrize(
        ("slug", "pattern"),
        [
            ("boomerang-orchestration", "ORC-01"),
            ("subagent-orchestration", "ORC-01"),
            ("state-checkpoint", "ORC-09"),
        ],
    )
    def test_declared_pattern(self, tmp_path: Path, slug: str, pattern: str) -> None:
        entry = registry.find_workflow(tmp_path, slug)

        assert entry is not None
        assert pattern in entry.patterns

    def test_every_declared_pattern_looks_like_a_catalogue_id(self, tmp_path: Path) -> None:
        import re

        for entry in registry.load_workflows(tmp_path):
            for pattern in entry.patterns:
                assert re.fullmatch(r"[A-Z]{3}-\d{2}", pattern), f"{entry.slug}: {pattern}"


class TestDeclaredTeams:
    @pytest.mark.parametrize(
        ("slug", "team"),
        [
            ("boomerang-orchestration", "team-build"),
            ("subagent-orchestration", "team-build"),
            ("incident-response", "team-ops"),
        ],
    )
    def test_declared_team(self, tmp_path: Path, slug: str, team: str) -> None:
        entry = registry.find_workflow(tmp_path, slug)

        assert entry is not None
        assert entry.team == team

    def test_every_declared_team_resolves(self, tmp_path: Path) -> None:
        """Déclarer une équipe absente donnerait un `show` qui promet dans le vide."""
        from grimoire.workflows.teams import load_team

        for entry in registry.load_workflows(tmp_path):
            if entry.team:
                assert load_team(tmp_path, entry.team) is not None, entry.slug

    @pytest.mark.parametrize("slug", ["party-mode", "state-checkpoint", "repo-map-generator"])
    def test_a_workflow_without_a_grounded_team_declares_none(self, tmp_path: Path, slug: str) -> None:
        """Mieux vaut aucune équipe qu'une équipe devinée.

        Ces trois-là ne nomment pas de roster et ne recoupent la spécialité
        d'aucune équipe : leur en attribuer une serait une invention.
        """
        entry = registry.find_workflow(tmp_path, slug)

        assert entry is not None
        assert entry.team == ""
