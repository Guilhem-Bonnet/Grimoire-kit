"""Projection 2D des embeddings réellement stockés — ou rien.

La page Mémoire affiche un nuage de points censé montrer comment les souvenirs
se regroupent. Il était tiré au sort : ``random.Random(42)`` autour de cinq
centres, avec les vrais noms de types dessus. Un nuage inventé se lit
exactement comme un vrai, et celui-ci mentait sur la seule chose qu'il
prétendait montrer.

Ce module le remplace par la projection des vecteurs que le backend possède
vraiment. Deux règles :

* **rien plutôt qu'un dessin** — un backend lexical n'a pas d'embedding ; la
  réponse est ``None``, et la page dit pourquoi ;
* **aucune dépendance nouvelle** — l'analyse en composantes principales tient
  en une trentaine de lignes par itération de puissance. Ajouter numpy pour un
  panneau décoratif serait payer cher un aveu de paresse.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

#: Assez de points pour voir une structure, assez peu pour qu'une page web les
#: dessine sans effort et que la projection reste instantanée.
DEFAULT_SAMPLE = 300

#: L'itération de puissance converge vite sur des données réelles ; ce plafond
#: n'existe que pour garantir la terminaison.
_POWER_ITERATIONS = 64
_EPSILON = 1e-12


def _mean(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    total = [0.0] * dim
    for vec in vectors:
        for i, value in enumerate(vec):
            total[i] += value
    return [t / len(vectors) for t in total]


def _centre(vectors: list[list[float]]) -> list[list[float]]:
    mean = _mean(vectors)
    return [[value - mean[i] for i, value in enumerate(vec)] for vec in vectors]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(_dot(vec, vec))
    if norm < _EPSILON:
        return vec
    return [v / norm for v in vec]


def _principal_axis(rows: list[list[float]], exclude: list[list[float]]) -> list[float]:
    """Axe de plus grande variance, par itération de puissance.

    On travaille sur ``Xᵀ(Xv)`` sans jamais former la matrice de covariance :
    elle serait de taille dimension², soit 147 456 flottants pour un modèle à
    384 dimensions, pour un résultat identique.
    """
    dim = len(rows[0])
    # Départ déterministe : deux exécutions sur les mêmes données doivent
    # rendre le même nuage, sinon la page bouge à chaque régénération.
    axis = _normalise([math.sin(i + 1) for i in range(dim)])
    for _ in range(_POWER_ITERATIONS):
        acc = [0.0] * dim
        for row in rows:
            weight = _dot(row, axis)
            for i, value in enumerate(row):
                acc[i] += weight * value
        for previous in exclude:  # orthogonalisation vis-à-vis des axes déjà pris
            projection = _dot(acc, previous)
            for i in range(dim):
                acc[i] -= projection * previous[i]
        nxt = _normalise(acc)
        if _dot(nxt, axis) > 1 - 1e-9:
            return nxt
        axis = nxt
    return axis


def project_2d(vectors: list[list[float]]) -> list[tuple[float, float]]:
    """Coordonnées 2D des vecteurs, par ACP sur les deux premiers axes."""
    if not vectors:
        return []
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        msg = "vecteurs de dimensions hétérogènes"
        raise ValueError(msg)
    rows = _centre(vectors)
    first = _principal_axis(rows, [])
    second = _principal_axis(rows, [first]) if dim > 1 else [0.0] * dim
    return [(round(_dot(r, first), 4), round(_dot(r, second), 4)) for r in rows]


def collect(project_root: Path, *, limit: int = DEFAULT_SAMPLE) -> list[tuple[str, list[float]]]:
    """Embeddings du store du projet, ou rien si le backend n'en tient pas."""
    # Lecture stricte au root du projet : un projet non initialisé ne doit pas
    # hériter de la config d'un parent, comme pour le lien mémoire.
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return []
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.memory.manager import MemoryManager

        config = GrimoireConfig.from_yaml(config_path)
        manager = MemoryManager.from_config(config, project_root=project_root)
        return list(manager.backend.vectors(limit=limit))
    except Exception:
        # Un store injoignable — service éteint, extra non installé, config
        # cassée — n'est pas une panne d'affichage : la page dira simplement
        # qu'il n'y a pas de projection.
        return []


def projection(
    project_root: Path,
    *,
    limit: int = DEFAULT_SAMPLE,
    types_by_id: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Nuage 2D des embeddings réels, ou ``None`` quand il n'y en a pas.

    ``None`` n'est pas un échec : c'est la réponse exacte pour un backend
    lexical ou fichier. La page affiche alors son état vide, qui dit la vérité.
    """
    pairs = collect(project_root, limit=limit)
    if len(pairs) < 2:  # une ACP sur un point ne veut rien dire
        return None
    ids = [entry_id for entry_id, _ in pairs]
    coords = project_2d([vector for _, vector in pairs])
    types = types_by_id or {}
    return {
        "is_demo": False,
        "dimensions": len(pairs[0][1]),
        "sampled": len(pairs),
        "points": [
            {"id": ids[i], "x": x, "y": y, "type": types.get(ids[i], "memory")}
            for i, (x, y) in enumerate(coords)
        ],
        "note": "projection ACP des embeddings réellement stockés",
    }
