"""What the kit cannot know ahead of time must be resolved at install time.

Four defects shared one shape: a file shipped with a value written in advance
where only the install knows the answer — a routing map from another archetype
family, a project name left as ``{{project_name}}``, an example roster sitting
where a live one belongs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, _render_placeholders


def _install(target: Path, archetypes: tuple[str, ...] = ("infra-ops",)) -> None:
    scaffolder = ProjectScaffolder(
        target,
        project_name="rendering-test",
        user_name="Test User",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=ResolvedArchetype(
            archetype=archetypes[0],
            stack_agents=(),
            feature_agents=(),
            reason="test",
            archetypes=archetypes,
        ),
        backend="local",
    )
    scaffolder.execute(scaffolder.plan())


def _installed_tags(root: Path) -> set[str]:
    manifest = (root / "_grimoire" / "kit" / "agent-manifest.csv").read_text(encoding="utf-8")
    return {line.split(",", 1)[0] for line in manifest.splitlines()[1:] if line.strip()}


def test_routing_map_names_the_agents_this_project_got(tmp_path: Path) -> None:
    """The entry-point persona shipped eleven agents from another archetype."""
    _install(tmp_path, ("platform-engineering", "infra-ops"))
    concierge = (tmp_path / "_grimoire" / "kit" / "agents" / "concierge.md").read_text(encoding="utf-8")
    block = concierge[concierge.index("<agents>"):concierge.index("</agents>")]
    routed = set(re.findall(r'<agent tag="([\w-]+)"', block))
    installed = _installed_tags(tmp_path)

    assert routed, "la carte de routage est vide"
    assert not routed - installed, f"agents routés mais non installés : {sorted(routed - installed)}"
    assert not installed - routed, f"agents installés mais absents de la carte : {sorted(installed - routed)}"


def test_routing_map_follows_the_archetype(tmp_path: Path) -> None:
    """A different archetype must produce a different map, or nothing is generated."""
    minimal = tmp_path / "minimal"
    infra = tmp_path / "infra"
    _install(minimal, ("minimal",))
    _install(infra, ("infra-ops",))

    def _tags(root: Path) -> set[str]:
        text = (root / "_grimoire" / "kit" / "agents" / "concierge.md").read_text(encoding="utf-8")
        return set(re.findall(r'<agent tag="([\w-]+)"', text))

    assert _tags(minimal) != _tags(infra)
    assert "ops-engineer" in _tags(infra)
    assert "ops-engineer" not in _tags(minimal)


@pytest.mark.parametrize("name", [
    "failure-museum.md", "contradiction-log.md", "decisions-log.md",
    "dependency-graph.md", "network-topology.md", "oss-references.md", "handoff-log.md",
])
def test_memory_seeds_carry_no_unresolved_placeholder(tmp_path: Path, name: str) -> None:
    """Both spellings shipped raw: ``{{project_name}}`` and ``$project_name``."""
    _install(tmp_path)
    text = (tmp_path / "_grimoire" / "_memory" / name).read_text(encoding="utf-8")
    assert "{{project_name}}" not in text
    assert "{{init_date}}" not in text
    assert "$project_name" not in text
    assert "rendering-test" in text


def test_legend_documents_placeholders_without_becoming_one() -> None:
    """Substituting inside the legend turned the documentation into nonsense."""
    text = (
        "<!-- grimoire:legend\n"
        "  {{dev_agent_name}} - Nom de l'agent développement\n"
        "-->\n"
        "Le fixer est {{dev_agent_name}}.\n"
    )
    rendered = _render_placeholders(text, {"dev_agent_name": "Stack"})

    assert "{{dev_agent_name}} - Nom de l'agent développement" in rendered, "la légende a été substituée"
    assert "Le fixer est Stack." in rendered, "le corps n'a pas été substitué"


def test_run_models_are_not_installed_as_live_workflows(tmp_path: Path) -> None:
    """``workflow-graph.yaml`` is a shape to copy, not this project's routing."""
    _install(tmp_path)
    workflows = tmp_path / "_grimoire" / "kit" / "workflows"

    assert not (workflows / "workflow-graph.yaml").exists()
    assert (workflows / "examples" / "workflow-graph.yaml").is_file()
    assert (workflows / "incident-response.md").is_file(), "un vrai workflow a été déplacé"

    model = (workflows / "examples" / "workflow-graph.yaml").read_text(encoding="utf-8")
    assert "CECI EST UN EXEMPLE" in model


def test_fix_apply_does_not_relist_what_it_just_wrote(tmp_path: Path) -> None:
    """The remaining plan was computed before the writes and printed after them."""
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    _install(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["standard", "init", str(tmp_path)]).exit_code == 0

    brief = tmp_path / "_grimoire" / "standard" / "mission-brief.md"
    assert brief.is_file()
    brief.unlink()

    result = runner.invoke(app, ["standard", "fix", str(tmp_path), "--apply"])
    assert "wrote _grimoire/standard/mission-brief.md" in result.output
    assert brief.is_file()

    outstanding = [line for line in result.output.splitlines() if line.strip().startswith("!")]
    assert not any("mission-brief" in line for line in outstanding), (
        "un fichier écrit avec succès est réaffiché comme manquant :\n" + result.output
    )
