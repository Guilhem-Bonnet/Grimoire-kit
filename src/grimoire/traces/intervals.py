"""Intervalles de confiance à 95 % pour les SLO du kit (W4-05).

Un SLO jugé sur la seule estimation ponctuelle punit le bruit : 2 séries sur 3
tout au vert face à un seuil de 80 % ne prouvent rien, et un gate ``enforce``
qui échoue là pénalise un échantillon, pas un défaut. Ces deux fonctions
bornent l'estimation ; le contrôle ``dispatch.cost_slo`` ne durcit un
dépassement que si l'intervalle entier est du mauvais côté du seuil.

Aucune dépendance (pas de scipy) : Wilson pour une proportion, estimateur de
ratio linéarisé avec quantile de Student pour un coût par tâche résolue.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = ["ratio_interval95", "wilson_interval95"]

_Z95 = 1.959964

#: Quantile bilatéral 95 % de Student, df = 1..30 (au-delà : paliers conservateurs).
_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)  # fmt: skip


def _t_quantile(df: int) -> float:
    if df <= len(_T95):
        return _T95[df - 1]
    if df <= 60:
        return 2.042
    if df <= 120:
        return 2.0
    return _Z95


def wilson_interval95(successes: int, total: int) -> tuple[float, float] | None:
    """Intervalle de Wilson à 95 % d'une proportion ; ``None`` sans observation.

    Contrairement à l'intervalle normal, il reste dans [0, 1] et ne s'effondre
    pas à un point quand ``successes`` vaut 0 ou ``total``.
    """
    if total <= 0 or not 0 <= successes <= total:
        return None
    p = successes / total
    z2 = _Z95 * _Z95
    denom = 1 + z2 / total
    centre = (p + z2 / (2 * total)) / denom
    half = _Z95 * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def ratio_interval95(samples: Sequence[tuple[float, int]]) -> tuple[float, float] | None:
    """Intervalle à 95 % de ``sum(coût) / sum(résolus)`` sur des ``(coût, résolu)`` par dispatch.

    Estimateur de ratio linéarisé : variance de ``coût - R * résolu`` sur la
    moyenne des résolus, quantile de Student à ``n - 1`` degrés. ``None`` si la
    variance n'est pas estimable (moins de deux dispatchs) ou sans aucun
    résolu : l'appelant retombe alors sur l'estimation ponctuelle, sans
    prétendre à une précision qu'il n'a pas. Borne basse à 0 (un coût est positif).
    """
    n = len(samples)
    resolved = sum(r for _, r in samples)
    if n < 2 or resolved <= 0:
        return None
    ratio = sum(c for c, _ in samples) / resolved
    residual_ss = sum((c - ratio * r) ** 2 for c, r in samples)
    mean_resolved = resolved / n
    std_err = math.sqrt(residual_ss / (n - 1) / n) / mean_resolved
    margin = _t_quantile(n - 1) * std_err
    return max(0.0, ratio - margin), ratio + margin
