"""Contrat commun aux adapters de runtime externe.

Trois adapters existaient — CrewAI, LangGraph, Gas City — avec le même contrat de
fait recopié trois fois : un ``_slugify`` identique au bit près dans deux d'entre
eux, une méthode d'entrée nommée successivement ``import_flow``, ``import_graph``
puis ``convert``, et trois types de rapport sans surface commune. Écrire un
quatrième adapter par copie aurait figé cette divergence.

Ce module donne le contrat, sans imposer une implémentation :

- ``slugify`` est l'unique définition de la normalisation d'identifiant ;
- ``ImportReport`` est un protocole structurel, pas une dataclass : les trois
  rapports existants portent des champs propres à leur source (``tasks_converted``,
  ``nodes_converted``, ``verification_gates``) qui font partie de leur contrat
  publié. Exiger une dataclass commune les casserait sans rien apporter ;
- ``RecipeAdapter`` fixe la méthode d'entrée canonique ``to_recipe``.

Ce que ce module ne normalise **pas**, délibérément : la valeur de
``VerificationGate.blocking``. CrewAI et LangGraph posent ``blocking=False`` pour
que les tâches importées atterrissent en ``NEEDS_VERIFICATION`` au lieu de se
clôturer seules — c'est leur garde-fou, pas un oubli. Gas City pose
``blocking=True`` sur les molécules qui déclarent exiger une preuve. Les deux
sémantiques sont justes ; les aplatir affaiblirait l'une ou l'autre.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from grimoire.runtime.recipes import Recipe

__all__ = ["ImportReport", "RecipeAdapter", "slugify"]


def slugify(name: str) -> str:
    """Normalize a free-form name into an identifier fragment."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


@runtime_checkable
class ImportReport(Protocol):
    """What every adapter reports back about an import.

    ``ok`` is the only verdict callers should branch on. An adapter that cannot
    guarantee the imported definition declares its outputs must return
    ``ok is False`` rather than a recipe the caller would trust.
    """

    errors: list[str]

    @property
    def ok(self) -> bool: ...

    def to_dict(self) -> dict[str, Any]: ...


@runtime_checkable
class RecipeAdapter(Protocol):
    """Read an external workflow definition and project it into a Recipe.

    An adapter never executes anything: it converts a declaration. The runtime
    that owns the execution stays outside grimoire.
    """

    source_id: str

    def to_recipe(self, raw: Any, *, recipe_id_prefix: str = "") -> tuple[Recipe, ImportReport]: ...
