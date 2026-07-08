"""Canaux de features — stable / beta / experimental.

Le kit expose ses capacités par canal de maturité :

- **stable** : contrat SemVer, activées d'office.
- **beta** : fonctionnelles et testées, opt-in par projet, journalisées —
  candidates à la promotion sur métriques d'usage (voir docs/rnd.md).
- **experimental** : surface R&D exploratoire, hors contrat, usage direct
  (CLI/outil) sans câblage dans le cycle agent.

L'état d'activation vit dans ``_grimoire/features.json`` du projet — même
patron que ``_grimoire/extensions/installed.json``. Une feature peut être un
simple drapeau (consulté par le code) ou porter une action d'activation
réelle (ex. ``stigmergy-hooks`` installe/retire des hooks).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATE_RELPATH = Path("_grimoire") / "features.json"

__all__ = [
    "FEATURES",
    "Feature",
    "feature_state",
    "is_enabled",
    "list_features",
    "set_enabled",
]


@dataclass(frozen=True)
class Feature:
    """Déclaration d'une feature à canal de maturité."""

    id: str
    name: str
    channel: str  # stable | beta | experimental
    description: str
    default_enabled: bool = False
    toggleable: bool = True
    doc: str = ""
    surfaces: tuple[str, ...] = field(default_factory=tuple)


# Registre des features à canal. Les capacités cœur (standard, mémoire,
# extensions, blueprints) sont stables et ne se listent pas ici : ce registre
# ne porte que ce qui est en cours de maturation ou togglable.
FEATURES: dict[str, Feature] = {
    f.id: f
    for f in (
        Feature(
            id="stigmergy",
            name="Stigmergy — coordination par phéromones",
            channel="beta",
            description=(
                "Board de signaux typés (NEED, ALERT, PROGRESS…) qui "
                "s'évaporent ; CLI grimoire stigmergy + vue live observatoire."
            ),
            default_enabled=True,
            toggleable=False,
            doc="docs/rnd.md",
            surfaces=("cli", "serve", "observatory"),
        ),
        Feature(
            id="stigmergy-hooks",
            name="Stigmergy — boucle automatique (hooks)",
            channel="beta",
            description=(
                "Émission et captation automatiques : SessionStart injecte "
                "les signaux actifs, PostToolUse dépose/renforce PROGRESS, "
                "Stop marque COMPLETE et purge. Non bloquants par construction."
            ),
            default_enabled=False,
            toggleable=True,
            doc="docs/cli-reference.md",
            surfaces=("hooks",),
        ),
    )
}


def _state_path(project_root: Path) -> Path:
    return project_root / STATE_RELPATH


def _load_state(project_root: Path) -> dict[str, Any]:
    path = _state_path(project_root)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(project_root: Path, state: dict[str, Any]) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_enabled(project_root: Path, feature_id: str) -> bool:
    """État effectif d'une feature pour un projet (défaut du canal sinon)."""
    feature = FEATURES.get(feature_id)
    if feature is None:
        return False
    state = _load_state(project_root)
    entry = state.get(feature_id)
    if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
        return bool(entry["enabled"])
    return feature.default_enabled


def set_enabled(project_root: Path, feature_id: str, enabled: bool) -> Feature:
    """Persiste l'activation d'une feature. Lève KeyError si inconnue."""
    feature = FEATURES[feature_id]
    if not feature.toggleable:
        msg = f"feature non togglable : {feature_id}"
        raise ValueError(msg)
    state = _load_state(project_root)
    state[feature_id] = {"enabled": enabled}
    _save_state(project_root, state)
    return feature


def feature_state(project_root: Path, feature_id: str) -> dict[str, Any]:
    """Vue sérialisable d'une feature (déclaration + état effectif)."""
    feature = FEATURES[feature_id]
    return {
        "id": feature.id,
        "name": feature.name,
        "channel": feature.channel,
        "description": feature.description,
        "doc": feature.doc,
        "surfaces": list(feature.surfaces),
        "toggleable": feature.toggleable,
        "enabled": is_enabled(project_root, feature_id),
    }


def list_features(project_root: Path) -> list[dict[str, Any]]:
    """Toutes les features à canal, avec leur état effectif pour le projet."""
    return [feature_state(project_root, fid) for fid in sorted(FEATURES)]
