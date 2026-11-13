"""Parité des fonctions de gouvernance appliquée au dépôt courant.

:mod:`grimoire.hosts.capabilities` porte la table ; ce module nomme l'hôte
qu'un dépôt active et rend sa parité sérialisable, pour ``grimoire up``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.hosts.capabilities import parity_report, resolve_host
from grimoire.hosts.detection import resolve_enabled_hosts


def detected_host_parity(target: Path) -> dict[str, Any]:
    """Parité du premier hôte activé qui se résout ; ``{}`` si aucun ne se résout.

    L'hôte est le premier alias de ``hosts.enabled`` (sinon de la détection du
    dépôt) que :func:`grimoire.hosts.capabilities.resolve_host` reconnaît. La
    ligne rendue nomme ce qui est dégradé ou absent ; les raisons détaillées
    sont dans ``grimoire host matrix``.
    """
    for alias in resolve_enabled_hosts(target):
        host_id = resolve_host(alias)
        if host_id is not None:
            return parity_report(host_id)
    return {}
