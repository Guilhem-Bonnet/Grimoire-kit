"""Étape Mémoire de ``grimoire init`` : bilan structurel après le scaffolding.

Sorti de :mod:`grimoire.cli.cmd_init` (plafond R2 du ratchet, 1500 lignes) ;
la logique est inchangée.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from grimoire.memory import profiles as memory_profiles


def memory_step_summary(target: Path, *, applied_profile: str, reason: str) -> dict[str, Any]:
    """Close the Memory step: a cheap, structural health check plus the
    upgrade path — the equivalent of `grimoire memory up --profile X --apply`
    already ran during scaffolding; this only reports on it.

    Deliberately never loads an embedding model (that first-use cost belongs
    to an explicit memory operation, e.g. `grimoire memory status`, not to
    `init` — see the module docstring of :mod:`grimoire.memory.embedding`).
    ``healthy`` only asserts the config landed on a profile this version
    knows, never that a remote service actually answers right now.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError
    from grimoire.tools.memory_setup import (
        docker_daemon_reachable,
        local_embedding_available,
        unreached_configured_services,
    )

    served = applied_profile
    backend = ""
    not_started: list[str] = []
    with contextlib.suppress(GrimoireConfigError, OSError):
        # A reporting step must never break `init`: a config-read failure falls back to what was applied.
        cfg = GrimoireConfig.from_yaml(target / "project-context.yaml")
        served = cfg.memory.layer_profile or applied_profile
        backend = cfg.memory.backend
        # `complet`/`graphe` can be configured yet never started (no consent, arbitrage #619):
        # the gap `grimoire doctor` names "memory stack not started".
        not_started = unreached_configured_services(cfg.memory)

    if not local_embedding_available():
        feasible = "lexical"
    elif docker_daemon_reachable():
        feasible = "complet"
    else:
        feasible = "standard"

    order = memory_profiles.PROFILE_ORDER
    served_rank = order.index(served) if served in order else 0
    upgrade_to = feasible if order.index(feasible) > served_rank else ""

    return {
        "served": served,
        "backend": backend,
        "reason": reason,
        "not_started": not_started,
        "healthy": served in order,
        "feasible": feasible,
        "upgrade_to": upgrade_to,
    }
