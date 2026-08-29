"""Catalogue de workflows — découverte, métadonnées et manifestes d'équipe.

Un workflow n'est pas du câblage CLI : c'est un artefact avec une nature, des
agents, parfois une équipe, et un tier de résolution. Ce paquet porte ce
modèle ; :mod:`grimoire.cli.cmd_workflows` ne fait que le rendre.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__all__ = ["WorkflowEntry", "load_team", "load_teams", "load_workflows"]

if TYPE_CHECKING:
    from grimoire.workflows.registry import WorkflowEntry as WorkflowEntry
    from grimoire.workflows.registry import load_workflows as load_workflows
    from grimoire.workflows.teams import load_team as load_team
    from grimoire.workflows.teams import load_teams as load_teams

#: Sous-module qui porte chaque export, pour que la résolution paresseuse sache
#: où aller sans importer les deux.
_ORIGIN = {
    "WorkflowEntry": "registry",
    "load_workflows": "registry",
    "load_team": "teams",
    "load_teams": "teams",
}


def __getattr__(name: str) -> Any:
    """Résolution paresseuse des exports déclarés.

    ``__all__`` doit nommer des objets atteignables, sinon ``import *`` lève —
    et le bloc ``TYPE_CHECKING`` ci-dessus les rend visibles à l'analyse
    statique, qui ne suit pas ``__getattr__``. Les importer au chargement du
    paquet chargerait ruamel.yaml pour un appelant qui ne veut que le registre.
    """
    origin = _ORIGIN.get(name)
    if origin is not None:
        return getattr(importlib.import_module(f"grimoire.workflows.{origin}"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
