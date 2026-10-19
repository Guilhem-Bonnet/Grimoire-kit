"""Matrice règle × hôte, calculée depuis ce que chaque émetteur produit (D-07).

La dégradation par hôte était *déclarée* (``Degradation``) mais jamais confrontée
à ce que l'émetteur écrit : rien n'empêchait un hook d'être retiré d'un rendu sans
que la liste des limites le dise, ni une règle scopée de partir sans sa portée.
Ici, aucune cellule n'est écrite à la main. Pour chaque couple (règle, hôte) :

- on rend le plan réel de l'hôte (``Emitter.plan``) ;
- on cherche la règle **dans ce que le plan écrit** (commande ``--event <X>`` pour un
  hook, ligne de règle et portée pour une règle de couche) ;
- si elle n'y est pas, on cherche la ``Degradation`` qui le dit.

Une règle ni portée ni déclarée dégradée est ``undeclared`` ; un hook écrit pour un
événement que l'hôte ne déclenche jamais est ``inert`` ; une règle scopée dont le
fichier ne porte pas la portée est ``misscoped``. Ce sont des échecs : une matrice
qui les accepterait ne vérifierait rien. Inconnu n'est pas OK.

Limite : la matrice dit ce que le *rendu* promet. Que le fichier soit sur le disque,
à jour, est l'affaire de :mod:`grimoire.hosts.probe`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.layered_rules import LAYERS, Rule, analyze
from grimoire.core.rule_resolution import emitted_rules, resolve_rules
from grimoire.hosts.capabilities import HostProfile, profile_for
from grimoire.hosts.emitters import emitter_for, supported_hosts
from grimoire.hosts.emitters.base import Degradation, EmitPlan
from grimoire.hosts.emitters.rules import renders_rule_files
from grimoire.hosts.events import Enforcement
from grimoire.hosts.surface import HookSpec, ProjectSurface

__all__ = [
    "FAILING",
    "Cell",
    "Matrix",
    "build_matrix",
    "carrier_present",
]

# ── statuts d'une cellule ────────────────────────────────────────────────────
ENFORCED = "enforced"  # hook écrit, bloquant, sur un hôte qui sait bloquer
ADVISORY = "advisory"  # hook écrit, mais il ne fait qu'informer
PROSE = "prose"  # hook énoncé comme règle dans l'entrypoint, rien ne l'exécute
ABSENT = "absent"  # non écrit, et l'émetteur le dit
NATIVE = "native"  # règle de couche écrite dans le format natif de l'hôte
DEGRADED = "degraded"  # règle non écrite, et l'émetteur le dit
UNDECLARED = "undeclared"  # ni écrit ni déclaré dégradé : l'émetteur ment par omission
INERT = "inert"  # écrit pour un événement que l'hôte ne déclenche pas
MISSCOPED = "misscoped"  # règle scopée écrite sans sa portée

#: Une cellule dans l'un de ces statuts rend la matrice, donc la sonde, non conforme.
FAILING = frozenset({UNDECLARED, INERT, MISSCOPED})

_EVENT_ARG = re.compile(r"--event[\s\"',]+([A-Za-z]+)")
_GLOBAL_SCOPE = "**"


def _norm(name: str) -> str:
    """``pre_tool_use`` et ``PreToolUse`` désignent le même événement."""
    return name.replace("_", "").lower()


@dataclass(frozen=True, slots=True)
class Cell:
    """Une règle sur un hôte : ce qui est promis, et par quels fichiers."""

    kind: str  # "hook" | "rule"
    subject: str
    host: str
    status: str
    enforcement: str = ""
    carriers: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status not in FAILING

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "host": self.host,
            "status": self.status,
            "enforcement": self.enforcement,
            "carriers": list(self.carriers),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Matrix:
    hosts: tuple[HostId, ...]
    cells: tuple[Cell, ...]
    plans: dict[HostId, EmitPlan] = field(default_factory=dict, compare=False, repr=False)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.cells)

    @property
    def failing(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if not c.ok)

    def for_host(self, host: HostId) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.host == host.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hosts": [h.value for h in self.hosts],
            "cells": [c.to_dict() for c in self.cells],
        }


def carrier_present(cell: Cell, text: str) -> bool:
    """*text* (le contenu d'un fichier) porte-t-il ce que la cellule dit y avoir écrit ?"""
    if cell.kind == "hook":
        event = cell.subject.removeprefix("hook:")
        if cell.status == PROSE:
            return f"**{event}**" in text
        return _norm(event) in {_norm(m) for m in _EVENT_ARG.findall(text)}
    return _rule_in_text(cell.subject, text)


def _rule_in_text(subject: str, text: str) -> bool:
    """Forme du sujet : ``rule:<couche>:<modalité> <verbe> <objet>[ @portée]``."""
    _, _, rest = subject.split(":", 2)
    bare, _, scope = rest.partition(" @")
    if not any(line == f"- {bare}" or line.startswith(f"- {bare} — ") for line in text.splitlines()):
        return False
    return not scope or scope in _scopes(text)


def _scopes(text: str) -> set[str]:
    """Portées que l'en-tête du fichier déclare à l'hôte (``globs:`` Cursor, ``applyTo:`` Copilot)."""
    if not text.startswith("---"):
        return set()
    parts = text.split("---", 2)
    found: set[str] = set()
    for line in (parts[1] if len(parts) > 2 else "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() in {"globs", "applyTo"}:
            found.add(value.strip().strip("'\""))
    return found


def _texts(plan: EmitPlan) -> list[tuple[str, str]]:
    """Ce que le plan écrit, fichier par fichier ; un fusion JSON est rendue sur un objet vide."""
    out = [(f.relpath.as_posix(), f.content) for f in plan.files]
    out.extend((m.relpath.as_posix(), json.dumps(m.merge({}), ensure_ascii=False)) for m in plan.merges)
    return out


def _declares(plan: EmitPlan, *surfaces: str) -> Degradation | None:
    return next((d for d in plan.degradations if d.surface in surfaces), None)


def _hook_cell(hook: HookSpec, host: HostId, profile: HostProfile, plan: EmitPlan, texts: list[tuple[str, str]]) -> Cell:
    event = hook.event.value
    subject = f"hook:{event}"
    wire = _norm(event)
    carriers = tuple(path for path, text in texts if wire in {_norm(m) for m in _EVENT_ARG.findall(text)})
    if carriers:
        if not profile.supports_event(hook.event):
            return Cell("hook", subject, host.value, INERT, carriers=carriers,
                        reason=f"écrit, mais {profile.display_name} ne déclenche pas l'événement {event}")
        if hook.enforcement is Enforcement.BLOCKING and profile.blocking_hooks:
            return Cell("hook", subject, host.value, ENFORCED, Enforcement.BLOCKING.value, carriers)
        why = "l'hôte ne sait pas bloquer" if hook.enforcement is Enforcement.BLOCKING else ""
        return Cell("hook", subject, host.value, ADVISORY, Enforcement.ADVISORY.value, carriers, why)
    degradation = _declares(plan, "hooks", f"hook {event}")
    if degradation is None:
        return Cell("hook", subject, host.value, UNDECLARED,
                    reason=f"aucun fichier de {profile.display_name} ne l'exécute et aucune limite ne le dit")
    prose = tuple(path for path, text in texts if f"**{event}**" in text)
    status = PROSE if prose else ABSENT
    return Cell("hook", subject, host.value, status, carriers=prose, reason=degradation.fallback)


def _rule_cells(host: HostId, plan: EmitPlan, texts: list[tuple[str, str]], project_root: Path) -> list[Cell]:
    report = analyze(project_root)
    rules = [rule for layer in LAYERS for rule in report.layers.get(layer, ())]
    resolved = resolve_rules(rules)
    live = {_key(rr.rule) for rr in emitted_rules(resolved)}
    declared = _declares(plan, "rules")
    renders = renders_rule_files(host)
    cells: list[Cell] = []
    for rr in resolved:
        rule = rr.rule
        subject = f"rule:{rule.layer}:{rule.render()}"
        carriers: tuple[str, ...] = ()
        if _key(rule) in live:
            carriers = tuple(p for p, t in texts if _rule_in_text(subject, t))
            if not carriers and renders:
                # Même texte sans portée : le fichier existe, mais la règle s'y applique partout.
                loose = tuple(p for p, t in texts if _rule_in_text(f"rule:x:{_bare(rule)}", t))
                status = MISSCOPED if loose else UNDECLARED
                why = "écrite sans sa portée" if loose else "livrable à cet hôte mais absente de ses fichiers"
                cells.append(Cell("rule", subject, host.value, status, carriers=loose, reason=why))
                continue
        if carriers:
            cells.append(Cell("rule", subject, host.value, NATIVE, carriers=carriers))
        elif declared is not None:
            cells.append(Cell("rule", subject, host.value, DEGRADED, reason=declared.fallback))
        else:
            cells.append(Cell("rule", subject, host.value, UNDECLARED, reason="non livrée et non déclarée dégradée"))
    return cells


def _bare(rule: Rule) -> str:
    return f"{rule.modality} {rule.verb} {rule.object}"


def _key(rule: Rule) -> tuple[str, int, str]:
    return (rule.layer, rule.line, rule.render())


def build_matrix(
    project_root: Path,
    host_ids: tuple[HostId, ...] | None = None,
    *,
    surface: ProjectSurface | None = None,
) -> Matrix:
    """Calculer la matrice des hôtes demandés (tous les hôtes à émetteur par défaut)."""
    from grimoire.hosts.collect import build_surface

    root = Path(project_root)
    surf = surface if surface is not None else build_surface(root)
    hosts = tuple(h for h in (host_ids if host_ids is not None else supported_hosts()) if emitter_for(h) is not None)
    cells: list[Cell] = []
    plans: dict[HostId, EmitPlan] = {}
    for host in hosts:
        emitter = emitter_for(host)
        assert emitter is not None  # filtré ci-dessus
        plan = emitter.plan(surf, root)
        plans[host] = plan
        profile = profile_for(host)
        texts = _texts(plan)
        cells.extend(_hook_cell(hook, host, profile, plan, texts) for hook in surf.hooks)
        cells.extend(_rule_cells(host, plan, texts, root))
    return Matrix(hosts=hosts, cells=tuple(cells), plans=plans)
