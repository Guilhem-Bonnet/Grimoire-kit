"""Empreinte du blueprint d'un run, dérive et migration (W2-04).

Sans empreinte, ``resume`` recharge le fichier blueprint tel qu'il est AUJOURD'HUI
et juge la sortie contre lui : un node déjà franchi dont le contrat a changé, ou
un ordre modifié, passe inaperçu. Ce module est pur (aucune lecture, aucune
écriture) : l'engine lui donne l'ordre et les contrats compilés.

Règle de dérive : le contrat, la présence et la position des nodes DÉJÀ FRANCHIS
sont comparés. Le contrat du node courant et des suivants peut être retouché sans
bloquer le run (le courant est ré-enregistré à son franchissement). En revanche le
run avance sur l'ordre qu'il a figé : un node retiré du blueprint (courant ou
suivant) ou AJOUTÉ à lui (jamais ouvert par le run) bloque jusqu'à ``flow migrate``
ou au rétablissement du blueprint.
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
    "blueprint_divergence",
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
    """``None`` si le blueprint colle encore au run ; sinon le constat ``blueprint_drift``.

    Nodes franchis : contrat, présence, position. Nodes courant et suivants : seule
    leur présence compte (``removed_nodes``) ; un node du blueprint absent de
    ``stored_order`` est ``added_nodes`` (jamais ouvert par un run qui avance sur
    son ordre figé).

    ``stored_digests`` vide (run antérieur à W2-04) : rien à comparer, ``None`` —
    l'appelant distingue ce cas « non vérifié » avant d'appeler.
    """
    removed = [n for n in stored_order if n not in new_contracts]
    added = [n for n in new_order if n not in stored_order]
    changed = [
        n
        for n in crossed
        if n in new_contracts and n in stored_digests and contract_digest(new_contracts[n]) != stored_digests[n]
    ]
    reordered = list(new_order[: len(crossed)]) != list(crossed) and not any(n in removed for n in crossed)
    if not (removed or added or changed or reordered):
        return None
    return {
        "kind": DRIFT_KIND,
        "changed_nodes": changed,
        "removed_nodes": removed,
        "added_nodes": added,
        "reordered": reordered,
        "stored_order": list(stored_order),
        "current_order": list(new_order),
        "order_diff": _order_diff(stored_order, new_order),
    }


def blueprint_divergence(
    order_a: Sequence[str],
    contracts_a: Mapping[str, Mapping[str, Any]],
    order_b: Sequence[str],
    contracts_b: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Ce qui sépare deux blueprints compilés (nodes en plus, en moins, contrats modifiés, ordre), ou ``None``."""
    only_a = [n for n in order_a if n not in contracts_b]
    only_b = [n for n in order_b if n not in contracts_a]
    changed = [
        n for n in order_a if n in contracts_b and contract_digest(contracts_a[n]) != contract_digest(contracts_b[n])
    ]
    reordered = not (only_a or only_b) and list(order_a) != list(order_b)
    if not (only_a or only_b or changed or reordered):
        return None
    parts = []
    if only_a:
        parts.append(f"seulement dans le premier : {only_a}")
    if only_b:
        parts.append(f"seulement dans le second : {only_b}")
    if changed:
        parts.append(f"contrats modifiés : {changed}")
    if reordered:
        parts.append("ordre différent : " + " ".join(_order_diff(order_a, order_b)))
    return " ; ".join(parts)


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
