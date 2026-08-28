"""La référence CLI doit citer chaque commande que la CLI expose.

Une commande livrée mais absente de la documentation est invisible pour
l'utilisateur : elle existe et personne ne peut la trouver. Le contrôle est
mécanique, donc il tient — sept commandes de premier niveau manquaient au
moment où ce test a été écrit (`blueprint`, `cadrage`, `cockpit`,
`context-pack`, `debugger`, `update`, `workflows`), plus les groupes `memory`
et `hooks`.

Le test énumère l'application Typer plutôt que la sortie de ``--help`` : pas
de sous-processus, pas de dépendance à la largeur du terminal ni au rendu Rich.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.cli.app import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REFERENCE = _REPO_ROOT / "docs" / "cli-reference.md"


def _visible_top_level_names() -> set[str]:
    """Noms exposés à l'utilisateur : commandes et groupes, alias masqués exclus."""
    names: set[str] = set()
    for command in app.registered_commands:
        if getattr(command, "hidden", False):
            continue
        name = command.name or (command.callback.__name__.replace("_", "-") if command.callback else "")
        if name:
            names.add(name)
    for group in app.registered_groups:
        if getattr(group, "hidden", False):
            continue
        if group.name:
            names.add(group.name)
    return names


@pytest.mark.skipif(not _REFERENCE.is_file(), reason="dépôt source uniquement")
def test_every_visible_command_appears_in_the_cli_reference() -> None:
    reference = _REFERENCE.read_text(encoding="utf-8")
    undocumented = sorted(n for n in _visible_top_level_names() if f"grimoire {n}" not in reference)
    assert not undocumented, (
        "commandes exposées mais absentes de docs/cli-reference.md : "
        f"{undocumented} — documenter, ou masquer la commande si elle n'est pas destinée à l'utilisateur"
    )
