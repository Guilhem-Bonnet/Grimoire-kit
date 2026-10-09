"""Le coût à trois états du ``TokenUsage`` aux statistiques de dispatch (W1-01, issue #709).

Un test de propriété (valeurs tirées d'un générateur à graine fixe — pas de
``hypothesis`` dans les dépendances de dev du kit à ce jour) vérifie la
promesse de la fiche : pour tout usage dont au moins un appel n'a pas de prix,
aucun champ de coût exposé ne vaut ``0.0``, quel que soit le chemin
(``DispatchReport``, trace, statistiques, node de flow, run de flow).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.flows.dispatch_executor import FlowDispatchOutcome, _node_outcome_from_report
from grimoire.missions.dispatch import DispatchAttempt, DispatchReport, _record_dispatch_outcome
from grimoire.missions.verifiability import Verifiability
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger, compute_dispatch_outcome_stats
from grimoire.traces.schemas import TraceOutcome

_TAGS = (DISPATCH_OUTCOME_TAG, "class:V0", "provider:p", "tier:cheap", "acceptance:judged", "resolved:true", "replay:k")


def _ledger_record(ledger: TraceLedger, token_usage: dict, *, run_id: str = "r") -> None:
    ledger.record(
        run_id=run_id,
        workflow_instance_id="",
        mission_id="",
        task_id="GAO-1",
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-01-01T00:00:00+00:00",
        token_usage=token_usage,
        tags=list(_TAGS),
    )


# ── Lecture tolérante de trace.v1 ────────────────────────────────────────────


def _write_v1_line(path: Path, cost: float | None) -> None:
    usage: dict = {"prompt_tokens": 1}
    if cost is not None:
        usage["estimated_cost_usd"] = cost
    line = {
        "id": "TRC-1",
        "schema_version": "grimoire.trace.v1",
        "run_id": "r1",
        "workflow_instance_id": "",
        "mission_id": "",
        "task_id": "GAO-1",
        "recipe_id": "grimoire.dispatch",
        "outcome": "success",
        "token_usage": usage,
        "tags": list(_TAGS),
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def test_trace_v1_a_zero_est_lue_comme_inconnue_pas_comme_gratuite(tmp_path: Path) -> None:
    """v1 écrivait ``0.0`` pour « inconnu » : le relire comme exact tirerait les moyennes vers zéro."""
    _write_v1_line(tmp_path / "traces.jsonl", 0.0)

    stats = TraceLedger(tmp_path).dispatch_outcome_stats()

    assert stats.overall.total == 1
    assert stats.overall.total_cost_usd is None
    assert stats.overall.cost_per_resolved_task_usd is None
    assert stats.to_dict()["overall"]["cost_status"] == "unknown"


def test_trace_v1_a_montant_positif_reste_exacte(tmp_path: Path) -> None:
    _write_v1_line(tmp_path / "traces.jsonl", 0.25)

    stats = TraceLedger(tmp_path).dispatch_outcome_stats()

    assert stats.overall.total_cost_usd == pytest.approx(0.25)
    assert stats.to_dict()["overall"]["cost_status"] == "exact"


def test_une_trace_ecrite_aujourd_hui_porte_le_schema_v2(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _ledger_record(ledger, {"estimated_cost_usd": 0.1})

    assert ledger.list_traces()[0].schema_version == "grimoire.trace.v2"


# ── Statistiques : inconnu, borne basse, exact ───────────────────────────────


def test_stats_de_couts_tous_inconnus_rendent_inconnu_pas_zero(tmp_path: Path) -> None:
    """Critère 2 de #709 : le chemin d'un fournisseur muet (aucun coût dans ``token_usage``)."""
    ledger = TraceLedger(tmp_path)
    _ledger_record(ledger, {"prompt_tokens": 10}, run_id="a")
    _ledger_record(ledger, {"prompt_tokens": 10}, run_id="b")

    overall = ledger.dispatch_outcome_stats().to_dict()["overall"]

    assert overall["total_cost_usd"] is None
    assert overall["cost_per_resolved_task_usd"] is None
    assert overall["cost_status"] == "unknown"
    assert overall["unpriced_calls"] == 2


