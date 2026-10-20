"""Vérificateur du pattern ``redis-hot-memory-soft-gate`` (issue #636).

La règle ``memory.hot-degrades-safely`` dit : Redis peut manquer, à condition
que la dégradation soit déclarée et que les stores durables restent la
source de vérité. ``memory.os_hot_memory_target`` vérifie la cible (Redis) ;
rien ne vérifiait la *dégradation*. Un seul contrôle :

- ``memory.hot_degradation_undeclared`` : ``memory_os.hot_memory_degradation``
  manque, ou ne dit pas ``on_unavailable: degrade`` avec un ``fallback``
  nommé (le repli local déclaré par ``capability-map.yaml`` :
  ``tech_extras.redis.degrades_to``).

Avertissement, comme la règle elle-même (``severity: warning`` dans
``rule-packs.yaml``) : c'est une porte *douce*. Le comportement à
l'exécution — ce que fait réellement le kit sans Redis — n'est pas
vérifiable statiquement et n'est pas affirmé ici.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check

__all__ = ["_verify_hot_memory_degradation"]

EXPECTED_ON_UNAVAILABLE = "degrade"


def _verify_hot_memory_degradation(
    memory_policy: Mapping[str, Any], *, result: StandardVerificationResult, path: Path
) -> None:
    memory_os = memory_policy.get("memory_os")
    block = memory_os.get("hot_memory_degradation") if isinstance(memory_os, dict) else None
    if not isinstance(block, dict):
        reason = "no memory_os.hot_memory_degradation block"
    elif str(block.get("on_unavailable", "")).strip() != EXPECTED_ON_UNAVAILABLE:
        reason = f"on_unavailable is {block.get('on_unavailable')!r}, expected {EXPECTED_ON_UNAVAILABLE!r}"
    elif not str(block.get("fallback", "")).strip():
        reason = "fallback names no local store"
    else:
        return
    _add_check(
        result,
        "memory.hot_degradation_undeclared",
        "warning",
        f"Hot memory degradation is undeclared ({reason}): the policy does not say what happens without Redis.",
        path=path,
    )
