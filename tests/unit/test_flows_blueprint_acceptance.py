"""Acceptance structurée d'un node de blueprint (issue #428).

``node.acceptance`` reste une liste de chaînes libres (rétrocompatible), mais
accepte désormais des entrées structurées ``{"run": ...}``, ``{"path_exists":
...}``, ``{"test": ...}`` — exécutables par le gate de
``flows.dispatch_executor``, plutôt qu'un simple vocabulaire que
``verifiability.classify`` sait reconnaître sans jamais rien exécuter. Ce
module teste uniquement le chargement/la dérivation (``blueprint_loader``),
pas l'exécution — voir ``test_flows_dispatch_executor_acceptance_gate.py``
pour la cascade complète.
"""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import build_node_contracts
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.verifiability import Verifiability, classify


def _blueprint(acceptance: Any) -> dict[str, Any]:
    return {
        "blueprintVersion": 1,
        "id": "acceptance-struct",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": acceptance,
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }


def _task_for(acceptance_texts: tuple[str, ...]) -> MissionTask:
    return MissionTask(
        id="T-1",
        mission_id="M-1",
        title="t",
        type=TaskType.IMPLEMENTATION,
        status=TaskState.PROPOSED,
        risk_profile=RiskProfile.STANDARD,
        acceptance=acceptance_texts,
        created_at="2026-09-11T00:00:00+00:00",
    )


# ── Rétrocompatibilité : texte libre inchangé ────────────────────────────────


def test_acceptance_textuelle_seule_reste_inchangee() -> None:
    contracts = build_node_contracts(_blueprint(["la suite de tests passe"]))
    n = contracts["n"]
    assert n.acceptance == ("la suite de tests passe",)
    assert n.acceptance_runs == ()
    assert n.acceptance_evidence == ()
    assert n.has_structured_acceptance is False


# ── Forme structurée « run » ──────────────────────────────────────────────────


def test_acceptance_run_produit_un_acceptance_run_et_un_texte_mecanique() -> None:
    contracts = build_node_contracts(
        _blueprint([{"run": "pytest -q tests/test_x.py", "expect_exit": 0, "timeout_s": 60}])
    )
    n = contracts["n"]
    assert len(n.acceptance_runs) == 1
    run = n.acceptance_runs[0]
    assert run.raw == "pytest -q tests/test_x.py"
    assert run.argv == ("pytest", "-q", "tests/test_x.py")
    assert run.expect_exit == 0
    assert run.timeout_s == 60.0
    assert run.cwd == "."
    assert n.has_structured_acceptance is True
    # Le texte dérivé doit rester mécanique pour verifiability.classify (V0).
    assert classify(_task_for(n.acceptance)) is Verifiability.V0


def test_acceptance_run_defauts() -> None:
    contracts = build_node_contracts(_blueprint([{"run": "true"}]))
    run = contracts["n"].acceptance_runs[0]
    assert run.expect_exit == 0
    assert run.cwd == "."
    assert run.timeout_s == 120.0
    assert run.expect_stdout_contains is None


def test_acceptance_run_avec_cwd_et_stdout_attendu() -> None:
    contracts = build_node_contracts(
        _blueprint([{"run": "pytest -q", "cwd": "sub", "expect_stdout_contains": "passed"}])
    )
    run = contracts["n"].acceptance_runs[0]
    assert run.cwd == "sub"
    assert run.expect_stdout_contains == "passed"


# ── Forme structurée « evidence » (path_exists / test) ───────────────────────


def test_acceptance_path_exists_produit_une_evidence_et_un_texte_mecanique() -> None:
    contracts = build_node_contracts(_blueprint([{"path_exists": "dist/out.json"}]))
    n = contracts["n"]
    assert len(n.acceptance_evidence) == 1
    assert n.acceptance_evidence[0].kind == "path_exists"
    assert n.acceptance_evidence[0].value == "dist/out.json"
    assert classify(_task_for(n.acceptance)) is Verifiability.V0


def test_acceptance_test_produit_une_evidence_et_un_texte_mecanique() -> None:
    contracts = build_node_contracts(_blueprint([{"test": "tests/test_x.py::test_y"}]))
    n = contracts["n"]
    assert n.acceptance_evidence[0].kind == "test"
    assert n.acceptance_evidence[0].value == "tests/test_x.py::test_y"
    assert classify(_task_for(n.acceptance)) is Verifiability.V0


def test_acceptance_mixte_texte_et_structuree() -> None:
    contracts = build_node_contracts(
        _blueprint(["ruff ne signale aucune erreur de lint", {"run": "pytest -q"}])
    )
    n = contracts["n"]
    assert len(n.acceptance) == 2
    assert len(n.acceptance_runs) == 1
    assert n.has_structured_acceptance is True


# ── Refus nommés (schéma invalide au chargement, jamais un gate muet) ────────


def test_acceptance_forme_dict_inconnue_est_refusee() -> None:
    with pytest.raises(GrimoireRuntimeError, match="n"):
        build_node_contracts(_blueprint([{"foo": "bar"}]))


def test_acceptance_deux_cles_structurees_est_refusee() -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run": "true", "path_exists": "x"}]))


def test_acceptance_run_vide_est_refuse() -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run": "   "}]))


def test_acceptance_expect_exit_non_entier_est_refuse() -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run": "true", "expect_exit": "0"}]))


def test_acceptance_timeout_negatif_est_refuse() -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run": "true", "timeout_s": -1}]))


def test_acceptance_forme_ni_chaine_ni_dict_est_refusee() -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([42]))
