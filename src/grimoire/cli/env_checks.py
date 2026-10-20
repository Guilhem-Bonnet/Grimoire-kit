"""Résultat d'une sonde d'environnement, partagé par ``grimoire up`` et ``grimoire doctor``.

Sorti de :mod:`grimoire.cli.cmd_up` pour que les sondes vivant dans leur propre
module (:mod:`grimoire.cli.doctor_hooks`) l'importent sans cycle, et pour tenir
``cmd_up`` sous le plafond de taille des modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EnvCheck:
    """Outcome of one environment probe."""

    name: str
    passed: bool
    level: str  # "ok" | "info" | "warn" | "fail"
    detail: str
    remedy: str = ""
    optional: bool = True
