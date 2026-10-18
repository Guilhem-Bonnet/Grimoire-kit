"""Règles parsables par couche dans ``standard verify`` (W6-03).

Extrait de ``verifiers.py``, collé au plafond de taille du ratchet. Skip
entièrement quand ``_grimoire/standard/rules/`` n'existe pas : un projet qui n'a
jamais écrit de règle n'a rien à se faire reprocher.

Sévérités : une ligne hors grammaire, un conflit non décidé (même couche), un
dépassement de ``rules.max_tokens_per_layer`` et une couche inconnue (hors
plafond) sont des ``error`` — ils font échouer ``standard verify``. Une règle
mise à l'ombre par une couche extérieure est décidée par le code : ``warning``.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check


def verify_layered_rules(root: Path, result: StandardVerificationResult) -> None:
    from grimoire.core.layered_rules import analyze

    for problem in analyze(root).problems:
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
