"""Contrôle ``instruction_files`` de ``grimoire doctor`` (issue #667).

Séparé de :mod:`grimoire.cli.cmd_up` pour tenir sous le plafond de taille des
modules ; la logique vit dans :mod:`grimoire.hosts.decisions.instruction_scan`,
la même que le ``SessionStart``.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.cli.env_checks import EnvCheck


def check_instruction_files(target: Path) -> list[EnvCheck]:
    """Un fichier d'instructions ni livré par le kit ni committé porte-t-il un motif d'injection ?"""
    from grimoire.hosts.decisions.instruction_scan import doctor_check

    try:
        result = doctor_check(target)
    except Exception as exc:  # jamais un doctor cassé pour un scan : le dire, sans échouer
        return [EnvCheck("instruction_files", passed=True, level="warn", detail=f"scan impossible ({type(exc).__name__})")]
    if result["passed"]:
        return [EnvCheck("instruction_files", passed=True, level="ok", detail=result["detail"])]
    return [EnvCheck(
        "instruction_files",
        passed=False,
        level="fail",
        detail=result["detail"],
        remedy=(
            "lis le fichier ; s'il est voulu, committe-le (il devient un fichier du projet), "
            "sinon retire le passage ou le fichier"
        ),
        optional=False,
    )]
