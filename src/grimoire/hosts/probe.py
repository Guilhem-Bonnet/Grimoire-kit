"""Sonde d'hôte : ce que la matrice promet est-il sur le disque, à jour ? (D-07)

:mod:`grimoire.hosts.matrix` dit ce que chaque émetteur *produit*. Cette sonde lit
le disque et confronte les deux : un fichier émis absent, périmé ou modifié à la
main, un hook qui n'est plus câblé, une règle qui a disparu du fichier de l'hôte.
Rien n'est écrit : l'état des fichiers vient du plan appliqué à blanc, donc de la
même règle de propriété que ``host sync`` (empreinte, marqueur, bloc géré).

Verdict par cellule (``disk``) :

- ``applied``  : la règle est dans le fichier, qui est à jour ;
- ``declared`` : rien à chercher sur le disque, la limite est déclarée ;
- ``missing``  : le fichier n'existe pas ;
- ``stale``    : le fichier existe mais ne porte pas, ou plus, la règle ;
- ``conflict`` : le fichier a été modifié à la main ou n'est pas au kit : le kit
  ne le réécrit pas, donc la règle n'est pas garantie.

Une cellule dont le statut de matrice échoue (``undeclared``, ``inert``,
``misscoped``) fait échouer la sonde : un fichier parfaitement à jour qui ment
n'est pas un succès.

Limite assumée : la sonde vérifie que le hook est *câblé* dans le fichier que
l'hôte lit, pas que l'hôte l'exécute. Rejouer le hook est l'affaire de
``grimoire upgrade-flow probe-hook``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import EmitPlan, EmitResult, apply_plan
from grimoire.hosts.matrix import Cell, Matrix, build_matrix, carrier_present
from grimoire.hosts.surface import ProjectSurface

__all__ = ["FileProbe", "HostProbe", "ProbeReport", "ProbedCell", "probe_hosts"]

APPLIED = "applied"
DECLARED = "declared"
MISSING = "missing"
STALE = "stale"
CONFLICT = "conflict"

#: États d'un fichier émis.
FILE_OK = "ok"
FILE_ORPHAN = "orphan"  # marqué du kit, plus émis : ``host sync`` le retirerait


@dataclass(frozen=True, slots=True)
class FileProbe:
    path: str
    state: str  # ok | missing | stale | conflict | orphan
    cause: str = ""

    @property
    def ok(self) -> bool:
        return self.state == FILE_OK

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "state": self.state, "cause": self.cause}


@dataclass(frozen=True, slots=True)
class ProbedCell:
    cell: Cell
    disk: str

    @property
    def ok(self) -> bool:
        return self.cell.ok and self.disk in {APPLIED, DECLARED}

    def to_dict(self) -> dict[str, Any]:
        return {**self.cell.to_dict(), "disk": self.disk, "ok": self.ok}


@dataclass(frozen=True, slots=True)
class HostProbe:
    host: str
    display_name: str
    files: tuple[FileProbe, ...]
    cells: tuple[ProbedCell, ...]

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.files) and all(c.ok for c in self.cells)

    @property
    def drift(self) -> tuple[str, ...]:
        """Ce qui est manquant ou périmé, hors modifications de l'utilisateur."""
        return tuple(f.path for f in self.files if f.state in {"missing", "stale", FILE_ORPHAN})

    @property
    def conflicts(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.state == "conflict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "display_name": self.display_name,
            "ok": self.ok,
            "files": [f.to_dict() for f in self.files],
            "cells": [c.to_dict() for c in self.cells],
        }


@dataclass(frozen=True, slots=True)
class ProbeReport:
    hosts: tuple[HostProbe, ...]

    @property
    def verified(self) -> bool:
        """Au moins un hôte a été sondé : « rien à sonder » n'est pas « tout est bon »."""
        return bool(self.hosts)

    @property
    def ok(self) -> bool:
        return self.verified and all(h.ok for h in self.hosts)

    @property
    def drift(self) -> tuple[str, ...]:
        return tuple(p for h in self.hosts for p in h.drift)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "verified": self.verified, "hosts": [h.to_dict() for h in self.hosts]}


def _file_states(plan: EmitPlan, result: EmitResult, root: Path) -> list[FileProbe]:
    paths = {f.relpath.as_posix(): f.relpath for f in plan.files}
    paths.update({(m.label or m.relpath.as_posix()): m.relpath for m in plan.merges})
    states: dict[str, FileProbe] = {}
    for label in result.unchanged:
        states[label] = FileProbe(paths.get(label, Path(label)).as_posix(), FILE_OK)
    for label in result.written:
        rel = paths.get(label, Path(label))
        states[label] = FileProbe(rel.as_posix(), STALE if (root / rel).exists() else MISSING)
    for label in result.skipped:
        known = next((k for k in paths if label == k or label.startswith(f"{k} ")), label)
        states[label] = FileProbe(paths.get(known, Path(known)).as_posix(), CONFLICT, result.conflicts.get(label, ""))
    for label in result.removed:
        states[label] = FileProbe(label, FILE_ORPHAN)
    return sorted(states.values(), key=lambda f: f.path)


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _disk_verdict(cell: Cell, files: dict[str, FileProbe], root: Path) -> str:
    if not cell.carriers:
        return DECLARED
    verdicts: list[str] = []
    for rel in cell.carriers:
        state = files.get(rel)
        text = _read(root, rel)
        if text is None:
            verdicts.append(MISSING)
        elif state is not None and state.state == CONFLICT:
            verdicts.append(CONFLICT)
        elif not carrier_present(cell, text) or (state is not None and not state.ok):
            verdicts.append(STALE)
        else:
            verdicts.append(APPLIED)
    for bad in (MISSING, CONFLICT, STALE):
        if bad in verdicts:
            return bad
    return APPLIED


def probe_hosts(
    project_root: Path,
    host_ids: tuple[HostId, ...] | None = None,
    *,
    surface: ProjectSurface | None = None,
    matrix: Matrix | None = None,
) -> ProbeReport:
    """Sonder le disque de *project_root* pour les hôtes demandés, sans rien écrire."""
    root = Path(project_root).resolve()
    m = matrix if matrix is not None else build_matrix(root, host_ids, surface=surface)
    probes: list[HostProbe] = []
    for host in m.hosts:
        plan = m.plans.get(host)
        if plan is None or emitter_for(host) is None:  # pragma: no cover - build_matrix filtre
            continue
        result = apply_plan(plan, root, dry_run=True)
        files = _file_states(plan, result, root)
        by_path = {f.path: f for f in files}
        cells = tuple(ProbedCell(c, _disk_verdict(c, by_path, root)) for c in m.for_host(host))
        probes.append(HostProbe(host.value, profile_for(host).display_name, tuple(files), cells))
    return ProbeReport(tuple(probes))
