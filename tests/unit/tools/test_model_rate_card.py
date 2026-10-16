"""Table de tarifs versionnée, datée et sourcée (W4-02, « Dépasser AI-DLC »).

Défauts visés sur la base 5b4d8edc :
- `_estimate_model_cost` reconnaît une famille par sous-chaîne (« opus » vaut
  n'importe quel opus), pas une version : un modèle non listé reçoit un tarif ;
- les tarifs de famille (0.8/4, 3/15, 15/75) sont ceux de modèles retirés ;
- la table ne porte ni date ni source ;
- le coût est multiplié par 1000 (taux $/MTok appliqué à des tokens bruts).
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from grimoire.cli.cmd_task import _estimate_model_cost
from grimoire.tools import cost_model as cm


class TestVersionedRateCard:
    def test_each_version_is_priced_on_its_own_row(self) -> None:
        assert cm.rate_for("claude-opus-5-5") == {"in": 4.0, "out": 20.0}
        assert cm.rate_for("claude-opus-5") == {"in": 5.0, "out": 25.0}

    def test_unlisted_model_has_no_rate(self) -> None:
        assert cm.rate_for("claude-opus-4-2") is None
        assert cm.rate_for("claude-sonnet-4-7") is None
        assert cm.rate_for("claude-opus") is None
        assert cm.rate_for("totally-unknown-model") is None

    def test_dated_and_dotted_ids_resolve_to_their_version(self) -> None:
        assert cm.rate_for("claude-haiku-4-5-20251001") == cm.rate_for("claude-haiku-4-5")
        assert cm.rate_for("claude-sonnet-4.6") == {"in": 3.0, "out": 15.0}

    def test_unlisted_model_cost_is_none(self) -> None:
        assert _estimate_model_cost("claude-opus-4-2", 1000, 0) is None

    def test_cost_is_in_dollars_per_million_tokens(self) -> None:
        # 1 M tokens d'entrée à 3 $/MTok = 3.0 $ (la base rendait 3000.0).
        assert _estimate_model_cost("claude-sonnet-4-6", 1_000_000, 0) == 3.0
        assert _estimate_model_cost("claude-sonnet-4-6", 0, 1_000_000) == 15.0

    def test_cmd_task_reads_the_single_table_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Une seule table : la commande lit cost_model, pas une copie.
        monkeypatch.setattr(cm, "MODEL_RATES", {"zeta-1": {"in": 1.0, "out": 2.0}})
        assert _estimate_model_cost("claude-zeta-1", 1_000_000, 0) == 1.0


class TestRateRowValidation:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
    def test_nan_infinite_or_negative_price_is_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError):
            cm.validate_rate_table({"x-1": {"in": bad, "out": 1.0}})
        with pytest.raises(ValueError):
            cm.validate_rate_table({"x-1": {"in": 1.0, "out": bad}})

    def test_valid_table_is_returned_unchanged(self) -> None:
        table = {"x-1": {"in": 0.0, "out": 2.5}}
        assert cm.validate_rate_table(table) == table
        assert all(math.isfinite(v) for row in cm.MODEL_RATES.values() for v in row.values())


class TestDatingAndSource:
    def test_card_is_dated_and_sourced(self) -> None:
        assert date(2026, 10, 16) == cm.RATE_CARD_AS_OF
        assert cm.RATE_CARD_SOURCE.startswith("https://")

    def test_warning_only_beyond_90_days(self) -> None:
        assert cm.rate_card_age_warning(date(2026, 10, 16)) is None
        assert cm.rate_card_age_warning(date(2027, 1, 14)) is None
        assert cm.rate_card_age_warning(date(2027, 1, 15)) is not None  # 91 jours

    def test_payload_keeps_the_family_view_for_the_web_observer(self) -> None:
        payload = cm.cost_model()
        assert set(payload["modelRates"]) >= {"opus", "sonnet", "haiku"}
        assert payload["modelRateCard"]["asOf"] == "2026-10-16"
