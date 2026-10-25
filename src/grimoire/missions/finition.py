"""Finition d'une tâche : valeur déclarée, ou héritée de la phase du projet (R24, issue #561).

Deux axes distincts (plan 2026-Q4, « Phase et finition ») : la **phase** du projet
(``exploration``, ``construction``, ``exploitation``) se déclare dans
``project-context.yaml`` (clé ``phase``, ou ``project.phase``) ; la **finition** du
livrable (``maquette`` ou ``peaufine``) se pose sur chaque tâche. Une tâche sans
finition explicite hérite de celle que sa phase implique ; une phase absente ou
inconnue n'implique rien (le kit ne devine jamais) et la finition reste vide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError

__all__ = ["FINITIONS", "PHASE_FINITION", "finition_for", "normalize_finition", "project_phase"]

#: Valeurs machine, sans accent (ADR-007 §6) ; le même ensemble que ``MissionTask.finition``.
FINITIONS: tuple[str, ...] = ("maquette", "peaufine")

#: Finition par défaut de chaque phase (tableau du plan 2026-Q4).
PHASE_FINITION: dict[str, str] = {
    "exploration": "maquette",
    "construction": "peaufine",
    "exploitation": "peaufine",
}

_ALIASES = {"peaufiné": "peaufine", "peaufinée": "peaufine", "peaufinee": "peaufine"}


def normalize_finition(value: str) -> str:
    """La valeur machine de *value* ; ``""`` reste ``""`` ; toute autre valeur est refusée."""
    cleaned = value.strip().lower()
    cleaned = _ALIASES.get(cleaned, cleaned)
    if cleaned and cleaned not in FINITIONS:
        raise GrimoireMissionError(
            f"finition inconnue : {value!r} — parmi {', '.join(FINITIONS)} (« peaufiné » est accepté)"
        )
    return cleaned


def project_phase(project_root: Path) -> str:
    """La phase déclarée dans ``project-context.yaml`` ; ``""`` si absente, illisible ou inconnue."""
    import yaml  # type: ignore[import-untyped]

    try:
        raw: Any = yaml.safe_load((project_root / "project-context.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):  # ValueError : UnicodeDecodeError d'un fichier non UTF-8
        return ""
    if not isinstance(raw, dict):
        return ""
    project = raw.get("project")
    declared = raw.get("phase") or (project.get("phase") if isinstance(project, dict) else "")
    phase = str(declared or "").strip().lower()
    return phase if phase in PHASE_FINITION else ""


def finition_for(project_root: Path, explicit: str = "") -> str:
    """La finition d'une tâche : celle demandée, sinon celle de la phase du projet, sinon ``""``."""
    chosen = normalize_finition(explicit)
    return chosen or PHASE_FINITION.get(project_phase(project_root), "")
