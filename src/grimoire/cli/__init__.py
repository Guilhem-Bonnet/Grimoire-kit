"""Grimoire CLI — thin wrapper over core."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

#: ``app`` n'est volontairement pas exporté. Le paquet contient un sous-module
#: ``grimoire.cli.app``, et l'instance Typer qu'il porte s'appelle également
#: ``app`` : dès que le sous-module est importé — ce que fait tout appel de la
#: CLI — il se lie sur le paquet et masque l'instance. Un nom dont la valeur
#: dépend de l'ordre des imports n'est pas une API publique. L'instance reste
#: atteignable en clair par ``from grimoire.cli.app import app``.
__all__ = ["cli"]

if TYPE_CHECKING:
    from grimoire.cli.app import cli as cli


def __getattr__(name: str) -> Any:
    """Résolution paresseuse des exports déclarés.

    ``__all__`` doit nommer des objets atteignables, sinon ``import *`` lève.
    Les importer au chargement du paquet chargerait toute la CLI — Typer, Rich
    et la surface des commandes — y compris pour un appelant qui ne veut que
    ``grimoire.cli.cmd_hooks``. Le coût est payé au premier accès.
    """
    if name in __all__:
        return getattr(importlib.import_module("grimoire.cli.app"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