def test_stats_d_un_connu_et_d_un_inconnu_sont_une_borne_basse(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _ledger_record(ledger, {"estimated_cost_usd": 0.42}, run_id="a")
    _ledger_record(ledger, {"prompt_tokens": 10}, run_id="b")

    overall = ledger.dispatch_outcome_stats().to_dict()["overall"]

    assert overall["total_cost_usd"] == pytest.approx(0.42)
    assert overall["cost_status"] == "lower_bound"
    assert overall["unpriced_calls"] == 1
    assert overall["cost_per_resolved_task_usd"] == pytest.approx(0.21)  # un minimum, signalé par le statut


def test_compute_accepte_encore_des_floats_et_traite_none_comme_inconnu() -> None:
    stats = compute_dispatch_outcome_stats([(_TAGS, 0.5), (_TAGS, None)])

    assert stats.overall.cost.status == "lower_bound"
    assert stats.overall.total_cost_usd == pytest.approx(0.5)
    assert stats.by_flow == {} or all(g.cost.status == "lower_bound" for g in stats.by_flow.values())


# ── Propriété : aucun chemin ne transforme un appel sans prix en 0.0 ──────────


def _attempt(n: int, cost: float | None) -> DispatchAttempt:
    return DispatchAttempt(
        attempt=n,
        tier="cheap",
        provider="p",
        model="m",
        exit_code=0,
        duration_s=0.1,
        checks=(),
        verdict="red" if n < 99 else "green",
        cost_usd=cost,
    )


def _report(costs: list[float | None]) -> DispatchReport:
    return DispatchReport(
        task_id="GAO-1",
        verifiability="V0",
        dry_run=False,
        planned_chain=("cheap",),
        prompt="",
        attempts=tuple(_attempt(i + 1, c) for i, c in enumerate(costs)),
    )


def _expected(costs: list[float | None]) -> tuple[float | None, str, int]:
    known = [c for c in costs if c is not None]
    missing = len(costs) - len(known)
    usd = sum(known) if known else None
    status = "exact" if not missing else ("lower_bound" if known else "unknown")
    return usd, status, missing


def _assert_honest(usd: float | None, status: str, unpriced: int, costs: list[float | None], where: str) -> None:
    exp_usd, exp_status, exp_unpriced = _expected(costs)
    assert status == exp_status, where
    assert unpriced == exp_unpriced, where
    if exp_usd is None:
        assert usd is None, f"{where} : un coût inconnu doit être None, pas {usd!r}"
    else:
        assert usd == pytest.approx(exp_usd), where
        assert usd != 0.0, where


def test_propriete_aucun_champ_de_cout_ne_vaut_zero_quand_un_appel_n_a_pas_de_prix(tmp_path: Path) -> None:
    rng = random.Random(709)
    cases = 0
    for case in range(120):
        costs: list[float | None] = [
            None if rng.random() < 0.5 else round(rng.uniform(0.01, 3.0), 4) for _ in range(rng.randint(1, 5))
        ]
        if all(c is not None for c in costs):
            costs[rng.randrange(len(costs))] = None  # on ne teste ici que les cas avec au moins un appel sans prix
        cases += 1
        report = _report(costs)

        # 1. DispatchReport
        rd = report.to_dict()["cost"]
        _assert_honest(rd["usd"], rd["status"], rd["unpriced_calls"], costs, f"DispatchReport#{case}")

        # 2. Trace dispatch.outcome écrite par run_dispatch, relue par le ledger
        root = tmp_path / f"c{case}"
        _record_dispatch_outcome(
            root, SimpleNamespace(id="GAO-1"), report, acceptance_declared=False, replay_key="bp:n"
        )
        ledger = TraceLedger(root / TRACES_DIR)
        usage = ledger.list_traces()[0].token_usage
        _assert_honest(usage.estimated_cost_usd, usage.cost.status, usage.unpriced_calls, costs, f"TokenUsage#{case}")
        assert usage.to_dict()["estimated_cost_usd"] != 0.0

        # 3. Statistiques (overall, par classe, par fournisseur, par flow)
        stats = ledger.dispatch_outcome_stats().to_dict()
        groups = [
            stats["overall"],
            *stats["by_class"].values(),
            *stats["by_provider"].values(),
            *stats["by_flow"].values(),
        ]
        assert len(groups) >= 4
        for group in groups:
            _assert_honest(
                group["total_cost_usd"], group["cost_status"], group["unpriced_calls"], costs, f"stats#{case}"
            )
            per_task = group["cost_per_resolved_task_usd"]
            assert per_task != 0.0, f"stats#{case}"

        # 4. Node de flow, puis run de flow
        node = _node_outcome_from_report("n", "T", Verifiability.V0, report, has_structured_acceptance=False)
        nd = node.to_dict()
        _assert_honest(nd["cost_usd"], nd["cost_status"], nd["unpriced_calls"], costs, f"node#{case}")
        flow = FlowDispatchOutcome(run_id="r", status="finished", node_id=None, nodes=(node, node)).to_dict()
        _assert_honest(
            flow["total_cost_usd"], flow["cost_status"], flow["unpriced_calls"], costs + costs, f"flow#{case}"
        )
    assert cases == 120


def test_cas_limite_un_appel_chiffre_a_zero_explicite_reste_un_zero_connu_signale_borne_basse() -> None:
    """Un zéro *déclaré* (fournisseur gratuit) n'est pas un inconnu : le statut seul dit qu'il manque des appels."""
    report = _report([0.0, None])

    cost = report.to_dict()["cost"]

    assert cost == {"usd": 0.0, "status": "lower_bound", "unpriced_calls": 1}
