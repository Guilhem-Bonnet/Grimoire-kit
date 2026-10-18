"""Règles parsables par couche dans ``standard verify`` (W6-03).

Extrait de ``verifiers.py``, collé au plafond de taille du ratchet. Skip
entièrement quand ``_grimoire/standard/rules/`` n'existe pas : un projet qui n'a
jamais écrit de règle n'a rien à se faire reprocher.

Sévérités : une ligne hors grammaire, un conflit non décidé (même couche), un
dépassement de ``rules.max_tokens_per_layer`` et une couche inconnue (hors
plafond) sont des ``error`` — ils font échouer ``standard verify``. Une règle
mise à l'ombre par une couche extérieure est décidée par le code : ``warning``.

Packs de règles par stack (W6-08) : une règle de pack installée dont le
vérificateur échoue est une ``error`` (``rules.pack_violation``), une règle dont
le vérificateur n'a pas pu conclure un ``warning`` (``rules.pack_unevaluated``) ;
un pack du kit illisible est une ``error`` (``rules.pack_invalid``). Un projet
sans règle de couche n'est pas parcouru.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check

if TYPE_CHECKING:
    from grimoire.core.layered_rules import Rule


def verify_layered_rules(root: Path, result: StandardVerificationResult) -> None:
    from grimoire.core.layered_rules import analyze

    report = analyze(root)
    for problem in report.problems:
        if problem.kind == "unparsable":
            _add_check(result, "rules.layer_unparsable", "error", problem.message, path=problem.path)
        elif problem.kind == "conflict":
            _add_check(result, "rules.layer_conflict", "error", problem.message, path=problem.path)
        elif problem.kind == "over_budget":
            _add_check(result, "rules.layer_over_budget", "error", problem.message, path=problem.path)
        elif problem.kind == "unknown_layer":
            _add_check(result, "rules.layer_unknown", "error", problem.message, path=problem.path)
        elif problem.kind == "shadowed":
            _add_check(result, "rules.layer_shadowed", "warning", problem.message, path=problem.path)
        elif problem.kind == "policy_invalid":
            _add_check(result, "rules.policy_invalid", "warning", problem.message, path=problem.path)
    verify_stack_packs(root, result, report.layers)


def verify_stack_packs(root: Path, result: StandardVerificationResult, layers: Mapping[str, tuple[Rule, ...]]) -> None:
    from grimoire.core.stack_packs import StackPackError, evaluate_project

    if not any(layers.values()):
        return
    try:
        report = evaluate_project(root, layers)
    except StackPackError as exc:
        _add_check(result, "rules.pack_invalid", "error", str(exc))
        return
    for status in report.statuses:
        if status.status == "fail":
            shown = "; ".join(f.render() for f in status.findings[:3])
            more = f" (+{len(status.findings) - 3})" if len(status.findings) > 3 else ""
            _add_check(result, "rules.pack_violation", "error", f"Pack {status.pack} : « {status.rule} » violée — {shown}{more}.")
        elif status.status == "unevaluated":
            _add_check(result, "rules.pack_unevaluated", "warning", f"Pack {status.pack} : « {status.rule} » non évaluée — {status.reason}.")
