"""Vérificateur du pattern ``plan-then-execute`` (tri R32).

Beurer-Kellner et al. : une donnée non fiable ingérée ne doit plus pouvoir
déclencher d'action conséquente ; le plan est donc figé *avant* la lecture.
Deux contrôles sur le bloc ``plan_then_execute:`` de ``prompt-firewall.yaml`` :

- ``firewall.plan_not_frozen`` : ``freeze_plan_before_untrusted_read`` n'est pas
  ``true`` ;
- ``firewall.plan_deviation_allowed`` : ``actions_outside_plan`` n'est ni
  ``ask`` ni ``deny``.

Le bloc est optionnel : absent, le projet ne déclare pas ce pattern et il n'y a
rien à contrôler. Ce qui s'applique réellement en session est le garde-fou
d'exécution (``tool_policy._untrusted_escalation``) ; ce contrôle vérifie
seulement que la déclaration du projet l'engage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check

__all__ = ["_verify_plan_then_execute"]


def _verify_plan_then_execute(
    data: dict[str, Any], strict_profile: bool, result: StandardVerificationResult, rel_path: Path
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
