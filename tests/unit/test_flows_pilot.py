"""Le pilote : la fonction qui décide combien dépenser par node (issue #209).

Deux couches testées séparément : la décision pure (:func:`decide`, sans
aucun appel réel) et le chargement/la validation de ``pilot.yaml`` — le
câblage bout-en-bout dans ``DispatchExecutor`` (politique → palier de départ
observé) vit dans ``test_flows_dispatch_executor_pilot.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.pilot import PilotPolicy, decide, load_pilot_policy
from grimoire.missions.verifiability import Verifiability

# ── decide() : logique pure ──────────────────────────────────────────────────


def test_decide_sans_politique_ne_donne_aucun_avis() -> None:
    decision = decide(Verifiability.V0, policy=PilotPolicy())
    assert decision.start_tier is None
    assert decision.max_tier is None
    assert decision.max_cost_usd is None


def test_decide_v2_ne_pilote_rien_mais_transmet_le_plafond() -> None:
    policy = PilotPolicy(start_tier={"V0": "strong"}, max_cost_usd_per_node=0.5)
    decision = decide(Verifiability.V2, policy=policy)
    assert decision.start_tier is None
    assert decision.max_tier is None
    assert decision.max_cost_usd == 0.5


def test_decide_applique_le_palier_de_depart_declare() -> None:
    policy = PilotPolicy(start_tier={"V0": "mid"})
    decision = decide(Verifiability.V0, policy=policy)
    assert decision.start_tier == "mid"


def test_decide_ne_descend_jamais_sous_le_plancher_de_la_classe() -> None:
    # V1 a pour plancher "mid" (voir missions.dispatch._START_TIER) — une
    # politique qui demande "cheap" est relevée, jamais appliquée telle quelle.
    policy = PilotPolicy(start_tier={"V1": "cheap"})
    decision = decide(Verifiability.V1, policy=policy)
    assert decision.start_tier == "mid"


def test_decide_max_escalations_zero_interdit_toute_escalade() -> None:
    policy = PilotPolicy(max_escalations=0)
    decision = decide(Verifiability.V0, policy=policy)
    assert decision.max_tier == "cheap"  # V0 part à "cheap", 0 escalade permise


def test_decide_max_escalations_un_autorise_un_seul_palier_de_plus() -> None:
    policy = PilotPolicy(max_escalations=1)
    decision = decide(Verifiability.V0, policy=policy)
    assert decision.max_tier == "mid"


def test_decide_max_escalations_au_dela_du_dernier_palier_plafonne_a_strong() -> None:
    policy = PilotPolicy(max_escalations=99)
    decision = decide(Verifiability.V0, policy=policy)
    assert decision.max_tier == "strong"


def test_decide_max_escalations_compte_depuis_le_palier_de_depart_declare() -> None:
    policy = PilotPolicy(start_tier={"V0": "mid"}, max_escalations=1)
    decision = decide(Verifiability.V0, policy=policy)
    assert decision.start_tier == "mid"
    assert decision.max_tier == "strong"


# ── load_pilot_policy() : absence, validité, refus nommé ────────────────────


def test_load_pilot_policy_absente_rend_la_politique_par_defaut(tmp_path: Path) -> None:
    policy = load_pilot_policy(tmp_path)
    assert policy == PilotPolicy()


def test_load_pilot_policy_valide(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text(
        "start_tier:\n  V0: mid\nmax_escalations: 1\nmax_cost_usd_per_node: 0.5\n",
        encoding="utf-8",
    )
    policy = load_pilot_policy(tmp_path)
    assert policy.start_tier == {"V0": "mid"}
    assert policy.max_escalations == 1
    assert policy.max_cost_usd_per_node == 0.5


def test_load_pilot_policy_unknown_key_est_refuse_nomme(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text("bogus_key: 1\n", encoding="utf-8")
    with pytest.raises(GrimoireRuntimeError, match="bogus_key"):
        load_pilot_policy(tmp_path)


def test_load_pilot_policy_unknown_class_est_refuse_nomme(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text(
        "start_tier:\n  V9: mid\n", encoding="utf-8"
    )
    with pytest.raises(GrimoireRuntimeError, match="V9"):
        load_pilot_policy(tmp_path)


def test_load_pilot_policy_unknown_tier_est_refuse_nomme(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text(
        "start_tier:\n  V0: ultra\n", encoding="utf-8"
    )
    with pytest.raises(GrimoireRuntimeError, match="ultra"):
        load_pilot_policy(tmp_path)


def test_load_pilot_policy_max_cost_negatif_est_refuse_nomme(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text(
        "max_cost_usd_per_node: -1\n", encoding="utf-8"
    )
    with pytest.raises(GrimoireRuntimeError):
        load_pilot_policy(tmp_path)


def test_load_pilot_policy_malformed_yaml_refuse_au_lieu_de_retomber_en_silence(tmp_path: Path) -> None:
    """Contrairement à ``orchestration-policy.yaml`` (best-effort) : un
    plafond de coût est un mécanisme de sécurité, une erreur de frappe ne
    doit jamais se traduire en « aucun plafond » silencieux."""
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire" / "standard" / "pilot.yaml").write_text(
        "not: [valid, yaml", encoding="utf-8"
    )
    with pytest.raises(GrimoireRuntimeError):
        load_pilot_policy(tmp_path)
