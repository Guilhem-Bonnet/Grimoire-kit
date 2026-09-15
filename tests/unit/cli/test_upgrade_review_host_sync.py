"""Projection réelle du mission pack `upgrade-review` par `grimoire host sync` (issue #520, PR 1).

Doctrine des artefacts du kit : un prompt mission pack (`framework/copilot/
prompts/*.prompt.md`) est la seule voie réellement projetée par les hôtes
(`collect_commands`/`_prompt_commands`) vers `.github/prompts/*.prompt.md`
(Copilot) et `.claude/commands/*.md` (Claude Code) ; un skill (`archetypes/
<archetype>/skills/*.md`, attaché via le frontmatter `skills:` d'un agent)
est projeté vers `.claude/skills/<slug>/SKILL.md`. Ce test scaffolde un
projet jetable en archétype `meta` (le seul qui déclare un agent concierge)
et vérifie que les deux mécanismes déposent bien les fichiers attendus après
`grimoire host sync` — jamais un mécanisme parallèle inventé pour ce lot.
"""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def _init_meta_project(tmp_path: Path) -> Path:
    env = {**os.environ, "GRIMOIRE_NO_COCKPIT": "1"}
    result = runner.invoke(
        app,
        [
            "init", str(tmp_path),
            "--name", "fixture-upgrade-review",
            "--archetype", "meta",
            "--backend", "local",
            "--no-cockpit",
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output
    return tmp_path


def test_the_upgrade_review_prompt_is_projected_by_host_sync(tmp_path: Path) -> None:
    project = _init_meta_project(tmp_path)

    result = runner.invoke(app, ["host", "sync", "--project-root", str(project), "--host", "all"])
    assert result.exit_code == 0, result.output

    claude_command = project / ".claude" / "commands" / "grimoire-upgrade-review.md"
    assert claude_command.is_file(), "commande Claude Code non projetée par host sync"
    assert "upgrade-flow review" in claude_command.read_text(encoding="utf-8")


def test_the_upgrade_review_prompt_is_projected_for_copilot(tmp_path: Path) -> None:
    project = _init_meta_project(tmp_path)

    result = runner.invoke(
        app, ["host", "sync", "--project-root", str(project), "--host", "copilot", "--force-host"]
    )
    assert result.exit_code == 0, result.output

    copilot_prompt = project / ".github" / "prompts" / "grimoire-upgrade-review.prompt.md"
    assert copilot_prompt.is_file(), "prompt Copilot non projeté par host sync"
    assert "upgrade-flow review" in copilot_prompt.read_text(encoding="utf-8")


def test_the_upgrade_review_skill_is_attached_to_the_concierge(tmp_path: Path) -> None:
    """Un skill déclaré dans le frontmatter `skills:` d'un agent Claude Code est plié
    inline dans le fichier de CET agent (`_attached_skill_section`, `claude_code.py`) —
    jamais un `.claude/skills/<slug>/SKILL.md` séparé, réservé aux skills transversaux
    qu'aucun agent ne revendique (issue #375/#406)."""
    project = _init_meta_project(tmp_path)

    result = runner.invoke(app, ["host", "sync", "--project-root", str(project), "--host", "all"])
    assert result.exit_code == 0, result.output

    concierge_source = project / "_grimoire" / "kit" / "agents" / "concierge.md"
    assert concierge_source.is_file(), "agent concierge absent de l'archétype meta scaffoldé"
    assert "upgrade-review" in concierge_source.read_text(encoding="utf-8"), (
        "le concierge ne déclare pas le skill upgrade-review dans son frontmatter"
    )

    concierge_claude = project / ".claude" / "agents" / "concierge.md"
    assert concierge_claude.is_file()
    projected = concierge_claude.read_text(encoding="utf-8")
    assert "## Compétence attachée : upgrade-review" in projected
    assert "upgrade-flow review" in projected


def test_upgrade_flow_review_context_prints_a_ready_to_paste_block_without_a_slash_command(
    tmp_path: Path,
) -> None:
    """Pour un hôte sans slash command : le contexte de l'étape 1 en un bloc prêt à coller."""
    project = _init_meta_project(tmp_path)

    result = runner.invoke(app, ["upgrade-flow", "review", "--project-root", str(project)])

    assert result.exit_code == 0, result.output
    assert "Revue de mise à jour" in result.output
    assert "Aucun run" in result.output or "run" in result.output
