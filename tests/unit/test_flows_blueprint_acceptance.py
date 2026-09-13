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

from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.blueprint_loader import build_node_contracts, hardcoded_command_warnings
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


# ── ``run_need`` : un besoin, pas une commande (issue #205, lot 2) ──────────


def test_run_need_declare_resout_depuis_project_context(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    test-runner: tox -e py312\n",
        encoding="utf-8",
    )
    contracts = build_node_contracts(_blueprint([{"run_need": "test-runner"}]), tmp_path)
    run = contracts["n"].acceptance_runs[0]
    assert run.raw == "tox -e py312"
    assert "résolu declared" in contracts["n"].acceptance[0]


def test_run_need_detecte_via_marqueur_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    contracts = build_node_contracts(_blueprint([{"run_need": "test-runner"}]), tmp_path)
    run = contracts["n"].acceptance_runs[0]
    assert run.raw == "pytest -q"
    assert "résolu detected" in contracts["n"].acceptance[0]


def test_run_need_declare_l_emporte_sur_le_marqueur_detecte(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    test-runner: tox -e py312\n",
        encoding="utf-8",
    )
    contracts = build_node_contracts(_blueprint([{"run_need": "test-runner"}]), tmp_path)
    run = contracts["n"].acceptance_runs[0]
    assert run.raw == "tox -e py312"


def test_run_need_non_resolvable_refuse_le_chargement_en_nommant_le_besoin(tmp_path: Path) -> None:
    with pytest.raises(GrimoireRuntimeError, match="migration-tool"):
        build_node_contracts(_blueprint([{"run_need": "migration-tool"}]), tmp_path)


def test_run_need_id_inconnu_du_catalogue_refuse_en_le_nommant(tmp_path: Path) -> None:
    with pytest.raises(GrimoireRuntimeError, match="inconnu du catalogue"):
        build_node_contracts(_blueprint([{"run_need": "not-a-need"}]), tmp_path)


def test_run_need_avec_args_les_ajoute_a_la_commande_resolue(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    contracts = build_node_contracts(
        _blueprint([{"run_need": "test-runner", "args": "tests/test_x.py"}]), tmp_path
    )
    run = contracts["n"].acceptance_runs[0]
    assert run.raw == "pytest -q tests/test_x.py"


def test_run_need_et_run_ensemble_est_une_forme_ambigue_refusee(tmp_path: Path) -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run": "true", "run_need": "test-runner"}]), tmp_path)


def test_run_need_vide_est_refuse(tmp_path: Path) -> None:
    with pytest.raises(GrimoireRuntimeError):
        build_node_contracts(_blueprint([{"run_need": "   "}]), tmp_path)


def test_run_need_produit_un_texte_mecanique_v0(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    contracts = build_node_contracts(_blueprint([{"run_need": "test-runner"}]), tmp_path)
    n = contracts["n"]
    assert n.has_structured_acceptance is True
    assert classify(_task_for(n.acceptance)) is Verifiability.V0


# ── Rétrocompatibilité : avertissement, jamais un refus (issue #205) ────────


def test_hardcoded_command_matching_a_resolved_need_is_a_warning_not_a_refusal(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    blueprint = _blueprint([{"run": "pytest -q"}])
    # Le chargement réussit toujours — aucune altération du contrat.
    contracts = build_node_contracts(blueprint, tmp_path)
    assert contracts["n"].acceptance_runs[0].raw == "pytest -q"
    warnings = hardcoded_command_warnings(blueprint, tmp_path)
    assert len(warnings) == 1
    assert "test-runner" in warnings[0]
    assert "n" in warnings[0]


def test_hardcoded_command_not_matching_any_need_has_no_warning(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    blueprint = _blueprint([{"run": "tox -e py312"}])
    assert hardcoded_command_warnings(blueprint, tmp_path) == ()
