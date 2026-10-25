"""Vérificateur du pattern ``plan-then-execute`` (tri R32).

Beurer-Kellner et al. : une donnée non fiable ingérée ne doit plus pouvoir
déclencher d'action conséquente ; le plan est donc figé *avant* la lecture.
Deux contrôles sur le bloc ``plan_then_execute:`` de ``prompt-firewall.yaml`` :

- ``firewall.plan_not_frozen`` : ``freeze_plan_before_untrusted_read`` n'est pas
  ``true`` ;
- ``firewall.plan_deviation_allowed`` : ``actions_outside_plan`` n'est ni
  ``ask`` ni ``deny``.

Le bloc est optionnel pour un profil qui n'installe pas le pattern ; pour un profil
qui l'installe (``profile-map.yaml``, ``mapped_capabilities``, cumulatif), son absence
est un avertissement ``firewall.plan_block_missing`` : un pattern installé mais jamais
déclaré ne contrôlait rien. Ce qui s'applique réellement en session est le garde-fou
d'exécution (``tool_policy._untrusted_escalation``) ; ce contrôle vérifie
seulement que la déclaration du projet l'engage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check

PATTERN_ID = "plan-then-execute"


def _pattern_installed(profile: StandardProfile) -> bool:
    """Le pattern est-il installé au palier de *profile* (ou à un palier inférieur) ?"""
    from grimoire.core.agentic_standard import load_profile_map

    for raw in load_profile_map().get("profiles", []):
        if PATTERN_ID in (raw.get("mapped_capabilities") or ()):
            return True
        if str(raw.get("id")) == profile.id:
            return False
    return False

__all__ = ["_verify_plan_then_execute"]


def _verify_plan_then_execute(
    data: dict[str, Any],
    profile: StandardProfile,
    strict_profile: bool,
    result: StandardVerificationResult,
    rel_path: Path,
) -> None:
    """Pattern ``plan-then-execute`` (Beurer-Kellner et al.) : le plan est figé avant de lire du contenu non fiable.

    Bloc optionnel ``plan_then_execute:`` de ``prompt-firewall.yaml``. Absent, il
    n'y a rien à contrôler (le projet ne déclare pas ce pattern) ; présent, il
    doit dire que le plan est figé *avant* la lecture et qu'une action hors plan
    est refusée ou soumise à confirmation — jamais acceptée d'office. Le garde-fou
    d'exécution (``tool_policy._untrusted_escalation``) est ce qui s'applique
    réellement en session ; ce contrôle vérifie que la déclaration l'engage.
    """
    if "plan_then_execute" not in data:
        if _pattern_installed(profile):
            _add_check(
                result, "firewall.plan_block_missing", "warning",
                "The plan-then-execute pattern is installed at this profile but prompt-firewall.yaml has no "
                "plan_then_execute: block — nothing is declared, so nothing is checked.",
                path=rel_path,
            )
        return
    block = data["plan_then_execute"]
    severity = "error" if strict_profile else "warning"
    if not isinstance(block, dict) or block.get("freeze_plan_before_untrusted_read") is not True:
        _add_check(
            result, "firewall.plan_not_frozen", severity,
            "plan_then_execute must set freeze_plan_before_untrusted_read: true — a plan fixed after reading "
            "untrusted content is steered by it.",
            path=rel_path,
        )
    outside = block.get("actions_outside_plan") if isinstance(block, dict) else None
    if outside not in ("ask", "deny"):
        _add_check(
            result, "firewall.plan_deviation_allowed", severity,
            "plan_then_execute.actions_outside_plan must be 'ask' or 'deny': an action outside the frozen plan "
            "must never be accepted silently.",
            path=rel_path,
        )
