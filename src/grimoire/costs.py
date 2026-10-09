"""Le coût d'un appel, d'une tentative ou d'un run — trois états, jamais un faux zéro (W1-01, issue #709).

Un fournisseur headless qui ne rend pas son coût (la plupart : seule une sortie
JSON portant ``total_cost_usd`` en donne un) laisse ce coût *inconnu*. Le kit
l'additionnait comme ``0.0`` : un plafond de dépense ne se déclenchait jamais,
et le coût par tâche résolue était tiré vers zéro sans le dire.

:class:`Cost` porte l'inconnu de bout en bout : une somme est ``exact`` quand
tous ses appels sont chiffrés, ``lower_bound`` quand certains seulement le
sont (le montant connu est alors un minimum, jamais le total), ``unknown``
quand aucun ne l'est (le montant est ``None``, pas ``0.0``).

Le plafond de coût applique une politique déclarée (:data:`POLICY_STOP` par
défaut, fail-closed) via :func:`cap_reason` — un seul endroit pour toutes les
comparaisons à un plafond, dispatch et genres de flow compris.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "COST_POLICIES",
    "POLICY_CONTINUE_FLAGGED",
    "POLICY_STOP",
    "Cost",
    "cap_reason",
]

#: Un coût inconnu face à un plafond arrête l'escalade (défaut, fail-closed).
POLICY_STOP = "stop"
#: Un coût inconnu ne bloque pas, mais le dépassement possible reste signalé
#: par le statut du coût (``unknown``/``lower_bound``) dans tous les rapports.
POLICY_CONTINUE_FLAGGED = "continue_flagged"
COST_POLICIES: tuple[str, ...] = (POLICY_STOP, POLICY_CONTINUE_FLAGGED)


@dataclass(frozen=True, slots=True)
class Cost:
    """Somme de coûts avec la part inchiffrée. Construire via les fabriques, additionner avec ``+``."""

    known_usd: float
    priced_calls: int
    unpriced_calls: int

    @classmethod
    def exact(cls, usd: float) -> Cost:
        return cls(float(usd), 1, 0)

    @classmethod
    def unpriced(cls, calls: int = 1) -> Cost:
        return cls(0.0, 0, max(1, int(calls)))

    @classmethod
    def none(cls) -> Cost:
        """Aucun appel : élément neutre de la somme, ``exact`` à 0 appel."""
        return cls(0.0, 0, 0)

    @classmethod
    def from_parts(cls, usd: float | None, unpriced_calls: int = 0) -> Cost:
        """Reconstruit un coût depuis sa forme sérialisée (``usd`` ``None`` = inconnu)."""
        if usd is None:
            return cls.unpriced(unpriced_calls)
        return cls(float(usd), 1, max(0, int(unpriced_calls)))

    @classmethod
    def coerce(cls, value: Cost | float | int | None) -> Cost:
        """``None`` est inconnu, un nombre est exact — l'API historique ``(tags, float)`` reste valide."""
        if isinstance(value, Cost):
            return value
        return cls.unpriced() if value is None else cls.exact(value)

    @classmethod
    def total(cls, costs: Iterable[Cost]) -> Cost:
        result = cls.none()
        for cost in costs:
            result = result + cost
        return result

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            self.known_usd + other.known_usd,
            self.priced_calls + other.priced_calls,
            self.unpriced_calls + other.unpriced_calls,
        )

    @property
    def status(self) -> str:
        """``exact`` | ``lower_bound`` | ``unknown``."""
        if self.unpriced_calls == 0:
            return "exact"
        return "lower_bound" if self.priced_calls > 0 else "unknown"

    @property
    def usd(self) -> float | None:
        """Le montant connu — ``None`` quand rien n'est chiffré, jamais ``0.0`` à la place."""
        return None if self.status == "unknown" else self.known_usd

    def to_dict(self) -> dict[str, Any]:
        return {"usd": self.usd, "status": self.status, "unpriced_calls": self.unpriced_calls}

    def render(self) -> str:
        """``0,42 USD`` | ``>= 0,42 USD (3 non pricés)`` | ``inconnu (2 appels non pricés)``."""
        if self.status == "exact":
            return f"{self.known_usd:.4f} USD"
        if self.status == "lower_bound":
            return f">= {self.known_usd:.4f} USD ({self.unpriced_calls} non pricés)"
        return f"inconnu ({self.unpriced_calls} non pricés)"


def cap_reason(cost: Cost, cap: float, policy: str, *, inclusive: bool = True) -> str | None:
    """Pourquoi *cost* arrête une escalade sous le plafond *cap* — ``None`` si elle peut continuer.

    ``"cost_reached"`` : le montant connu (un minimum) atteint déjà le plafond,
    quelle que soit la part inchiffrée. ``"cost_unknown"`` : la part inchiffrée
    empêche d'affirmer qu'on est sous le plafond, et la politique est
    :data:`POLICY_STOP`. *inclusive* distingue ``>=`` (dispatch) de ``>`` (genres de flow).
    """
    reached = cost.known_usd >= cap if inclusive else cost.known_usd > cap
    if reached and cost.priced_calls > 0:
        return "cost_reached"
    if cost.unpriced_calls > 0 and policy == POLICY_STOP:
        return "cost_unknown"
    return None
