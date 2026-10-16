"""V0 réservé aux commandes déclarées, et ``task verify`` producteur de preuve (issue #772)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.evidence.schemas import VerdictResult
from grimoire.evidence.service import EvidenceService
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, TaskCheck
from grimoire.missions.verifiability import Verifiability, as_dict, classify
from grimoire.missions.verify import NoDeclaredChecksError, verify_task

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")
EVIDENCE = Path("_grimoire-runtime-output/evidence")


def _task(root: Path, acceptance: tuple[str, ...], checks: tuple[TaskCheck, ...] = ()) -> MissionTask:
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission("m", origin="user")
    return ledger.create_task(mission.id, "t", acceptance=acceptance, checks=checks)


def _py(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


# ── #708 : V0 exige une commande déclarée ────────────────────────────────────


def test_tests_verts_sans_commande_declaree_n_est_pas_v0(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",))
    assert classify(task) is Verifiability.V1
    advice = dispatch_advice(task)
    assert advice["recommended_model"] == "sonnet"
    data = as_dict(task)
    assert data["raised_by"] == "no_declared_command"
    assert "checks" in data["reason"]


def test_meme_critere_avec_commande_declaree_reste_v0(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",), (TaskCheck(cmd="true"),))
    assert classify(task) is Verifiability.V0
    assert dispatch_advice(task)["recommended_model"] == "haiku"
    assert "raised_by" not in as_dict(task)


def test_sure_tests_ne_donne_jamais_haiku(tmp_path: Path) -> None:
    task = _task(tmp_path, ("sûre (tests)",))
    assert classify(task) is not Verifiability.V0
    assert dispatch_advice(task)["recommended_model"] != "haiku"


def test_checks_survivent_au_rejeu_du_ledger(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",), (TaskCheck(cmd="pytest -q", expect_exit=1),))
    replayed = MissionLedger(tmp_path / LEDGER).get_task(task.id)
    assert replayed is not None
    assert replayed.checks == (TaskCheck(cmd="pytest -q", expect_exit=1),)


@pytest.mark.parametrize("bad", [{"cmd": ""}, {"cmd": "x", "expect_exit": True}, {"cmd": "x", "expect_exit": "0"}])
def test_check_invalide_est_refuse(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TaskCheck.from_dict(bad)


# ── task verify : pack et verdict ────────────────────────────────────────────


def test_verify_vert_ecrit_pack_et_verdict(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert outcome.passed
    service = EvidenceService(tmp_path / EVIDENCE)
    assert service.get_pack(outcome.pack.id) is not None
    latest = service.get_latest_verdict(task.id)
    assert latest is not None and latest.verdict is VerdictResult.PASSED
    assert latest.evidence_pack_id == outcome.pack.id


def test_verify_code_de_sortie_inattendu_rend_failed(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("raise SystemExit(3)")),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert outcome.verdict.decision.reopen_task is True
    assert any("code de sortie 3, attendu 0" in c.reason for c in outcome.verdict.checks)


def test_verify_respecte_expect_exit(tmp_path: Path) -> None:
    task = _task(tmp_path, ("échoue comme prévu",), (TaskCheck(cmd=_py("raise SystemExit(3)"), expect_exit=3),))
    assert verify_task(task, tmp_path, tmp_path / EVIDENCE).passed


def test_verify_commande_inexecutable_est_failed_jamais_passed(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd="commande-qui-n-existe-pas-772"),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed


def test_verify_une_commande_rouge_suffit_a_echouer(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")), TaskCheck(cmd=_py("raise SystemExit(1)"))))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert len(outcome.pack.items) == 2


def test_verify_sans_commande_declaree_est_refuse_et_n_ecrit_rien(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",))
    with pytest.raises(NoDeclaredChecksError):
        verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not (tmp_path / EVIDENCE / "verdicts.jsonl").exists()


def test_cli_task_verify_codes_de_sortie(tmp_path: Path) -> None:
    ok = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")),))
    res = runner.invoke(app, ["--output", "json", "task", "verify", ok.id, "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["verdict"]["verdict"] == "passed"
    assert payload["pack"]["id"] == payload["verdict"]["evidence_pack_id"]

    ko = MissionLedger(tmp_path / LEDGER).create_task(
        ok.mission_id, "rouge", acceptance=("x",), checks=(TaskCheck(cmd=_py("raise SystemExit(1)")),)
    )
    assert runner.invoke(app, ["task", "verify", ko.id, "--project-root", str(tmp_path)]).exit_code == 1

    nu = MissionLedger(tmp_path / LEDGER).create_task(ok.mission_id, "nu", acceptance=("les tests passent",))
    assert runner.invoke(app, ["task", "verify", nu.id, "--project-root", str(tmp_path)]).exit_code == 2


def test_cli_task_add_check_declare_la_commande(tmp_path: Path) -> None:
    res = runner.invoke(
        app,
        ["--output", "json", "task", "add", "t", "-a", "les tests passent", "--check", "true", "--project-root", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["id"]
    task = MissionLedger(tmp_path / LEDGER).get_task(task_id)
    assert task is not None and task.checks == (TaskCheck(cmd="true"),)
    assert classify(task) is Verifiability.V0
