"""Un validateur de schéma minimal, strict, pour les sorties de modèle (épique #1477, lot B2).

Sous-ensemble volontairement petit de JSON Schema : ``type`` (object, array, string, boolean), ``properties`` et ``required``
(un objet n'a JAMAIS de champ hors ``properties``), ``items`` et ``maxItems``, ``minLength`` / ``maxLength``, ``enum``.
Toute entorse est une :class:`~grimoire.tools.atelier_engines.transport.EngineSortie` au message fixe (le nom du champ,
jamais sa valeur : une sortie hostile ne se retrouve ni dans une note ni dans un journal).
"""

from __future__ import annotations

import json
from typing import Any

from grimoire.tools.atelier_engines.transport import EngineSortie

__all__ = ["MAX_SORTIE_CHARS", "decoder", "valider"]

#: Une sortie de modèle plus longue est refusée avant tout décodage.
MAX_SORTIE_CHARS = 20_000
_PROFONDEUR_MAX = 6


def decoder(texte: str) -> Any:
    """Décode le texte JSON d'un modèle ; une clôture de code autour de l'objet est tolérée, rien d'autre."""
    if not isinstance(texte, str) or len(texte) > MAX_SORTIE_CHARS:
        raise EngineSortie("sortie du modèle absente ou trop longue : refusée")
    brut = texte.strip()
    if brut.startswith("```") and brut.endswith("```"):
        brut = brut.strip("`").removeprefix("json").strip()
    try:
        return json.loads(brut)
    except (ValueError, RecursionError):
        raise EngineSortie("sortie du modèle : JSON invalide") from None


def valider(valeur: Any, schema: dict[str, Any], chemin: str = "sortie", profondeur: int = 0) -> None:
    """Lève :class:`EngineSortie` si ``valeur`` n'est pas exactement ce que ``schema`` décrit."""
    if profondeur > _PROFONDEUR_MAX:
        raise EngineSortie(f"{chemin} : imbrication trop profonde")
    kind = schema["type"]
    if kind == "object":
        if not isinstance(valeur, dict):
            raise EngineSortie(f"{chemin} : un objet est attendu")
        props: dict[str, Any] = schema["properties"]
        extra = [k for k in valeur if k not in props]
        if extra:
            raise EngineSortie(f"{chemin} : champ en trop")  # jamais le nom ni la valeur : ils viennent du modèle
        for name in schema.get("required", ()):
            if name not in valeur:
                raise EngineSortie(f"{chemin}.{name} : champ manquant")
        for name, item in valeur.items():
            valider(item, props[name], f"{chemin}.{name}", profondeur + 1)
    elif kind == "array":
        if not isinstance(valeur, list):
            raise EngineSortie(f"{chemin} : une liste est attendue")
        if len(valeur) > schema.get("maxItems", 50):
            raise EngineSortie(f"{chemin} : trop d'éléments")
        for i, item in enumerate(valeur):
            valider(item, schema["items"], f"{chemin}[{i}]", profondeur + 1)
    elif kind == "string":
        if not isinstance(valeur, str):
            raise EngineSortie(f"{chemin} : un texte est attendu")
        if not schema.get("minLength", 0) <= len(valeur) <= schema.get("maxLength", 2000):
            raise EngineSortie(f"{chemin} : texte hors bornes")
        if "enum" in schema and valeur not in schema["enum"]:
            raise EngineSortie(f"{chemin} : valeur hors de la liste fermée")
    elif kind == "boolean":
        if not isinstance(valeur, bool):
            raise EngineSortie(f"{chemin} : un booléen est attendu")
    else:  # pragma: no cover — un schéma écrit par le programme, jamais par un modèle
        raise ValueError(f"type de schéma inconnu : {kind}")
