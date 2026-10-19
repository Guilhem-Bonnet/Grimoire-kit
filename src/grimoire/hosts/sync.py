"""Render every host surface, and say so when it cannot.

Three commands regenerated the surfaces (agents, skills, commands, hooks)
behind their own copy of ``except Exception: return []``, each justified by
"``host status`` reports the drift". A project whose surfaces failed to render
therefore left ``init`` with a green report and no hook, and the user only
learned it by going to look. One writer, one outcome, one explicit failure.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.hosts import collect
from grimoire.hosts.detection import enabled_host_ids
from grimoire.hosts.emitters import EmitPlan, EmitResult, apply_plans, emitter_for, supported_hosts

__all__ = ["HostSyncOutcome", "sync_host_surfaces", "unsynced_detail"]


@dataclass(frozen=True, slots=True)
class HostSyncOutcome:
    """What the sync wrote — or why it wrote nothing."""

    written: tuple[str, ...] = ()
    error: str = ""
    #: Fichiers que le kit n'a pas pu réécrire parce que l'utilisateur les a modifiés (ou qu'ils
    #: ne sont pas au kit). Préservés à raison — mais l'hôte ne reçoit pas ce que le projet déclare.
    conflicts: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Vrai seulement si chaque surface est à jour : un fichier préservé n'est pas un fichier synchronisé."""
        return not self.error and not self.conflicts

    @property
    def warning(self) -> str:
        """The one line a caller prints when the sync failed; empty otherwise."""
        if self.ok:
            return ""
        if self.error:
            return f"surfaces hôtes non régénérées ({self.error}) — relance `grimoire host sync` après correction"
        return (
            f"surfaces hôtes non synchronisées : {', '.join(self.conflicts)} modifié(s) à la main ou étranger(s) au kit, "
            "préservé(s) — `grimoire host probe` détaille, `grimoire host sync --force` les écrase"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "written": list(self.written), "error": self.error, "conflicts": list(self.conflicts)}


def sync_host_surfaces(project_root: Path) -> HostSyncOutcome:
    """Render the project onto every known host. Never raises, never hides.

    An emitter problem must not fail an otherwise successful install or
    repair, so the failure comes back as data — and is written once to
    stderr, so that a caller in JSON mode cannot swallow it either. The write
    is one transaction: on failure ``written`` is empty because nothing was
    kept, not because nothing was tried.
    """
    out: list[str] = []
    conflicts: list[str] = []
    try:
        surface = collect.build_surface(project_root)
        # Issue #177 (petite version) : n'émettre que les hôtes déclarés dans
        # `hosts.enabled`, ou détectés depuis le dépôt quand la clé est
        # absente — jamais la totalité des hôtes connus par défaut.
        enabled = enabled_host_ids(project_root)
        plans: list[EmitPlan] = []
        for host_id in supported_hosts():
            if host_id not in enabled:
                continue
            emitter = emitter_for(host_id)
            if emitter is None:  # pragma: no cover - registry is complete
                continue
            plans.append(emitter.plan(surface, project_root))
        # Une seule transaction pour tous les hôtes : une panne laisse l'arbre comme il était.
        for result in apply_plans(plans, project_root):
            out.extend(result.written)
            conflicts.extend(result.skipped)
    except Exception as exc:
        outcome = HostSyncOutcome(tuple(out), f"{type(exc).__name__}: {exc}")
        print(f"[grimoire] {outcome.warning}", file=sys.stderr)
        return outcome
    outcome = HostSyncOutcome(tuple(out), conflicts=tuple(conflicts))
    if not outcome.ok:
        print(f"[grimoire] {outcome.warning}", file=sys.stderr)
    return outcome


def unsynced_detail(results: list[EmitResult], project_root: Path) -> str:
    """Pourquoi une synchronisation déjà faite ne laisse pas les hôtes activés à jour ; vide si elle les y laisse.

    ``host_sync.ok`` est exigé (D-07) : un fichier que le kit a préservé parce que l'utilisateur
    l'a modifié reste, pour l'hôte, une surface périmée. Les conflits d'un hôte désactivé
    (``hosts.enabled``) ne comptent pas : le projet ne demande pas que cet hôte soit à jour.
    """
    enabled = enabled_host_ids(project_root)
    conflicts = [label for r in results if r.host_id in enabled for label in r.skipped]
    if not conflicts:
        return ""
    return (
        f"{len(conflicts)} host file(s) edited by hand or not owned by the kit, left untouched: "
        f"{', '.join(conflicts)} — `grimoire host probe` details, `grimoire host sync --force` overwrites"
    )
