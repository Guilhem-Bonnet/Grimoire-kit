"""Puissance du banc : effet minimal détectable et nombre de tâches requis (B-02).

Aucune dépense, aucun appel ``claude`` : calcul pur (``statistics.NormalDist``).

Pourquoi : le critère V1 de la campagne (non-infériorité à 5 points, « MDE ≤ 10 points au
n atteint ») n'avait ni calcul ni valeur. Une suite de 20 tâches ne peut pas distinguer deux
bras à 10 points : sans ce calcul, un « pas de différence » se lirait comme « équivalents ».

Modèle (approximation normale, deux bras de ``n`` tâches chacun, une tâche = une unité binaire,
``pass^k`` vaut 1 si la tâche est verte à toutes ses répétitions) :

    MDE(n)  = (z_{1-alpha/sides} + z_{power}) * sqrt((Var_a + Var_b) / n)
    n(mde)  = (z_{1-alpha/sides} + z_{power})^2 * (Var_a + Var_b) / mde^2        (arrondi au-dessus)

avec ``Var_x = p_x (1 - p_x)``. Deux bases de variance, toujours publiées côte à côte :

- ``worst_case`` : p = 0,5 pour les deux bras, variance maximale. Valable sans donnée.
- ``observed`` : p observés sur les lignes du banc. Un taux observé de 0 ou de 1 donne une
  variance nulle, donc un MDE nul : c'est une suite saturée (B-01), pas une précision. Dans ce
  cas ``observed`` vaut ``null`` avec la raison, jamais un MDE fabriqué.

Limites écrites : l'approximation normale est grossière pour ``n`` petit (n = 3, n = 30 en bord
de validité) ; les tâches sont traitées comme indépendantes et de même difficulté, ce qui
sous-estime la variance quand elles diffèrent ; un MDE supérieur à 1 veut dire « aucun effet
détectable à ce n », pas une valeur à citer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80
DEFAULT_SIDES = 2
#: Effet visé par la barrière 3 (critère V1 du protocole, section 6 du plan).
DEFAULT_TARGET_MDE = 0.10
#: Tailles de suite que le chantier B-02 doit toujours chiffrer.
REFERENCE_SIZES = (3, 30)
WORST_CASE_VARIANCE_SUM = 0.5  # 0,25 + 0,25


def _z_total(alpha: float, power: float, sides: int) -> float:
    if not 0.0 < alpha < 1.0 or not 0.0 < power < 1.0:
        raise ValueError(f"alpha et power doivent être dans ]0, 1[, reçu alpha={alpha}, power={power}")
    if sides not in (1, 2):
        raise ValueError(f"sides vaut 1 ou 2, reçu {sides}")
    normal = NormalDist()
    return normal.inv_cdf(1.0 - alpha / sides) + normal.inv_cdf(power)


def variance_sum(p_a: float, p_b: float) -> float:
    """Somme des variances de Bernoulli de deux bras."""
    for p in (p_a, p_b):
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"une proportion est dans [0, 1], reçu {p}")
    return p_a * (1.0 - p_a) + p_b * (1.0 - p_b)


def minimum_detectable_effect(
    n_tasks: int,
    *,
    var_sum: float = WORST_CASE_VARIANCE_SUM,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sides: int = DEFAULT_SIDES,
) -> float:
    """Plus petit écart de pass^k (en proportion, 0,10 = 10 points) détectable avec ``n_tasks`` tâches par bras."""
    if n_tasks < 1:
        raise ValueError(f"n_tasks doit être >= 1, reçu {n_tasks}")
    if var_sum <= 0.0:
        raise ValueError("variance nulle : le MDE n'est pas défini (suite saturée ?), voir observed_variance_sum")
    return _z_total(alpha, power, sides) * math.sqrt(var_sum / n_tasks)


def n_required(
    mde: float,
    *,
    var_sum: float = WORST_CASE_VARIANCE_SUM,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sides: int = DEFAULT_SIDES,
) -> int:
    """Nombre de tâches par bras requis pour détecter un écart ``mde`` (proportion)."""
    if not 0.0 < mde <= 1.0:
        raise ValueError(f"mde doit être dans ]0, 1], reçu {mde}")
    if var_sum <= 0.0:
        raise ValueError("variance nulle : n requis non défini (suite saturée ?), voir observed_variance_sum")
    z_total = _z_total(alpha, power, sides)
    # -1e-9 : un n exact (flottant 30.0000000001) ne doit pas monter d'une unité.
    return max(1, math.ceil(z_total**2 * var_sum / mde**2 - 1e-9))


def observed_variance_sum(rate_a: float | None, rate_b: float | None) -> tuple[float | None, str | None]:
    """``(variance, None)`` ou ``(None, raison)`` quand les taux observés ne permettent pas d'estimer la variance."""
    if rate_a is None or rate_b is None:
        return None, f"taux du bras {'a' if rate_a is None else 'b'} inconnu (aucune tâche mesurée)"
    for name, rate in (("a", rate_a), ("b", rate_b)):
        if rate <= 0.0 or rate >= 1.0:
            return None, (
                f"taux observé du bras {name} = {rate:g} : variance nulle (suite saturée ou échec total), "
                "elle n'estime pas la variance de la population de tâches"
            )
    return variance_sum(rate_a, rate_b), None


