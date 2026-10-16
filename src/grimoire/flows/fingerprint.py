"""Empreinte du blueprint d'un run, dérive et migration (W2-04).

Sans empreinte, ``resume`` recharge le fichier blueprint tel qu'il est AUJOURD'HUI
et juge la sortie contre lui : un node déjà franchi dont le contrat a changé, ou
un ordre modifié, passe inaperçu. Ce module est pur (aucune lecture, aucune
écriture) : l'engine lui donne l'ordre et les contrats compilés.

Règle de dérive : seuls les nodes DÉJÀ FRANCHIS sont comparés (contrat, présence,
position). Ce qui vient après le node courant peut évoluer sans bloquer le run ;
``flow migrate`` l'adopte explicitement.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "DRIFT_KIND",
    "contract_digest",
    "drift_of",
    "migration_refusal",
    "relative_blueprint_path",
    "resolve_blueprint_path",
]

DRIFT_KIND = "blueprint_drift"


def contract_digest(contract_dict: Mapping[str, Any]) -> str:
    """sha256 canonique d'un contrat de node compilé."""
    canonical = json.dumps(contract_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def relative_blueprint_path(blueprint_path: Path, project_root: Path) -> str:
    """Le chemin du blueprint relatif à la racine du projet — jamais un chemin absolu.

    Séparateurs POSIX (le run se déplace d'un poste à l'autre). Sur Windows, deux
    lecteurs différents n'ont pas de chemin relatif : le chemin reçu est gardé.
    """
    try:
        rel = os.path.relpath(blueprint_path.resolve(), project_root.resolve())
    except ValueError:
        return str(blueprint_path)
    return Path(rel).as_posix()


def resolve_blueprint_path(stored: str, project_root: Path) -> Path:
    """Inverse de :func:`relative_blueprint_path` ; un chemin absolu hérité d'un run ancien est rendu tel quel."""
    path = Path(stored)
    return path if path.is_absolute() else (project_root.resolve() / path).resolve()


def _order_diff(stored: Sequence[str], current: Sequence[str]) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(list(stored), list(current), lineterm="", n=0)
        if not line.startswith(("---", "+++", "@@"))
    ]


def drift_of(
    *,
    stored_order: Sequence[str],
    stored_digests: Mapping[str, str],
    crossed: Sequence[str],
    new_order: Sequence[str],
    new_contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """``None`` si les nodes franchis sont inchangés ; sinon le constat ``blueprint_drift``.

    ``stored_digests`` vide (run antérieur à W2-04) : rien à comparer, ``None`` —
    l'appelant distingue ce cas « non vérifié » avant d'appeler.
    """
    removed = [n for n in crossed if n not in new_contracts]
    changed = [
        n
        for n in crossed
        if n in new_contracts and n in stored_digests and contract_digest(new_contracts[n]) != stored_digests[n]
    ]
    reordered = list(new_order[: len(crossed)]) != list(crossed) and not removed
    if not (removed or changed or reordered):
        return None
    return {
        "kind": DRIFT_KIND,
        "changed_nodes": changed,
        "removed_nodes": removed,
        "reordered": reordered,
        "stored_order": list(stored_order),
        "current_order": list(new_order),
        "order_diff": _order_diff(stored_order, new_order),
    }


def migration_refusal(
    *, crossed: Sequence[str], current: str | None, new_order: Sequence[str], new_contracts: Mapping[str, Any]
) -> str | None:
    """La raison pour laquelle ``flow migrate`` refuse, ou ``None`` s'il peut adopter le nouvel ordre.

    Condition : les nodes franchis forment un préfixe identique du nouvel ordre ET
    le node courant vient juste après — rien de franchi ni d'en cours n'est perdu.
    """
    if current is None:
        return "aucun node courant : le run est terminé ou tous ses nodes sont déjà franchis, rien à migrer"
    head = list(new_order[: len(crossed)])
    if head != list(crossed):
        return (
            f"les nodes franchis {list(crossed)} ne forment pas un préfixe identique du nouvel ordre "
            f"{list(new_order)} : " + " ".join(_order_diff(crossed, head))
        )
    following = new_order[len(crossed)] if len(new_order) > len(crossed) else None
    if following != current or current not in new_contracts:
        return f"le node courant « {current} » ne suit plus les nodes franchis dans le nouvel ordre {list(new_order)}"
    return None
