"""Conclusion de ``grimoire doctor`` : les avertissements comptent, ils ne passent pas pour des succès.

Un contrôle ``passed`` qui porte ``level: warn`` n'est pas une panne mais n'est pas non plus « OK » :
la dernière ligne doit le dire, sinon « 34/34 checks passed » contredit les lignes WARN affichées au-dessus.
"""

from __future__ import annotations

from typing import Any

__all__ = ["conclusion", "tally"]


def tally(results: list[dict[str, Any]]) -> tuple[int, int, int]:
    """``(réussis, échoués, avertissements)`` ; les avertissements sont inclus dans les réussis."""
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    warned = sum(1 for r in results if r["passed"] and r.get("level") == "warn")
    return passed, failed, warned


def conclusion(passed: int, failed: int, warned: int) -> str:
    """Dernière ligne du rapport : inchangée sans avertissement, sinon le détail."""
    if not warned:
        return f"{passed}/{passed + failed} checks passed"
    parts = [f"{passed - warned} OK"]
    if warned:
        parts.append(f"{warned} avertissement{'s' if warned > 1 else ''}")
    if failed:
        parts.append(f"{failed} FAIL")
    return ", ".join(parts)