def _table(
    var_sum: float, sizes: Sequence[int], target_mde: float, *, alpha: float, power: float, sides: int
) -> dict[str, Any]:
    by_n: dict[str, Any] = {}
    for n in sizes:
        mde = minimum_detectable_effect(n, var_sum=var_sum, alpha=alpha, power=power, sides=sides)
        # Au-dessus de 1 (100 points), l'écart ne peut pas exister : aucun effet détectable à ce n.
        by_n[str(n)] = {"mde": mde, "detectable": mde <= 1.0}
    return {
        "variance_sum": var_sum,
        "n_required": n_required(target_mde, var_sum=var_sum, alpha=alpha, power=power, sides=sides),
        "by_n": by_n,
    }


def power_report(
    *,
    tasks_run: int,
    rate_a: float | None = None,
    rate_b: float | None = None,
    target_mde: float = DEFAULT_TARGET_MDE,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sides: int = DEFAULT_SIDES,
) -> dict[str, Any]:
    """Tableau de puissance : n = 3, n = 30 et ``tasks_run``, bases ``worst_case`` et ``observed``.

    ``n_reached`` vaut vrai seulement si ``tasks_run`` atteint le n requis ; la base retenue pour ce
    verdict est ``observed`` quand elle existe, sinon ``worst_case`` (champ ``n_reached_basis``).
    """
    sizes = sorted({*REFERENCE_SIZES, max(tasks_run, 1)})
    worst = _table(WORST_CASE_VARIANCE_SUM, sizes, target_mde, alpha=alpha, power=power, sides=sides)
    observed_sum, reason = observed_variance_sum(rate_a, rate_b)
    observed: dict[str, Any] | None = None
    if observed_sum is not None:
        observed = {**_table(observed_sum, sizes, target_mde, alpha=alpha, power=power, sides=sides), "rates": [rate_a, rate_b]}
    basis = "observed" if observed else "worst_case"
    required = (observed or worst)["n_required"]
    return {
        "method": "approximation normale, deux bras de n tâches, une tâche = une unité binaire (pass^k)",
        "alpha": alpha,
        "power": power,
        "sides": sides,
        "target_mde": target_mde,
        "tasks_run": tasks_run,
        "worst_case": worst,
        "observed": observed,
        "observed_unavailable_reason": reason,
        "n_reached_basis": basis,
        "n_reached": tasks_run >= required,
    }


def render_power_lines(report: Mapping[str, Any]) -> list[str]:
    """Lignes de texte (français) pour un ``power_report``."""
    lines = [
        f"Puissance (alpha {report['alpha']}, puissance {report['power']}, {report['sides']} côté(s)) — "
        f"{report['method']}.",
        f"MDE visé : {report['target_mde'] * 100:.0f} points. Tâches rejouées : {report['tasks_run']}.",
    ]
    for basis in ("worst_case", "observed"):
        table = report.get(basis)
        label = "pire cas (p = 0,5)" if basis == "worst_case" else "variance observée"
        if table is None:
            lines.append(f"- {label} : NON ESTIMABLE — {report.get('observed_unavailable_reason')}")
            continue
        lines.append(f"- {label} : n requis pour {report['target_mde'] * 100:.0f} points = {table['n_required']} tâches par bras.")
        for n, cell in table["by_n"].items():
            shown = f"{cell['mde'] * 100:.1f} points" if cell["detectable"] else f"{cell['mde'] * 100:.0f} points (> 100 : aucun effet détectable)"
            lines.append(f"    n = {n} : MDE {shown}")
    verdict = "ATTEINT" if report["n_reached"] else "NON ATTEINT"
    lines.append(f"n requis {verdict} (base {report['n_reached_basis']}).")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MDE et n requis pour pass^k (B-02), sans donnée.")
    parser.add_argument("--mde", type=float, default=DEFAULT_TARGET_MDE, help="effet visé en proportion (0.10 = 10 points)")
    parser.add_argument("--tasks", type=int, default=20, help="nombre de tâches du banc")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--power", type=float, default=DEFAULT_POWER)
    parser.add_argument("--sides", type=int, choices=(1, 2), default=DEFAULT_SIDES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = power_report(
        tasks_run=args.tasks, target_mde=args.mde, alpha=args.alpha, power=args.power, sides=args.sides
    )
    print(json.dumps(report, indent=2) if args.json else "\n".join(render_power_lines(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
