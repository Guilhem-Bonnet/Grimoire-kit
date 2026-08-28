"""``__all__`` doit nommer des objets qui existent.

Deux paquets déclaraient un ``__all__`` sans rien importer :
``grimoire.cli`` annonçait ``app`` et ``cli``, ``grimoire.mcp`` annonçait
``main``. ``from grimoire.cli import *`` levait donc ``AttributeError`` —
l'import étoile est le seul chemin qui consomme ``__all__``, et c'est
exactement celui que personne n'essaie.

Le test parcourt les paquets du SDK plutôt que ces deux-là : la classe se
reproduit à chaque nouveau paquet qui déclare ses exports avant de les
importer.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import grimoire

_PACKAGES = sorted(
    module.name
    for module in pkgutil.iter_modules(grimoire.__path__, prefix="grimoire.")
    if module.ispkg
)


@pytest.mark.parametrize("package_name", _PACKAGES)
def test_declared_exports_exist(package_name: str) -> None:
    module = importlib.import_module(package_name)
    declared = getattr(module, "__all__", None)
    if declared is None:
        pytest.skip(f"{package_name} ne déclare pas d'exports")
    missing = [name for name in declared if not hasattr(module, name)]
    assert not missing, (
        f"{package_name}.__all__ nomme des objets absents : {missing} — "
        "`from ... import *` lève AttributeError"
    )


@pytest.mark.parametrize("package_name", _PACKAGES)
def test_star_import_succeeds(package_name: str) -> None:
    """Le contrat que ``__all__`` promet est ``import *`` — vérifié tel quel."""
    module = importlib.import_module(package_name)
    if getattr(module, "__all__", None) is None:
        pytest.skip(f"{package_name} ne déclare pas d'exports")
    namespace: dict[str, object] = {}
    exec(f"from {package_name} import *", namespace)  # noqa: S102
    for name in module.__all__:
        assert name in namespace, f"{package_name}.__all__ promet {name!r}, absent après import *"
