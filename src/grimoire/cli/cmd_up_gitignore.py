"""Le pas ``gitignore`` de ``grimoire up`` : les lignes du kit dans le ``.gitignore`` d'un projet installé.

Extrait de :mod:`grimoire.cli.cmd_up` (fichier sous cliquet de taille) ; la seule appelante est
:func:`~grimoire.cli.cmd_up.run_up_pipeline`.

Pourquoi un pas à part : :func:`~grimoire.core.standard_generation.ensure_grimoire_gitignore` n'était appelée que par
la mise en place du profil standard, que ``up`` saute (``--no-standard``, profil préservé, erreur) et que le refresh du
kit ne remplace pas (un ``.gitignore`` existant est un fichier du projet). Un projet installé avant l'ajout de
nouvelles lignes (verrous) ne les recevait donc jamais.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.cli.cmd_up import _UpState

__all__ = ["GITIGNORE_NOTICE", "step_gitignore"]

GITIGNORE_NOTICE = ".gitignore : lignes du kit ajoutées"


def step_gitignore(state: _UpState, target: Path, *, dry_run: bool, blocked: bool) -> None:
    """Complète le ``.gitignore`` par les lignes du kit ; ne dit rien quand il n'y a rien à ajouter.

    Idempotent, jamais bloquant : la fonction partagée ne lève pas (lien symbolique, fichier non UTF-8 et erreur
    d'écriture laissent le fichier intact) et les lignes de l'utilisateur ne sont ni modifiées ni réordonnées.
    """
    if blocked or dry_run:
        return
    from grimoire.cli.cmd_up import StepResult
    from grimoire.core.standard_generation import ensure_grimoire_gitignore

    if ensure_grimoire_gitignore(target) is not None:
        state.steps.append(StepResult("gitignore", "done", GITIGNORE_NOTICE))
        state.actions.append(GITIGNORE_NOTICE)
