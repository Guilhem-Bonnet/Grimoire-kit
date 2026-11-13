"""La double relecture des chemins réservés, au choix du projet.

Quand ``orchestrator.roles.merger.protected_gate`` vaut ``dual_review``, une PR qui touche un chemin réservé
(:data:`~grimoire.missions.merger.DEFAULT_PROTECTED_PATHS`, plus ``protected_paths``) n'escalade plus à l'humain :
le merger demande une **seconde** relecture, par une session distincte de celle du premier relecteur et dont la
famille de modèle diffère de celle du worker. Ce module choisit le modèle de cette seconde relecture dans le
registre de fournisseurs du projet — jamais un nom en dur — et nomme les chemins qu'aucune relecture ne peut juger.

Famille : le nom du modèle porte l'alias (``opus``, ``sonnet``, ``haiku``) ; un modèle inconnu est sa propre
famille, ce qui oblige le registre à déclarer des modèles distincts pour garantir l'indépendance.

Choix, à défaut d'un modèle d'une autre famille que celle du premier relecteur : un **autre** modèle de la même
famille que lui, toujours d'une famille différente de celle du worker. Le journal du lancement dit laquelle des deux
règles a tranché.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grimoire.providers.registry import ProviderRegistryError, read_registry

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "BASIS_OTHER_FAMILY",
    "BASIS_SAME_FAMILY",
    "HUMAN_ONLY_PATHS",
    "PROTECTED_GATES",
    "PROTECTED_GATE_DUAL_REVIEW",
    "PROTECTED_GATE_HUMAN",
    "DualReviewChoice",
    "choose_second_reviewer",
    "model_family",
]

PROTECTED_GATE_HUMAN = "human"
PROTECTED_GATE_DUAL_REVIEW = "dual_review"
PROTECTED_GATES: tuple[str, ...] = (PROTECTED_GATE_HUMAN, PROTECTED_GATE_DUAL_REVIEW)

#: Les chemins qui restent à l'humain quel que soit ``protected_gate`` : la politique elle-même, la configuration de
#: l'hôte et ses serveurs MCP, et les fichiers compilés ou binaires — qu'aucune relecture ne peut juger.
HUMAN_ONLY_PATHS: tuple[str, ...] = (
    "_grimoire/standard/*",
    ".claude/*",
    ".mcp.json",
    "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.dylib", "*.exe", "*.whl", "*.egg", "*.pth",
    "*sitecustomize*", "*usercustomize*",
)

#: Les familles que le nom d'un modèle peut porter, dans l'ordre où on les reconnaît.
_FAMILY_ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")
#: Le palier d'un modèle du registre : les moins chers d'abord, quand la politique n'impose pas de palier.
_TIER_ORDER = {"cheap": 0, "mid": 1, "strong": 2}

#: Le registre offrait une famille différente de celle du premier relecteur : elle a été préférée.
BASIS_OTHER_FAMILY = "other_family"
#: Le registre n'offrait pas d'autre famille : un autre modèle de la famille du premier relecteur a été pris.
BASIS_SAME_FAMILY = "same_family"


def model_family(model: str) -> str:
    """La famille d'un modèle, lue dans son nom ; un nom sans alias connu est sa propre famille."""
    lowered = model.lower()
    return next((alias for alias in _FAMILY_ALIASES if alias in lowered), lowered)


@dataclass(frozen=True, slots=True)
class DualReviewChoice:
    """Le modèle de la seconde relecture, sa famille, et la règle qui l'a fait choisir."""

    model: str
    family: str
    basis: str
    first_family: str
    worker_family: str

    def to_dict(self) -> dict[str, str]:
        return {
            "model": self.model, "family": self.family, "family_basis": self.basis,
            "first_family": self.first_family, "worker_family": self.worker_family,
        }


def _registry_models(root: Path, tier: str) -> tuple[str, ...]:
    """Les modèles des fournisseurs activés du registre, palier *tier* si la politique en impose un."""
    try:
        providers = read_registry(root)
    except ProviderRegistryError:
        return ()
    found: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for provider in providers:
        if not provider.enabled:
            continue
        for model in provider.models:
            if model.id in seen or (tier and model.tier != tier):
                continue
            seen.add(model.id)
            found.append((_TIER_ORDER.get(model.tier, len(_TIER_ORDER)), len(found), model.id))
    return tuple(model_id for _order, _rank, model_id in sorted(found))


def choose_second_reviewer(
    root: Path, *, first_model: str, worker_model: str, tier: str = "",
) -> tuple[DualReviewChoice | None, str]:
    """``(choix, "")`` ou ``(None, raison)`` : le modèle de la seconde relecture, choisi dans le registre.

    Contrainte dure : la famille diffère de celle du worker. Parmi les modèles admissibles, une famille différente de
    celle du premier relecteur est préférée ; sinon un autre modèle de sa famille. Sans candidat, rien n'est choisi :
    l'appelant escalade à l'humain plutôt que de faire relire deux fois par la même famille.
    """
    first_family = model_family(first_model) if first_model else ""
    worker_family = model_family(worker_model) if worker_model else ""
    if not first_family or not worker_family:
        return None, "famille du worker ou du premier relecteur inconnue : l'indépendance des deux relectures n'est pas prouvable"
    models = _registry_models(root, tier)
    if not models:
        where = f"au palier {tier!r}" if tier else "pour ce projet"
        return None, f"le registre de fournisseurs n'offre aucun modèle {where}"
    eligible = [model for model in models if model_family(model) != worker_family]
    if not eligible:
        return None, (f"le registre n'offre aucun modèle d'une famille différente de celle du worker "
                      f"({worker_family})")
    other = next((model for model in eligible if model_family(model) != first_family), "")
    if other:
        return DualReviewChoice(other, model_family(other), BASIS_OTHER_FAMILY, first_family, worker_family), ""
    same = next((model for model in eligible if model_family(model) == first_family and model != first_model), "")
    if same:
        return DualReviewChoice(same, first_family, BASIS_SAME_FAMILY, first_family, worker_family), ""
    return None, (f"le registre n'offre ni modèle d'une autre famille que celle du premier relecteur ({first_family}), "
                  f"ni autre modèle de cette famille, distinct de la famille du worker ({worker_family})")
