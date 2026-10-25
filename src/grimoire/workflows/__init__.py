"""Catalogue de workflows — découverte et métadonnées.

Un workflow n'est pas du câblage CLI : c'est un artefact avec une nature, des
agents, parfois une équipe, et un tier de résolution. Ce paquet porte ce
modèle ; :mod:`grimoire.cli.cmd_workflows` ne fait que le rendre.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__all__ = ["WorkflowEntry", "load_workflows"]

if TYPE_CHECKING:
    from grimoire.workflows.registry import WorkflowEntry as WorkflowEntry
    from grimoire.workflows.registry import load_workflows as load_workflows

#: Sous-module qui porte chaque export, pour que la résolution paresseuse sache
#: où aller sans importer le module.
_ORIGIN = {
    "WorkflowEntry": "registry",
    "load_workflows": "registry",
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
