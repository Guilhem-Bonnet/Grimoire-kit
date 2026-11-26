"""Le point de passage commun des gestes d'autorité du cockpit qui exigent la clé de contrôle.

Un geste d'autorité (mandat, pilote automatique, clés de fournisseurs, acceptation en lot) est celui d'un humain :
il exige la clé de contrôle, et il ne se dicte pas — seul un clic à l'écran le tranche. Ce module réunit les deux
refus, dans l'ordre : pas de dictée, puis clé de contrôle. Les routes l'appellent au lieu de répéter chacune la
vérification de la clé : une route qui exige la clé refuse donc aussi un texte dicté, sans copie à maintenir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AUTHORITY_DICTATION_REFUSAL", "require_human_key"]

AUTHORITY_DICTATION_REFUSAL = "un geste d'autorité ne se dicte pas : il se confirme par un clic à l'écran"


def require_human_key(project_root: Path, body: dict[str, Any], refusal: str) -> None:
    """Refuse (rien d'écrit) un corps issu d'une dictée (:class:`ValueError`, 400), puis une clé de contrôle absente
    ou invalide (:class:`PermissionError`, 403, message *refusal*)."""
    from grimoire.missions.mandate import verify_control_key
    from grimoire.voice.dictation import sent_as_dictation

    if sent_as_dictation(project_root, body):
        raise ValueError(AUTHORITY_DICTATION_REFUSAL)
    if not verify_control_key(body.get("control_key")):
        raise PermissionError(refusal)
