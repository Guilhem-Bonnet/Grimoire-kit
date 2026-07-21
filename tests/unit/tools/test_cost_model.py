"""Tests du modèle de coût calibré (C2) et de l'endpoint /api/cost-model."""

from __future__ import annotations

from pathlib import Path

from grimoire.tools import cost_model as cm
from grimoire.tools.forge_server import ForgeAPI


class TestCostModel:
    def test_payload_shape_and_calibrated_flag(self) -> None:
        model = cm.cost_model()
        assert model["schemaVersion"] == cm.COST_MODEL_SCHEMA_VERSION
        assert model["calibrated"] is True
        for key in ("patterns", "modelRates", "missionCapsK", "tierWindowFraction"):
            assert key in model
        assert model["patterns"]["ORC-01"] == {"in": 4.5, "out": 2.4, "runs": 3}

    def test_window_follows_target_model(self) -> None:
        # copilot par défaut (200k) ; un modèle à grande fenêtre remonte.
        assert cm.cost_model()["window"] == 200_000
        assert cm.cost_model("gemini-3-pro")["window"] == 1_000_000

    def test_node_entry_tokens_calibrated_per_pattern(self) -> None:
        # ORC-01 : in 4.5k × runs 3 = 13500 tokens d'entrée.
        assert cm.node_entry_tokens("ORC-01") == 13500
        # pattern inconnu → défaut catalogue (5k) ; externe → défaut ext (18k).
        assert cm.node_entry_tokens("ZZZ-99") == 5000
        assert cm.node_entry_tokens("ZZZ-99", is_ext=True) == 18000

    def test_tier_ceiling_scales_with_window(self) -> None:
        assert cm.tier_ceiling("deep") == int(200_000 * 0.80)
        assert cm.tier_ceiling("tiny") == int(200_000 * 0.02)
        # tier inconnu → medium.
        assert cm.tier_ceiling("bogus") == cm.tier_ceiling("medium")

    def test_cost_under_assertion_against_rates(self) -> None:
        # sonnet : 3 $/MTok in, 15 $/MTok out. 100k in + 20k out = 0.6 $.
        assert cm.estimate_usd(100_000, 20_000, "sonnet") == 0.6
        assert cm.cost_under(100_000, 20_000, cap_usd=1.0, model="sonnet") is True
        assert cm.cost_under(100_000, 20_000, cap_usd=0.5, model="sonnet") is False
        # modèle inconnu → repli sonnet (jamais d'exception).
        assert cm.estimate_usd(1_000, 0, "inconnu") == cm.estimate_usd(
            1_000, 0, "sonnet"
        )

    def test_endpoint_returns_model(self, tmp_path: Path) -> None:
        api = ForgeAPI(tmp_path, tmp_path, None)
        payload = api.cost_model_view()
        assert payload["calibrated"] is True
        assert payload["schemaVersion"] == cm.COST_MODEL_SCHEMA_VERSION
