"""Règles par couche émises dans le format natif de l'hôte (W6-05).

Les règles vivent dans ``_grimoire/standard/rules/<couche>.rules`` (W6-03). Ce
module les rend là où l'hôte sait les charger **par chemin** :

- Cursor : ``.cursor/rules/grimoire-rules*.mdc`` avec ``globs:`` (ou
  ``alwaysApply: true`` pour une règle sans portée) ;
- GitHub Copilot : ``.github/instructions/grimoire-rules*.instructions.md`` avec
  ``applyTo:``.

Un fichier par portée distincte : l'hôte ne charge que les règles qui valent
pour le fichier en cours, au lieu de relire tout le corpus à chaque tour.

Ce qui est émis est décidé par :func:`grimoire.core.rule_resolution.resolve_rules`,
pas par l'hôte :

- seules les couches ``team`` et ``project`` partent (décision 10 de la
  campagne : ni ``organization``, ni ``agent`` en vague 6) ;
- une règle qui cède devant une règle effective d'une couche extérieure, sur
  tout ce qu'elle couvre, n'est pas émise ; si elle ne cède que là où les
  portées se recouvrent, elle l'est avec la mention de ce devant quoi elle cède ;
- deux règles opposées d'une même couche, que rien ne départage, ne sont ni l'une
  ni l'autre émises : mieux vaut aucune consigne qu'une consigne qui se contredit.

Tout ce qui n'est pas livré est dit : une :class:`Degradation` ``rules`` par cause.
Les hôtes sans fichier de règles scopées reçoivent la même mention.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.core.layered_rules import LAYERS, RULES_DIR, Rule, RulesReport, analyze
from grimoire.core.rule_resolution import (
    EMITTED_LAYERS,
    STATUS_EFFECTIVE,
    STATUS_PARTIAL,
    STATUS_SHADOWED,
    STATUS_UNRESOLVED,
    ResolvedRule,
    resolve_rules,
)
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.emitters.base import Degradation, EmitPlan, EmittedFile, Emitter, managed_header

__all__ = ["rule_file_name", "with_rule_layers"]

#: Portée d'une règle sans ``@portée`` (voir ``layered_rules.Rule.scope``).
GLOBAL_SCOPE = "**"


def rule_file_name(scope: str, suffix: str) -> str:
    """Nom stable et injectif d'un fichier de règles : ``grimoire-rules[-<slug>-<empreinte>]<suffix>``.

    L'empreinte départage ``src/**`` et ``src/*``, que le slug seul confondrait.
    """
    if scope == GLOBAL_SCOPE:
        return f"grimoire-rules{suffix}"
    slug = re.sub(r"[^a-z0-9]+", "-", scope.lower()).strip("-")[:32] or "scope"
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:8]
    return f"grimoire-rules-{slug}-{digest}{suffix}"


def _bare(rule: Rule) -> str:
    """La règle sans sa portée : le fichier la porte déjà."""
    return f"{rule.modality} {rule.verb} {rule.object}"


def _body(scope: str, items: list[ResolvedRule]) -> list[str]:
    where = "tout le projet" if scope == GLOBAL_SCOPE else f"`{scope}`"
    lines = [
        managed_header(".md"),
        "",
        f"# Règles Grimoire — {where}",
        "",
        "Source : `_grimoire/standard/rules/*.rules`, couches "
        f"{', '.join(f'`{name}`' for name in EMITTED_LAYERS)} (la première l'emporte). "
        "Régénéré par `grimoire host sync` ; éditer la source, pas ce fichier.",
        "",
    ]
    for layer in EMITTED_LAYERS:
        chosen = [item for item in items if item.layer == layer]
        if not chosen:
            continue
        lines += [f"## {layer}", ""]
        for item in chosen:
            note = ""
            if item.status == STATUS_PARTIAL:
                beaten = " ; ".join(f"« {w.render()} » ({w.layer})" for w in item.defeated_by)
                note = f" — cède devant {beaten} là où les portées se recouvrent"
            lines.append(f"- {_bare(item.rule)}{note}")
        lines.append("")
    return lines


def _cursor_file(scope: str, items: list[ResolvedRule]) -> EmittedFile:
    if scope == GLOBAL_SCOPE:
        head = ["---", "description: Règles Grimoire du projet (couches team et project)", "alwaysApply: true", "---"]
    else:
        # Cursor écrit `globs:` sans guillemets ; un glob qui commence par « * » n'est pas du YAML strict.
        head = ["---", f"description: Règles Grimoire pour {scope}", f"globs: {scope}", "alwaysApply: false", "---"]
    content = "\n".join([*head, *_body(scope, items)])
    return EmittedFile(relpath=Path(".cursor/rules") / rule_file_name(scope, ".mdc"), content=content)


def _copilot_file(scope: str, items: list[ResolvedRule]) -> EmittedFile:
    description = "Règles Grimoire du projet (couches team et project)" if scope == GLOBAL_SCOPE else (
        f"Règles Grimoire pour {scope}"
    )
    head = Emitter.frontmatter({"description": description, "applyTo": scope})
    content = "\n".join([head, *_body(scope, items)])
    return EmittedFile(relpath=Path(".github/instructions") / rule_file_name(scope, ".instructions.md"), content=content)


#: hôte -> (rendu d'un fichier par portée, motif des fichiers qu'il possède).
_RENDERERS: dict[HostId, tuple[Callable[[str, list[ResolvedRule]], EmittedFile], str]] = {
    HostId.CURSOR: (_cursor_file, ".cursor/rules/grimoire-rules*.mdc"),
    HostId.GITHUB_COPILOT: (_copilot_file, ".github/instructions/grimoire-rules*.instructions.md"),
}


def _causes(report: RulesReport, resolved: tuple[ResolvedRule, ...]) -> list[Degradation]:
    """Ce qui n'est pas livré à l'hôte, une dégradation par cause."""
    out: list[Degradation] = []
    elsewhere = sorted({rr.layer for rr in resolved if rr.layer not in EMITTED_LAYERS})
    if elsewhere:
        out.append(Degradation(
            surface="rules",
            reason=f"couche(s) {', '.join(elsewhere)} non livrée(s) aux hôtes en vague 6 (décision 10).",
            fallback=f"règles lisibles dans {RULES_DIR}/ ; `grimoire memory rules explain <chemin>` les ordonne.",
        ))
    dead = [rr for rr in resolved if rr.layer in EMITTED_LAYERS and rr.status == STATUS_SHADOWED]
    if dead:
        out.append(Degradation(
            surface="rules",
            reason=f"{len(dead)} règle(s) à l'ombre d'une couche extérieure, non émise(s) : "
            + " ; ".join(f"« {rr.rule.render()} » ({rr.layer})" for rr in dead),
            fallback="aucune : la règle extérieure l'emporte ; retirer la règle morte pour la faire disparaître.",
        ))
    open_ = [rr for rr in resolved if rr.layer in EMITTED_LAYERS and rr.status == STATUS_UNRESOLVED]
    if open_:
        out.append(Degradation(
            surface="rules",
            reason=f"{len(open_)} règle(s) en conflit non décidé (même couche), non émise(s) : "
            + " ; ".join(f"« {rr.rule.render()} » ({rr.layer}:{rr.rule.line})" for rr in open_),
            fallback="`grimoire memory rules check` nomme le conflit ; retirer l'une des deux règles.",
        ))
    for problem in report.problems:
        if problem.kind in {"unparsable", "over_budget", "unknown_layer"}:
            out.append(Degradation(
                surface="rules",
                reason=problem.message,
                fallback="ligne hors grammaire ignorée ; `standard verify` échoue tant que ce n'est pas corrigé.",
            ))
    return out


def with_rule_layers(plan: EmitPlan, project_root: Path) -> EmitPlan:
    """Compléter le plan d'un hôte avec ses fichiers de règles et ce qu'il ne reçoit pas.

    Sans règle dans le projet, le plan est rendu tel quel (hors motif de
    nettoyage, pour retirer les fichiers d'une règle supprimée depuis).
    """
    renderer = _RENDERERS.get(plan.host_id)
    report = analyze(project_root)
    resolved = resolve_rules(rule for layer in LAYERS for rule in report.layers.get(layer, ()))
    owned = (renderer[1],) if renderer else ()
    if not resolved and not report.problems:
        return replace(plan, owned_globs=(*plan.owned_globs, *owned))

    live = [rr for rr in resolved if rr.layer in EMITTED_LAYERS and rr.status in {STATUS_EFFECTIVE, STATUS_PARTIAL}]
    degradations = list(plan.degradations)
    files = list(plan.files)
    if renderer is None:
        if live:
            degradations.append(Degradation(
                surface="rules",
                reason=f"{profile_for(plan.host_id).display_name} : le kit n'émet pas de fichier de règles par chemin pour cet hôte.",
                fallback=f"{len(live)} règle(s) lisible(s) dans {RULES_DIR}/ ; `grimoire memory rules explain <chemin>` donne celles d'un fichier.",
            ))
        return replace(plan, degradations=tuple(degradations))

    render = renderer[0]
    by_scope: dict[str, list[ResolvedRule]] = {}
    for rr in live:
        by_scope.setdefault(rr.rule.scope, []).append(rr)
    files.extend(render(scope, items) for scope, items in sorted(by_scope.items()))
    degradations.extend(_causes(report, resolved))
    return replace(plan, files=tuple(files), degradations=tuple(degradations), owned_globs=(*plan.owned_globs, *owned))
