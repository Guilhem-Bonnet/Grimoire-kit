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


def _in_out(row: dict[str, float] | None) -> tuple[float, float] | None:
    return None if row is None else (row["in"], row["out"])


class TestVersionedRateCard:
    def test_each_version_is_priced_on_its_own_row(self) -> None:
        assert _in_out(cm.rate_for("claude-opus-5-5")) == (4.0, 20.0)
        assert _in_out(cm.rate_for("claude-opus-5")) == (5.0, 25.0)

    def test_unlisted_model_has_no_rate(self) -> None:
        assert cm.rate_for("claude-opus-4-2") is None
        assert cm.rate_for("claude-sonnet-4-7") is None
        assert cm.rate_for("claude-opus") is None
        assert cm.rate_for("totally-unknown-model") is None

    def test_dated_and_dotted_ids_resolve_to_their_version(self) -> None:
        assert cm.rate_for("claude-haiku-4-5-20251001") == cm.rate_for("claude-haiku-4-5")
        assert _in_out(cm.rate_for("claude-sonnet-4.6")) == (3.0, 15.0)

    def test_each_row_carries_the_five_published_rates(self) -> None:
        for key, row in cm.MODEL_RATES.items():
            assert set(row) == {"in", "out", "cache_read", "cache_write_5m", "cache_write_1h"}, key

    def test_the_new_versions_are_priced_from_the_2026_11_12_card(self) -> None:
        assert cm.MODEL_RATES["fable-5-2"] == {
            "in": 8.0,
            "out": 40.0,
            "cache_read": 0.20,
            "cache_write_5m": 10.0,
            "cache_write_1h": 16.0,
        }
        assert _in_out(cm.rate_for("claude-haiku-5-5")) == (0.10, 0.50)
        # Palier haut : prompt > 100 000 jetons (entrée + cache lu + cache écrit).
        assert _in_out(cm.rate_for_call("claude-haiku-5-5", 120_000)) == (0.50, 2.50)
        assert _in_out(cm.rate_for_call("claude-haiku-5-5", 100_000)) == (0.10, 0.50)

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
        table = {
            "x-1": {
                "in": 0.0,
                "out": 2.5,
                "cache_read": 0.0,
                "cache_write_5m": 0.0,
                "cache_write_1h": 0.0,
            }
        }
        assert cm.validate_rate_table(table) == table
        assert all(math.isfinite(v) for row in cm.MODEL_RATES.values() for v in row.values())

    def test_a_row_without_a_cache_write_rate_is_rejected(self) -> None:
        row = {"in": 1.0, "out": 2.0, "cache_read": 0.1, "cache_write_5m": 1.25}
        with pytest.raises(ValueError, match="cache_write_1h"):
            cm.validate_rate_table({"x-1": row})


class TestDatingAndSource:
    def test_card_is_dated_and_sourced(self) -> None:
        assert date(2026, 11, 12) == cm.RATE_CARD_AS_OF
        assert cm.RATE_CARD_SOURCE.startswith("https://")

    def test_warning_only_beyond_90_days(self) -> None:
        assert cm.rate_card_age_warning(date(2026, 11, 12)) is None
        assert cm.rate_card_age_warning(date(2027, 2, 10)) is None
        assert cm.rate_card_age_warning(date(2027, 2, 11)) is not None  # 91 jours

    def test_payload_exposes_the_per_version_table_for_the_web(self) -> None:
        payload = cm.cost_model()
        assert payload["modelRates"] == cm.MODEL_RATES
        assert set(payload["modelAliases"]) == {"opus", "sonnet", "haiku"}
        assert payload["modelRateCard"]["asOf"] == "2026-11-12"
