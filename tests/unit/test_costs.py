"""``grimoire.costs`` : le coût à trois états et la décision de plafond (W1-01, issue #709)."""

from __future__ import annotations

import pytest

from grimoire.costs import POLICY_CONTINUE_FLAGGED, POLICY_STOP, Cost, cap_reason


def test_somme_vide_est_exacte_a_zero_appel() -> None:
    total = Cost.total([])
    assert total.status == "exact"
    assert total.usd == 0.0
    assert total.unpriced_calls == 0


def test_tous_inconnus_est_unknown_et_usd_none() -> None:
    total = Cost.total([Cost.unpriced(), Cost.unpriced()])
    assert (total.status, total.usd, total.unpriced_calls) == ("unknown", None, 2)


def test_un_connu_un_inconnu_est_lower_bound() -> None:
    total = Cost.exact(0.42) + Cost.unpriced()
    assert (total.status, total.usd, total.unpriced_calls) == ("lower_bound", 0.42, 1)
    assert total.render() == ">= 0.4200 USD (1 non pricés)"


def test_from_parts_none_compte_au_moins_un_appel_non_price() -> None:
    assert Cost.from_parts(None, 0).unpriced_calls == 1
    assert Cost.from_parts(None, 3).unpriced_calls == 3
    assert Cost.from_parts(0.2, 2).status == "lower_bound"


def test_coerce_none_est_inconnu_et_un_nombre_est_exact() -> None:
    assert Cost.coerce(None).status == "unknown"
    assert Cost.coerce(0.3).status == "exact"
    assert Cost.coerce(Cost.exact(1.0)) == Cost.exact(1.0)


@pytest.mark.parametrize(
    ("cost", "policy", "inclusive", "expected"),
    [
        (Cost.exact(0.5), POLICY_STOP, True, "cost_reached"),
        (Cost.exact(0.4), POLICY_STOP, True, "cost_reached"),  # égalité : inclusif
        (Cost.exact(0.4), POLICY_STOP, False, None),  # égalité : strict (genres de flow)
        (Cost.exact(0.1), POLICY_STOP, True, None),
        (Cost.unpriced(), POLICY_STOP, True, "cost_unknown"),
        (Cost.exact(0.1) + Cost.unpriced(), POLICY_STOP, True, "cost_unknown"),
        (Cost.exact(0.9) + Cost.unpriced(), POLICY_STOP, True, "cost_reached"),  # le minimum suffit
        (Cost.unpriced(), POLICY_CONTINUE_FLAGGED, True, None),
        (Cost.exact(0.9) + Cost.unpriced(), POLICY_CONTINUE_FLAGGED, True, "cost_reached"),
    ],
)
def test_cap_reason(cost: Cost, policy: str, inclusive: bool, expected: str | None) -> None:
    assert cap_reason(cost, 0.4, policy, inclusive=inclusive) == expected
