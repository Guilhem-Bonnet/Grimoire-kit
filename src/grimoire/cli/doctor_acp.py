"""Contrôle ``doctor`` du mode ACP des hôtes installés (ACP3).

Pour chaque hôte que le projet utilise (``hosts.enabled`` ou détecté, plus l'hôte du chat) et dont
l'exécutable est présent, lance sa commande ACP et négocie
``initialize`` — rien d'autre : pas de ``session/new``, pas de prompt, donc aucun
coût de modèle. Un hôte absent n'est pas rapporté (rien à vérifier) ; un mode qui ne
répond pas est un avertissement, jamais une panne : le chat se replie sur le kanban.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from grimoire.acp import AcpClient, AcpError
from grimoire.bridges.schemas import HostId
from grimoire.cli.env_checks import EnvCheck
from grimoire.hosts.acp_hosts import acp_availability, chosen_alias
from grimoire.hosts.capabilities import HostProfile, all_profiles, resolve_host
from grimoire.hosts.detection import enabled_host_ids

__all__ = ["check_acp_hosts", "probe_initialize"]

PROBE_TIMEOUT_SECONDS = 15.0

#: ``(commande, dossier) -> None`` ; lève :class:`AcpError` si ``initialize`` ne répond pas.
Probe = Callable[[tuple[str, ...], Path], None]


def probe_initialize(command: tuple[str, ...], cwd: Path) -> None:
    """Démarre l'agent, négocie ``initialize``, l'arrête."""
    with AcpClient(command, cwd, request_timeout=PROBE_TIMEOUT_SECONDS):
        pass


def _check(profile: HostProfile, target: Path, probe: Probe) -> EnvCheck:
    name = f"acp_{profile.host_id.value}"
    preview = " (preview)" if profile.acp_preview else ""
    try:
        probe(profile.acp_command, target)
    except AcpError as exc:
        return EnvCheck(
            name,
            passed=True,
            level="warn",
            detail=f"{profile.display_name} : le mode ACP{preview} ne répond pas à initialize — {exc}",
            remedy=profile.acp_note or f"Vérifier `{' '.join(profile.acp_command)}` ; le chat se replie sur le kanban.",
        )
    return EnvCheck(
        name,
        passed=True,
        level="ok",
        detail=f"{profile.display_name} : le mode ACP{preview} répond à initialize (`{' '.join(profile.acp_command)}`)",
    )


def check_acp_hosts(target: Path, probe: Probe = probe_initialize) -> list[EnvCheck]:
    """Un contrôle par hôte installé qui déclare un mode ACP, dans l'ordre des profils."""
    installed = [p for p in all_profiles() if p.acp_command and acp_availability(p)[0]]
    # A host the project does not use is not probed: its agent may be installed on
    # the machine without being set up, and a failed handshake there is neither a
    # fault of the project nor worth a trace. The chosen chat host stays checked.
    used = _used_hosts(target)
    active = [p for p in installed if p.host_id in used]
    skipped = [p.display_name for p in installed if p.host_id not in used]
    checks: list[EnvCheck] = []
    if active:
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            checks = list(pool.map(lambda p: _check(p, target, probe), active))
    if skipped:
        checks.append(EnvCheck(
            "acp_unused_hosts",
            passed=True,
            level="info",
            detail=f"ACP not checked for hosts this project does not use: {', '.join(skipped)}",
        ))
    return checks


def _used_hosts(target: Path) -> set[HostId]:
    """Hosts the project enables, plus the one the orchestrator chat talks to."""
    used = enabled_host_ids(target)
    chosen = resolve_host(chosen_alias(target))
    if chosen is not None:
        used.add(chosen)
    return used
